"""Central, environment-driven configuration.

Every value that is deployment-specific — endpoints, credentials, model names,
collection names — is read from the environment here and nowhere else. Modules
import ``settings`` rather than calling ``os.environ`` directly, so the set of
knobs a fork has to care about is visible in one file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env(name)
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = _env(name)
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


@dataclass(frozen=True)
class LLMSettings:
    """Chat model configuration.

    ``provider`` selects a factory in :mod:`src.llm.factory`:

    ``openai``
        Any OpenAI-compatible chat-completions endpoint. This covers the hosted
        OpenAI API, a self-hosted vLLM or Ollama instance, or an
        enterprise proxy — they differ only in ``base_url`` and ``model``.
    ``echo``
        An offline, deterministic stub used by demo mode. It performs no network
        I/O and simply composes an answer out of the retrieved context.
    """

    provider: str = field(default_factory=lambda: _env("LLM_PROVIDER", "openai").lower())
    model: str = field(default_factory=lambda: _env("LLM_MODEL", "gpt-4o-mini"))
    base_url: str | None = field(default_factory=lambda: _env("LLM_BASE_URL") or None)
    api_key: str = field(default_factory=lambda: _env("LLM_API_KEY"))
    temperature: float = field(default_factory=lambda: _env_float("LLM_TEMPERATURE", 0.2))
    top_p: float = field(default_factory=lambda: _env_float("LLM_TOP_P", 0.9))
    max_tokens: int = field(default_factory=lambda: _env_int("LLM_MAX_TOKENS", 1500))
    timeout: int = field(default_factory=lambda: _env_int("LLM_TIMEOUT", 120))


@dataclass(frozen=True)
class EmbeddingSettings:
    """Embedding model configuration.

    ``provider`` selects a factory in :mod:`src.embeddings.factory`:

    ``openai``
        Any OpenAI-compatible ``/embeddings`` endpoint.
    ``hashing``
        An offline, deterministic hashing embedder. It is not semantically
        meaningful, but it is stable and dependency-free, which makes the demo
        and the test suite runnable without any external service.
    """

    provider: str = field(default_factory=lambda: _env("EMBEDDING_PROVIDER", "openai").lower())
    model: str = field(default_factory=lambda: _env("EMBEDDING_MODEL", "text-embedding-3-small"))
    base_url: str | None = field(default_factory=lambda: _env("EMBEDDING_BASE_URL") or None)
    api_key: str = field(default_factory=lambda: _env("EMBEDDING_API_KEY"))
    dimensions: int = field(default_factory=lambda: _env_int("EMBEDDING_DIMENSIONS", 384))
    batch_size: int = field(default_factory=lambda: _env_int("EMBEDDING_BATCH_SIZE", 8))
    max_chars: int = field(default_factory=lambda: _env_int("EMBEDDING_MAX_CHARS", 6000))
    timeout: int = field(default_factory=lambda: _env_int("EMBEDDING_TIMEOUT", 120))


@dataclass(frozen=True)
class VectorStoreSettings:
    """Vector store configuration.

    ``backend`` selects a factory in :mod:`src.vectorstore.factory`:

    ``milvus``
        A Milvus / Zilliz deployment reached over ``uri``.
    ``memory``
        A process-local brute-force cosine index. Adequate for the demo corpus
        and for tests; not intended for large corpora.
    """

    backend: str = field(default_factory=lambda: _env("VECTOR_STORE", "milvus").lower())
    uri: str = field(default_factory=lambda: _env("VECTOR_STORE_URI"))
    user: str = field(default_factory=lambda: _env("VECTOR_STORE_USER"))
    password: str = field(default_factory=lambda: _env("VECTOR_STORE_PASSWORD"))
    database: str = field(default_factory=lambda: _env("VECTOR_STORE_DB", "default"))
    collection_prefix: str = field(default_factory=lambda: _env("VECTOR_COLLECTION_PREFIX", "kb_"))


@dataclass(frozen=True)
class RetrievalSettings:
    chunk_size: int = field(default_factory=lambda: _env_int("CHUNK_SIZE", 900))
    chunk_overlap: int = field(default_factory=lambda: _env_int("CHUNK_OVERLAP", 120))
    top_k: int = field(default_factory=lambda: _env_int("RETRIEVAL_TOP_K", 5))
    max_query_chars: int = field(default_factory=lambda: _env_int("MAX_QUERY_CHARS", 1000))


@dataclass(frozen=True)
class ApiSettings:
    """Settings for the FastAPI service and the clients that call it."""

    base_url: str = field(default_factory=lambda: _env("API_BASE_URL", "http://localhost:8000"))
    api_key: str = field(default_factory=lambda: _env("API_KEY"))
    model_id: str = field(default_factory=lambda: _env("API_MODEL_ID", "multi-domain-rag"))
    environment: str = field(default_factory=lambda: _env("ENVIRONMENT", "development"))
    cors_origins: list[str] = field(
        default_factory=lambda: [
            o.strip() for o in _env("CORS_ALLOW_ORIGINS", "http://localhost:8502").split(",") if o.strip()
        ]
    )
    public_base_url: str = field(
        default_factory=lambda: _env("PUBLIC_BASE_URL", "http://localhost:8502").rstrip("/")
    )


@dataclass(frozen=True)
class DatabaseSettings:
    """Postgres used by the Chainlit UI for chat history and share links."""

    host: str = field(default_factory=lambda: _env("DB_HOST", "localhost"))
    port: int = field(default_factory=lambda: _env_int("DB_PORT", 5432))
    name: str = field(default_factory=lambda: _env("DB_NAME", "ragagent"))
    user: str = field(default_factory=lambda: _env("DB_USERNAME", "ragagent"))
    password: str = field(default_factory=lambda: _env("DB_PASSWORD", ""))
    enabled: bool = field(default_factory=lambda: _env_bool("CHAT_PERSISTENCE_ENABLED", False))

    @property
    def async_dsn(self) -> str:
        return f"postgresql+asyncpg://{self.user}:{self.password}@{self.host}:{self.port}/{self.name}"


@dataclass(frozen=True)
class Settings:
    """Top-level settings object. Import the module-level ``settings`` instance."""

    demo_mode: bool = field(default_factory=lambda: _env_bool("DEMO_MODE", False))
    data_dir: Path = field(default_factory=lambda: Path(_env("DATA_DIR", "data")))
    init_mode: str = field(default_factory=lambda: _env("INIT_MODE", "auto").lower())
    pipeline: str = field(default_factory=lambda: _env("RAG_PIPELINE", "simple_rag"))
    repo_ingest_root: str | None = field(default_factory=lambda: _env("REPO_INGEST_ROOT") or None)

    llm: LLMSettings = field(default_factory=LLMSettings)
    embeddings: EmbeddingSettings = field(default_factory=EmbeddingSettings)
    vector_store: VectorStoreSettings = field(default_factory=VectorStoreSettings)
    retrieval: RetrievalSettings = field(default_factory=RetrievalSettings)
    api: ApiSettings = field(default_factory=ApiSettings)
    database: DatabaseSettings = field(default_factory=DatabaseSettings)

    def resolved(self) -> Settings:
        """Apply demo-mode overrides.

        Demo mode exists so the repository can be cloned and run with no
        external dependency at all. It swaps in the three offline
        implementations and leaves everything else alone.
        """
        if not self.demo_mode:
            return self

        from dataclasses import replace

        return replace(
            self,
            llm=replace(self.llm, provider="echo"),
            embeddings=replace(self.embeddings, provider="hashing"),
            vector_store=replace(self.vector_store, backend="memory"),
        )


settings = Settings().resolved()
