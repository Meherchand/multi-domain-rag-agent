"""Vector-store factory and the domain-index bootstrap.

``build_knowledge_base`` is the single place that decides, at start-up, whether
to attach to already-indexed domains or to build the index from the corpus on
disk. ``INIT_MODE`` controls it:

``retrieve``
    Attach only. Fastest start-up; the process serves whatever is already
    indexed and never reads the corpus.
``auto`` (default)
    Attach to what exists, then index only the domains that are missing.
``index``
    Re-index every domain from disk, dropping what was there.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable

from langchain_core.embeddings import Embeddings

from src.config.settings import Settings, settings
from src.vectorstore.base import VectorStoreBackend, normalize_domain

logger = logging.getLogger(__name__)

BackendBuilder = Callable[[Embeddings, Settings], VectorStoreBackend]

_REGISTRY: dict[str, BackendBuilder] = {}


def register_backend(name: str, builder: BackendBuilder) -> None:
    _REGISTRY[name.lower()] = builder


def available_backends() -> list[str]:
    return sorted(_REGISTRY)


def _build_milvus(embeddings: Embeddings, cfg: Settings) -> VectorStoreBackend:
    from src.vectorstore.milvus_store import MilvusBackend

    return MilvusBackend(embeddings, cfg.vector_store)


def _build_memory(embeddings: Embeddings, cfg: Settings) -> VectorStoreBackend:
    from src.vectorstore.memory_store import InMemoryBackend

    return InMemoryBackend(embeddings)


register_backend("milvus", _build_milvus)
register_backend("memory", _build_memory)


def get_vector_store(embeddings: Embeddings | None = None, cfg: Settings | None = None) -> VectorStoreBackend:
    cfg = cfg or settings
    if embeddings is None:
        from src.embeddings.factory import get_embeddings

        embeddings = get_embeddings(cfg.embeddings)

    builder = _REGISTRY.get(cfg.vector_store.backend)
    if builder is None:
        raise ValueError(
            f"Unknown VECTOR_STORE '{cfg.vector_store.backend}'. Available: {', '.join(available_backends())}"
        )
    logger.info("Using vector store backend '%s'", cfg.vector_store.backend)
    return builder(embeddings, cfg)


def discover_domains(data_dir) -> list[str]:
    """Every immediate subdirectory of the corpus root is a knowledge domain."""
    from pathlib import Path

    root = Path(data_dir)
    if not root.is_dir():
        return []
    return sorted(
        normalize_domain(p.name) for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")
    )


def build_knowledge_base(
    cfg: Settings | None = None,
    init_mode: str | None = None,
    embeddings: Embeddings | None = None,
) -> VectorStoreBackend:
    """Create the backend and bring its domains online per ``INIT_MODE``."""
    cfg = cfg or settings
    mode = (init_mode or cfg.init_mode).lower()
    store = get_vector_store(embeddings=embeddings, cfg=cfg)

    for domain in store.list_domains():
        store.load_domain(domain)

    if mode == "retrieve":
        logger.info("Start-up mode 'retrieve': attached to %d domains", len(store.loaded_domains()))
        return store

    on_disk = discover_domains(cfg.data_dir)
    if not on_disk:
        logger.warning("No knowledge domains found under '%s'", cfg.data_dir)
        return store

    targets = on_disk if mode == "index" else [d for d in on_disk if d not in store.loaded_domains()]
    if not targets:
        logger.info("All %d domains already indexed", len(on_disk))
        return store

    index_domains(store, targets, cfg)
    return store


def index_domains(store: VectorStoreBackend, domains: list[str], cfg: Settings | None = None) -> None:
    """Load, chunk and index the named domains from the corpus on disk."""
    from src.ingestion.document_processor import DocumentProcessor

    cfg = cfg or settings
    logger.info("Indexing %d domain(s): %s", len(domains), ", ".join(domains))

    processor = DocumentProcessor(
        chunk_size=cfg.retrieval.chunk_size, chunk_overlap=cfg.retrieval.chunk_overlap
    )
    corpus = processor.load_domains(cfg.data_dir, only=domains)

    for domain in domains:
        chunks = corpus.get(domain)
        if not chunks:
            logger.warning("No documents found for domain '%s'", domain)
            continue
        try:
            store.create_collection(domain, chunks)
        except Exception as exc:
            logger.error("Failed to index domain '%s': %s", domain, type(exc).__name__)


def env_init_mode() -> str:
    return os.environ.get("INIT_MODE", "auto").lower()
