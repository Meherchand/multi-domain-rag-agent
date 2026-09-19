"""Embedding-model factory.

Extension point: register a callable under a provider name and it becomes
selectable via ``EMBEDDING_PROVIDER``. See ``docs/extending.md``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from langchain_core.embeddings import Embeddings

from src.config.settings import EmbeddingSettings, settings

logger = logging.getLogger(__name__)

EmbeddingBuilder = Callable[[EmbeddingSettings], Embeddings]

_REGISTRY: dict[str, EmbeddingBuilder] = {}


def register_embeddings(name: str, builder: EmbeddingBuilder) -> None:
    _REGISTRY[name.lower()] = builder


def available_providers() -> list[str]:
    return sorted(_REGISTRY)


def _embeddings_url(base_url: str | None) -> str:
    """Accept either a base URL or a full endpoint, and normalise to the latter."""
    if not base_url:
        return "https://api.openai.com/v1/embeddings"
    trimmed = base_url.rstrip("/")
    if trimmed.endswith("/embeddings"):
        return trimmed
    return f"{trimmed}/embeddings"


def _build_openai(cfg: EmbeddingSettings) -> Embeddings:
    from src.embeddings.remote import RemoteEmbeddings

    return RemoteEmbeddings(
        endpoint=_embeddings_url(cfg.base_url),
        model=cfg.model,
        api_key_env="EMBEDDING_API_KEY",
        batch_size=cfg.batch_size,
        max_chars=cfg.max_chars,
        timeout=cfg.timeout,
    )


def _build_hashing(cfg: EmbeddingSettings) -> Embeddings:
    from src.embeddings.hashing import HashingEmbeddings

    return HashingEmbeddings(dimensions=cfg.dimensions, max_chars=cfg.max_chars)


register_embeddings("openai", _build_openai)
register_embeddings("hashing", _build_hashing)


def get_embeddings(cfg: EmbeddingSettings | None = None) -> Embeddings:
    cfg = cfg or settings.embeddings
    builder = _REGISTRY.get(cfg.provider)
    if builder is None:
        raise ValueError(
            f"Unknown EMBEDDING_PROVIDER '{cfg.provider}'. Available: {', '.join(available_providers())}"
        )
    logger.info("Using embedding provider '%s' (model=%s)", cfg.provider, cfg.model)
    return builder(cfg)
