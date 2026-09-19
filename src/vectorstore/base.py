"""Vector-store backend interface.

The system keeps **one collection per knowledge domain** rather than a single
collection with a domain filter. That is the central design decision of the
retrieval layer, and it is what the interface below encodes.

Why per-domain collections:

* a query can be scoped to a subset of domains without a metadata filter, which
  most vector databases execute far more cheaply than a post-filter;
* domains can be re-indexed, dropped, or added independently, so refreshing one
  corpus does not touch the others;
* a domain that fails to index does not poison the rest of the index.

The cost is fan-out: an unscoped query hits every collection and merges the
results. ``retrieve`` therefore takes an explicit ``domains`` argument, and the
UIs push users toward scoping.
"""

from __future__ import annotations

import abc
import re

from langchain_core.documents import Document


def normalize_domain(name: str) -> str:
    """Fold an arbitrary folder or display name into a safe collection suffix."""
    normalized = re.sub(r"[^a-zA-Z0-9_]", "_", name.lower())
    return re.sub(r"_+", "_", normalized).strip("_")


class VectorStoreBackend(abc.ABC):
    """A pluggable, domain-partitioned vector store.

    Implementations are registered in :mod:`src.vectorstore.factory` and chosen
    with the ``VECTOR_STORE`` environment variable.
    """

    @abc.abstractmethod
    def create_collection(self, domain: str, documents: list[Document]) -> bool:
        """Create (replacing any existing) the collection for ``domain``."""

    @abc.abstractmethod
    def has_collection(self, domain: str) -> bool:
        """Whether a collection for ``domain`` exists in the backing store."""

    @abc.abstractmethod
    def drop_collection(self, domain: str) -> None:
        """Delete the collection for ``domain``."""

    @abc.abstractmethod
    def list_domains(self) -> list[str]:
        """Domains that exist in the backing store, indexed or not."""

    @abc.abstractmethod
    def loaded_domains(self) -> list[str]:
        """Domains currently loaded into this process and ready to query."""

    @abc.abstractmethod
    def load_domain(self, domain: str) -> bool:
        """Attach to an existing collection. Returns False if there is none."""

    @abc.abstractmethod
    def similarity_search(self, query: str, domain: str, k: int) -> list[Document]:
        """Top-``k`` chunks for ``query`` within a single domain."""

    # -- shared behaviour ---------------------------------------------------

    def retrieve(
        self,
        query: str,
        domains: list[str] | None = None,
        k: int = 5,
    ) -> list[Document]:
        """Fan out across domains, tag each hit with its origin, and merge.

        A failure in one domain is logged and skipped rather than failing the
        whole query — a single unavailable collection should degrade the answer,
        not break the request.
        """
        import logging

        logger = logging.getLogger(__name__)

        if not query or not isinstance(query, str):
            raise ValueError("Query must be a non-empty string")

        targets = [normalize_domain(d) for d in domains] if domains else self.loaded_domains()
        if not targets:
            raise ValueError("No knowledge domains are available for retrieval")

        sanitized = query.strip()[:1000]
        results: list[Document] = []

        for domain in targets:
            try:
                docs = self.similarity_search(sanitized, domain, k)
                for doc in docs:
                    doc.metadata["retrieved_from"] = domain
                results.extend(docs)
                logger.info("Retrieved %d chunks from domain '%s'", len(docs), domain)
            except Exception as exc:
                logger.error("Retrieval failed for domain '%s': %s", domain, type(exc).__name__)
                continue

        if not results:
            raise ValueError("No relevant documents found")
        return results

    def domain_info(self, domain: str) -> dict[str, object]:
        normalized = normalize_domain(domain)
        return {
            "domain": normalized,
            "exists": self.has_collection(normalized),
            "loaded": normalized in self.loaded_domains(),
        }
