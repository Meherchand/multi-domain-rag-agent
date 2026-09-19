"""Vector-store behaviour: domain partitioning, scoping and fan-out."""

from __future__ import annotations

import pytest
from langchain_core.documents import Document

from src.vectorstore.base import normalize_domain
from src.vectorstore.memory_store import InMemoryBackend


class TestNormalizeDomain:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Order Service", "order_service"),
            ("order-service", "order_service"),
            ("Order   Service!!", "order_service"),
            ("__order__service__", "order_service"),
            ("ORDER.SERVICE", "order_service"),
        ],
    )
    def test_folds_to_a_safe_identifier(self, raw, expected):
        assert normalize_domain(raw) == expected


class TestInMemoryBackend:
    def test_indexing_makes_a_domain_available(self, store):
        assert set(store.loaded_domains()) == {"order_service", "payment_service"}
        assert store.has_collection("order_service")
        assert not store.has_collection("nonexistent")

    def test_domains_are_independent(self, store, embeddings):
        store.drop_collection("order_service")
        assert store.loaded_domains() == ["payment_service"]
        assert store.retrieve("authorisation hold", domains=["payment_service"], k=2)

    def test_scoped_retrieval_stays_within_the_domain(self, store):
        docs = store.retrieve("idempotency key header", domains=["order_service"], k=5)
        assert docs
        assert {d.metadata["retrieved_from"] for d in docs} == {"order_service"}

    def test_unscoped_retrieval_fans_out(self, store):
        docs = store.retrieve("service", domains=None, k=5)
        assert {d.metadata["retrieved_from"] for d in docs} == {"order_service", "payment_service"}

    def test_hits_are_tagged_with_their_origin(self, store):
        docs = store.retrieve("capture settles the hold", domains=["payment_service"], k=2)
        assert all(d.metadata.get("retrieved_from") == "payment_service" for d in docs)

    def test_ranking_prefers_lexically_closer_chunks(self, store):
        docs = store.retrieve("Idempotency-Key header returns 422", domains=["order_service"], k=2)
        assert "Idempotency-Key" in docs[0].page_content

    def test_k_limits_results_per_domain(self, store):
        assert len(store.retrieve("service", domains=["order_service"], k=1)) == 1

    def test_unknown_domain_is_skipped_not_fatal(self, store):
        # One good domain and one missing: the query still answers.
        docs = store.retrieve("payment", domains=["payment_service", "does_not_exist"], k=2)
        assert docs
        assert all(d.metadata["retrieved_from"] == "payment_service" for d in docs)

    def test_no_matching_domain_raises(self, store):
        with pytest.raises(ValueError, match="No relevant documents"):
            store.retrieve("anything", domains=["does_not_exist"], k=2)

    def test_empty_query_is_rejected(self, store):
        with pytest.raises(ValueError, match="non-empty"):
            store.retrieve("", domains=["order_service"], k=2)

    def test_indexing_without_documents_is_rejected(self, embeddings):
        backend = InMemoryBackend(embeddings)
        with pytest.raises(ValueError, match="No documents"):
            backend.create_collection("empty", [])

    def test_reindexing_replaces_rather_than_appends(self, store):
        store.create_collection("order_service", [Document(page_content="replacement text only")])
        docs = store.retrieve("replacement", domains=["order_service"], k=5)
        assert len(docs) == 1

    def test_domain_info_reports_state(self, store):
        assert store.domain_info("order_service") == {
            "domain": "order_service",
            "exists": True,
            "loaded": True,
        }
        assert store.domain_info("missing")["exists"] is False
