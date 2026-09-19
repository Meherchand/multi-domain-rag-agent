"""Configuration: env parsing, demo-mode overrides, and the domain catalog."""

from __future__ import annotations

import json

import pytest

from src.config.domains import DomainCatalog, DomainMeta, load_catalog
from src.config.settings import Settings


class TestSettings:
    def test_demo_mode_forces_the_offline_providers(self, monkeypatch):
        monkeypatch.setenv("DEMO_MODE", "true")
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.setenv("VECTOR_STORE", "milvus")
        resolved = Settings().resolved()
        assert resolved.llm.provider == "echo"
        assert resolved.embeddings.provider == "hashing"
        assert resolved.vector_store.backend == "memory"

    def test_without_demo_mode_the_configured_providers_win(self, monkeypatch):
        monkeypatch.setenv("DEMO_MODE", "false")
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.setenv("VECTOR_STORE", "milvus")
        resolved = Settings().resolved()
        assert resolved.llm.provider == "openai"
        assert resolved.vector_store.backend == "milvus"

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("true", True),
            ("True", True),
            ("1", True),
            ("yes", True),
            ("on", True),
            ("false", False),
            ("0", False),
            ("", False),
            ("nonsense", False),
        ],
    )
    def test_boolean_parsing(self, monkeypatch, value, expected):
        monkeypatch.setenv("DEMO_MODE", value)
        assert Settings().demo_mode is expected

    def test_a_malformed_integer_falls_back_to_the_default(self, monkeypatch):
        monkeypatch.setenv("RETRIEVAL_TOP_K", "not-a-number")
        assert Settings().retrieval.top_k == 5

    def test_cors_origins_are_split_and_trimmed(self, monkeypatch):
        monkeypatch.setenv("CORS_ALLOW_ORIGINS", "http://a.test , http://b.test")
        assert Settings().api.cors_origins == ["http://a.test", "http://b.test"]

    def test_no_endpoint_is_hardcoded_to_a_default(self, monkeypatch):
        """Endpoints must come from the environment, never from source."""
        for var in ("LLM_BASE_URL", "EMBEDDING_BASE_URL", "VECTOR_STORE_URI"):
            monkeypatch.delenv(var, raising=False)
        cfg = Settings()
        assert cfg.llm.base_url is None
        assert cfg.embeddings.base_url is None
        assert cfg.vector_store.uri == ""

    def test_the_database_dsn_is_assembled_from_parts(self, monkeypatch):
        monkeypatch.setenv("DB_HOST", "db.internal.test")
        monkeypatch.setenv("DB_PORT", "6543")
        monkeypatch.setenv("DB_NAME", "app")
        monkeypatch.setenv("DB_USERNAME", "user")
        monkeypatch.setenv("DB_PASSWORD", "pw")
        dsn = Settings().database.async_dsn
        # Assembled from parts rather than compared to a literal DSN, so the
        # repository never contains a credential-shaped connection string.
        assert dsn.startswith("postgresql+asyncpg://")
        assert "user" in dsn and "pw" in dsn
        assert dsn.endswith("@db.internal.test:6543/app")


class TestDomainCatalog:
    def test_labels_fall_back_to_the_folder_name(self):
        assert DomainCatalog().label("order_service") == "Order Service"

    def test_a_configured_label_wins(self):
        catalog = DomainCatalog(domains={"x": DomainMeta(key="x", label="Custom Label")})
        assert catalog.label("x") == "Custom Label"

    def test_grouping_places_known_domains(self):
        catalog = DomainCatalog(groups=[{"name": "Commerce", "domains": ["a", "b"]}])
        assert catalog.grouped(["a", "b"]) == [{"name": "Commerce", "domains": ["a", "b"]}]

    def test_ungrouped_domains_still_appear(self):
        catalog = DomainCatalog(groups=[{"name": "Commerce", "domains": ["a"]}])
        grouped = catalog.grouped(["a", "z"])
        assert grouped[-1] == {"name": "Other", "domains": ["z"]}

    def test_groups_with_no_available_members_are_dropped(self):
        catalog = DomainCatalog(groups=[{"name": "Empty", "domains": ["gone"]}])
        assert catalog.grouped(["a"]) == [{"name": "Knowledge domains", "domains": ["a"]}]

    def test_a_missing_metadata_file_is_not_fatal(self, tmp_path):
        assert load_catalog(tmp_path).domains == {}

    def test_malformed_metadata_is_not_fatal(self, tmp_path):
        (tmp_path / "domains.json").write_text("{ not json", encoding="utf-8")
        assert load_catalog(tmp_path).domains == {}

    def test_metadata_is_loaded_from_disk(self, tmp_path):
        (tmp_path / "domains.json").write_text(
            json.dumps(
                {
                    "groups": [{"name": "G", "domains": ["d"]}],
                    "domains": {"d": {"label": "D", "description": "desc", "faqs": ["q1"]}},
                }
            ),
            encoding="utf-8",
        )
        catalog = load_catalog(tmp_path)
        assert catalog.label("d") == "D"
        assert catalog.faqs("d") == ["q1"]
        assert catalog.description("d") == "desc"

    def test_the_shipped_catalog_matches_the_shipped_corpus(self):
        """Guards against the demo corpus and its metadata drifting apart."""
        from src.config.settings import PROJECT_ROOT
        from src.vectorstore.factory import discover_domains

        data_dir = PROJECT_ROOT / "data"
        catalog = load_catalog(data_dir)
        on_disk = set(discover_domains(data_dir))
        assert set(catalog.domains) == on_disk
        assert all(catalog.faqs(d) for d in on_disk)
