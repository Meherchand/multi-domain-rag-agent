"""In-process vector store: brute-force cosine similarity over Python lists.

Chosen over a real vector database for demo mode and tests because it has no
install step, no daemon, and no network. It is O(n) per query and holds every
vector in RAM, so it is appropriate for the demo corpus and small local
experiments — not for a real corpus. Switch ``VECTOR_STORE`` to ``milvus`` for
anything larger.
"""

from __future__ import annotations

import logging

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings

from src.vectorstore.base import VectorStoreBackend, normalize_domain

logger = logging.getLogger(__name__)


def _cosine(a: list[float], b: list[float]) -> float:
    # Both sides are unit-normalised by the embedder, so the dot product is the
    # cosine. Guard anyway in case a custom embedder is not normalised.
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class InMemoryBackend(VectorStoreBackend):
    def __init__(self, embeddings: Embeddings):
        self.embeddings = embeddings
        self._collections: dict[str, list[tuple[list[float], Document]]] = {}

    def create_collection(self, domain: str, documents: list[Document]) -> bool:
        if not documents:
            raise ValueError("No documents provided")
        normalized = normalize_domain(domain)
        try:
            vectors = self.embeddings.embed_documents([d.page_content for d in documents])
            self._collections[normalized] = list(zip(vectors, documents, strict=True))
            logger.info("Indexed domain '%s': %d chunks", normalized, len(documents))
            return True
        except Exception as exc:
            logger.error("Indexing failed for domain '%s': %s", normalized, type(exc).__name__)
            return False

    def has_collection(self, domain: str) -> bool:
        return normalize_domain(domain) in self._collections

    def drop_collection(self, domain: str) -> None:
        self._collections.pop(normalize_domain(domain), None)

    def list_domains(self) -> list[str]:
        return sorted(self._collections)

    def loaded_domains(self) -> list[str]:
        return sorted(self._collections)

    def load_domain(self, domain: str) -> bool:
        # Nothing to attach to: this backend is its own storage.
        return self.has_collection(domain)

    def similarity_search(self, query: str, domain: str, k: int) -> list[Document]:
        normalized = normalize_domain(domain)
        entries = self._collections.get(normalized)
        if not entries:
            return []
        query_vec = self.embeddings.embed_query(query)
        ranked = sorted(entries, key=lambda e: _cosine(query_vec, e[0]), reverse=True)
        return [doc for _, doc in ranked[:k]]
