"""Shared fixtures.

Every test runs fully offline. The environment is forced into demo mode before
``src.config.settings`` is imported anywhere, so no test can accidentally reach
a real provider even if one is configured in the developer's shell.
"""

from __future__ import annotations

import os

# Must run before any src.* import — settings are read at module import time.
os.environ.update(
    {
        "DEMO_MODE": "true",
        "LLM_PROVIDER": "echo",
        "EMBEDDING_PROVIDER": "hashing",
        "VECTOR_STORE": "memory",
        "API_KEY": "test-key-not-a-secret",
        "CHAT_PERSISTENCE_ENABLED": "false",
        "INIT_MODE": "retrieve",
        "RETRIEVAL_TOP_K": "3",
    }
)

from pathlib import Path  # noqa: E402

import pytest  # noqa: E402
from langchain_core.documents import Document  # noqa: E402


@pytest.fixture
def documents() -> list[Document]:
    """A small synthetic corpus with clearly separable topics."""
    return [
        Document(
            page_content=(
                "The Order Service owns the order lifecycle. Orders move from DRAFT to "
                "PENDING_PAYMENT once inventory is reserved, and inventory reservations "
                "expire after thirty minutes."
            ),
            metadata={"domain": "order_service", "source_file": "overview.md"},
        ),
        Document(
            page_content=(
                "Every write endpoint on the Order Service requires an Idempotency-Key "
                "header. Reusing a key with a different body returns 422."
            ),
            metadata={"domain": "order_service", "source_file": "api.md"},
        ),
        Document(
            page_content=(
                "The Payment Service authorises and captures money. An authorisation "
                "places a hold on funds; a capture settles it. Holds expire after seven days."
            ),
            metadata={"domain": "payment_service", "source_file": "overview.md"},
        ),
        Document(
            page_content=(
                "A declined card is never retried automatically. Only transport errors "
                "and retryable provider codes are retried, with exponential backoff."
            ),
            metadata={"domain": "payment_service", "source_file": "troubleshooting.md"},
        ),
    ]


@pytest.fixture
def embeddings():
    from src.embeddings.hashing import HashingEmbeddings

    return HashingEmbeddings(dimensions=128)


@pytest.fixture
def store(embeddings, documents):
    """An in-memory store with two indexed domains."""
    from src.vectorstore.memory_store import InMemoryBackend

    backend = InMemoryBackend(embeddings)
    backend.create_collection(
        "order_service", [d for d in documents if d.metadata["domain"] == "order_service"]
    )
    backend.create_collection(
        "payment_service", [d for d in documents if d.metadata["domain"] == "payment_service"]
    )
    return backend


@pytest.fixture
def llm():
    from src.llm.echo import EchoChatModel

    return EchoChatModel()


@pytest.fixture
def engine(store, llm):
    from src.rag.engine import RAGEngine

    return RAGEngine(store=store, llm=llm)


@pytest.fixture
def corpus_dir(tmp_path) -> Path:
    """A throwaway two-domain corpus on disk."""
    (tmp_path / "alpha_domain").mkdir()
    (tmp_path / "alpha_domain" / "guide.md").write_text(
        "# Alpha\n\n" + ("Alpha domain covers widget assembly and calibration. " * 30),
        encoding="utf-8",
    )
    (tmp_path / "beta_domain").mkdir()
    (tmp_path / "beta_domain" / "guide.txt").write_text(
        "Beta domain covers shipping logistics and customs paperwork. " * 30,
        encoding="utf-8",
    )
    (tmp_path / ".hidden").mkdir()
    return tmp_path
