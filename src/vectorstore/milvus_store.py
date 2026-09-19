"""Milvus-backed vector store, one collection per knowledge domain.

Connection handling note: this uses a long-lived ``MilvusClient`` rather than
pymilvus's global ORM ``connections.connect(alias="default")``. The global alias
is process-wide mutable state, and the background indexing thread can tear it
down underneath an in-flight query, which surfaces as an intermittent
``ConnectionNotExistException`` on an unrelated request. A dedicated client,
guarded by a lock and rebuilt on failure, removes that class of bug.

Admin calls go through :meth:`_client_call`, which reconnects and retries once —
enough to ride out an idle-timeout disconnect without papering over a genuinely
unreachable server.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings

from src.config.settings import VectorStoreSettings
from src.vectorstore.base import VectorStoreBackend, normalize_domain

logger = logging.getLogger(__name__)


class MilvusBackend(VectorStoreBackend):
    def __init__(self, embeddings: Embeddings, cfg: VectorStoreSettings):
        self.embeddings = embeddings
        self.cfg = cfg
        self.prefix = cfg.collection_prefix
        self.connection_args = self._build_connection_args(cfg)
        self._stores: dict[str, Any] = {}
        self._conn_lock = threading.Lock()
        self._client: Any | None = None
        self._get_client()

    # -- connection ---------------------------------------------------------

    @staticmethod
    def _build_connection_args(cfg: VectorStoreSettings) -> dict[str, Any]:
        if not cfg.uri:
            raise ValueError(
                "VECTOR_STORE_URI is not set. Set it, or run with DEMO_MODE=true "
                "(or VECTOR_STORE=memory) to use the in-process store."
            )
        args: dict[str, Any] = {
            "uri": cfg.uri,
            "secure": cfg.uri.startswith("https"),
            "db_name": cfg.database,
        }
        # Credentials are optional: a local Milvus started from the bundled
        # compose file has authentication disabled.
        if cfg.user:
            args["user"] = cfg.user
        if cfg.password:
            args["password"] = cfg.password
        return args

    def _get_client(self):
        from pymilvus import MilvusClient

        with self._conn_lock:
            if self._client is None:
                try:
                    self._client = MilvusClient(**self.connection_args)
                    logger.info("Vector store client established")
                except Exception as exc:
                    logger.error("Vector store connection failed: %s", type(exc).__name__)
                    raise
            return self._client

    def _reset_client(self) -> None:
        with self._conn_lock:
            if self._client is not None:
                try:
                    self._client.close()
                except Exception:
                    pass
                self._client = None

    def _client_call(self, method: str, *args, **kwargs):
        try:
            return getattr(self._get_client(), method)(*args, **kwargs)
        except Exception as exc:
            logger.warning(
                "Vector store call '%s' failed (%s); reconnecting and retrying once",
                method,
                type(exc).__name__,
            )
            self._reset_client()
            return getattr(self._get_client(), method)(*args, **kwargs)

    def _collection_name(self, domain: str) -> str:
        return f"{self.prefix}{normalize_domain(domain)}"

    # -- VectorStoreBackend -------------------------------------------------

    def create_collection(self, domain: str, documents: list[Document]) -> bool:
        from langchain_milvus import Milvus

        if not documents:
            raise ValueError("No documents provided")

        normalized = normalize_domain(domain)
        collection = self._collection_name(normalized)
        try:
            if self.has_collection(normalized):
                self.drop_collection(normalized)
            store = Milvus.from_documents(
                documents,
                self.embeddings,
                connection_args=self.connection_args,
                collection_name=collection,
            )
            self._stores[normalized] = store
            logger.info("Indexed domain '%s': %d chunks", normalized, len(documents))
            return True
        except Exception as exc:
            logger.error("Indexing failed for domain '%s': %s", normalized, type(exc).__name__)
            return False

    def has_collection(self, domain: str) -> bool:
        try:
            return bool(self._client_call("has_collection", self._collection_name(domain)))
        except Exception:
            return False

    def drop_collection(self, domain: str) -> None:
        self._client_call("drop_collection", self._collection_name(domain))
        self._stores.pop(normalize_domain(domain), None)

    def list_domains(self) -> list[str]:
        try:
            names = self._client_call("list_collections") or []
        except Exception as exc:
            logger.error("Could not list collections: %s", type(exc).__name__)
            return []
        return sorted(n[len(self.prefix) :] for n in names if n.startswith(self.prefix))

    def loaded_domains(self) -> list[str]:
        return sorted(self._stores)

    def load_domain(self, domain: str) -> bool:
        from langchain_milvus import Milvus

        normalized = normalize_domain(domain)
        if normalized in self._stores:
            return True
        if not self.has_collection(normalized):
            return False
        try:
            self._stores[normalized] = Milvus(
                embedding_function=self.embeddings,
                connection_args=self.connection_args,
                collection_name=self._collection_name(normalized),
            )
            return True
        except Exception as exc:
            logger.error("Failed to load domain '%s': %s", normalized, type(exc).__name__)
            return False

    def similarity_search(self, query: str, domain: str, k: int) -> list[Document]:
        normalized = normalize_domain(domain)
        if normalized not in self._stores and not self.load_domain(normalized):
            return []
        retriever = self._stores[normalized].as_retriever(search_kwargs={"k": k})
        return retriever.invoke(query)
