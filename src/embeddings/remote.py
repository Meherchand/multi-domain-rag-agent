"""Embeddings over an OpenAI-compatible HTTP ``/embeddings`` endpoint.

Implemented directly against ``requests`` rather than through a provider SDK so
that any compatible server works — the hosted OpenAI API, a local vLLM or
Ollama instance, or a gateway that proxies several of them.

Behaviour worth noting:

* requests are **batched**, because embedding endpoints commonly cap the number
  of inputs per call;
* inputs are **truncated** to ``max_chars``, because a single oversized chunk
  otherwise fails the whole batch;
* transient failures (429 and 5xx) are **retried with backoff** on a pooled
  session, so a rate-limited bulk index recovers instead of aborting.
"""

from __future__ import annotations

import logging
import os

import requests
from langchain_core.embeddings import Embeddings
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)


class RemoteEmbeddings(Embeddings):
    def __init__(
        self,
        endpoint: str,
        model: str,
        api_key_env: str = "EMBEDDING_API_KEY",
        batch_size: int = 8,
        max_chars: int = 6000,
        timeout: int = 120,
    ):
        if not endpoint:
            raise ValueError(
                "No embedding endpoint configured. Set EMBEDDING_BASE_URL, or run with "
                "DEMO_MODE=true to use the offline hashing embedder."
            )
        self.endpoint = endpoint
        self.model = model
        self.api_key_env = api_key_env
        self.batch_size = batch_size
        self.max_chars = max_chars
        self.timeout = timeout
        self.session = self._create_session()

    @staticmethod
    def _create_session() -> requests.Session:
        session = requests.Session()
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["POST"],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=10, pool_maxsize=10)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    # -- Embeddings interface ----------------------------------------------

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        truncated = [t[: self.max_chars] for t in texts]
        results: list[list[float]] = []
        for start in range(0, len(truncated), self.batch_size):
            results.extend(self._embed_batch(truncated[start : start + self.batch_size]))
        return results

    def embed_query(self, text: str) -> list[float]:
        embeddings = self._embed_batch([text[: self.max_chars]])
        return embeddings[0] if embeddings else []

    # -- internals ----------------------------------------------------------

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        api_key = os.environ.get(self.api_key_env, "").strip()
        if not api_key:
            raise ValueError(f"Missing {self.api_key_env} environment variable")

        payload = {"model": self.model, "input": texts, "encoding_format": "float"}
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

        try:
            response = self.session.post(
                self.endpoint, json=payload, headers=headers, timeout=self.timeout, verify=True
            )
            response.raise_for_status()
            data = response.json()
            embeddings = [item["embedding"] for item in data.get("data", [])]

            if len(embeddings) != len(texts):
                raise ValueError(f"Expected {len(texts)} embeddings, got {len(embeddings)}")
            return embeddings

        except requests.exceptions.RequestException as exc:
            # Deliberately does not log the endpoint, the credential, or the
            # input text: embedding inputs may contain user or customer data,
            # and logs are the easiest place for either to escape.
            logger.error(
                "Embedding request to the configured embedding provider failed "
                "(model=%s, batch=%d, timeout=%ds): %s",
                self.model,
                len(texts),
                self.timeout,
                type(exc).__name__,
            )
            raise RuntimeError("Failed to generate embeddings") from exc
        except Exception as exc:
            logger.error("Embedding processing error: %s", type(exc).__name__)
            raise

    def __del__(self):
        session = getattr(self, "session", None)
        if session is not None:
            session.close()
