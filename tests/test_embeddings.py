"""Embedding providers: the offline embedder, and the remote one under failure."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from src.embeddings.factory import _embeddings_url, available_providers, get_embeddings
from src.embeddings.hashing import HashingEmbeddings
from src.embeddings.remote import RemoteEmbeddings


class TestHashingEmbeddings:
    def test_is_deterministic(self):
        embedder = HashingEmbeddings(dimensions=64)
        assert embedder.embed_query("same text") == embedder.embed_query("same text")

    def test_respects_the_configured_dimension(self):
        assert len(HashingEmbeddings(dimensions=96).embed_query("text")) == 96

    def test_vectors_are_unit_normalised(self):
        vector = HashingEmbeddings(dimensions=64).embed_query("some reasonable text here")
        assert sum(v * v for v in vector) == pytest.approx(1.0, abs=1e-9)

    def test_shared_vocabulary_scores_higher_than_disjoint(self):
        embedder = HashingEmbeddings(dimensions=256)

        def cosine(a, b):
            return sum(x * y for x, y in zip(a, b, strict=False))

        query = embedder.embed_query("inventory reservation expires")
        related = embedder.embed_query("the inventory reservation expires after thirty minutes")
        unrelated = embedder.embed_query("completely different vocabulary about penguins")
        assert cosine(query, related) > cosine(query, unrelated)

    def test_empty_text_gives_a_zero_vector(self):
        assert HashingEmbeddings(dimensions=32).embed_query("") == [0.0] * 32

    def test_batch_matches_single(self):
        embedder = HashingEmbeddings(dimensions=32)
        assert embedder.embed_documents(["a b", "c d"]) == [
            embedder.embed_query("a b"),
            embedder.embed_query("c d"),
        ]

    def test_rejects_a_degenerate_dimension(self):
        with pytest.raises(ValueError):
            HashingEmbeddings(dimensions=2)


class TestRemoteEmbeddings:
    def test_requires_an_endpoint(self):
        with pytest.raises(ValueError, match="endpoint"):
            RemoteEmbeddings(endpoint="", model="m")

    def test_requires_an_api_key(self, monkeypatch):
        monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)
        embedder = RemoteEmbeddings(endpoint="http://localhost:9999/embeddings", model="m")
        with pytest.raises(ValueError, match="EMBEDDING_API_KEY"):
            embedder.embed_query("text")

    def test_batches_requests(self, monkeypatch):
        monkeypatch.setenv("EMBEDDING_API_KEY", "test-key-not-a-secret")
        embedder = RemoteEmbeddings(endpoint="http://localhost:9999/embeddings", model="m", batch_size=2)

        response = MagicMock()
        response.json.return_value = {"data": [{"embedding": [0.1]}, {"embedding": [0.2]}]}
        response.raise_for_status.return_value = None

        with patch.object(embedder.session, "post", return_value=response) as post:
            embedder.embed_documents(["a", "b", "c", "d"])
        assert post.call_count == 2  # 4 inputs, batch size 2

    def test_truncates_oversized_inputs(self, monkeypatch):
        monkeypatch.setenv("EMBEDDING_API_KEY", "test-key-not-a-secret")
        embedder = RemoteEmbeddings(endpoint="http://localhost:9999/embeddings", model="m", max_chars=10)

        response = MagicMock()
        response.json.return_value = {"data": [{"embedding": [0.1]}]}
        response.raise_for_status.return_value = None

        with patch.object(embedder.session, "post", return_value=response) as post:
            embedder.embed_query("x" * 500)
        assert len(post.call_args.kwargs["json"]["input"][0]) == 10

    def test_transport_failure_does_not_leak_the_credential(self, monkeypatch, caplog):
        monkeypatch.setenv("EMBEDDING_API_KEY", "super-secret-value-abc123")
        embedder = RemoteEmbeddings(endpoint="http://localhost:9999/embeddings", model="m")

        with patch.object(embedder.session, "post", side_effect=requests.exceptions.ConnectTimeout("boom")):
            with caplog.at_level("ERROR"):
                with pytest.raises(RuntimeError):
                    embedder.embed_query("text")

        logged = caplog.text
        assert "super-secret" not in logged
        assert "abc123" not in logged
        assert "localhost:9999" not in logged

    def test_a_short_response_is_an_error(self, monkeypatch):
        monkeypatch.setenv("EMBEDDING_API_KEY", "test-key-not-a-secret")
        embedder = RemoteEmbeddings(endpoint="http://localhost:9999/embeddings", model="m")

        response = MagicMock()
        response.json.return_value = {"data": [{"embedding": [0.1]}]}
        response.raise_for_status.return_value = None

        with patch.object(embedder.session, "post", return_value=response):
            with pytest.raises(ValueError, match="Expected 2 embeddings"):
                embedder.embed_documents(["a", "b"])


class TestFactory:
    def test_lists_both_providers(self):
        assert set(available_providers()) == {"openai", "hashing"}

    def test_unknown_provider_is_reported_clearly(self):
        from src.config.settings import EmbeddingSettings

        cfg = EmbeddingSettings()
        object.__setattr__(cfg, "provider", "nope")
        with pytest.raises(ValueError, match="Unknown EMBEDDING_PROVIDER"):
            get_embeddings(cfg)

    @pytest.mark.parametrize(
        ("base", "expected"),
        [
            ("http://localhost:4000/v1", "http://localhost:4000/v1/embeddings"),
            ("http://localhost:4000/v1/", "http://localhost:4000/v1/embeddings"),
            ("http://localhost:4000/v1/embeddings", "http://localhost:4000/v1/embeddings"),
            ("", "https://api.openai.com/v1/embeddings"),
        ],
    )
    def test_accepts_a_base_url_or_a_full_endpoint(self, base, expected):
        assert _embeddings_url(base) == expected
