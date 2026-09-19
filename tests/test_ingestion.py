"""Corpus discovery, loading and chunking."""

from __future__ import annotations

import pytest

from src.ingestion.document_processor import DocumentProcessor
from src.vectorstore.factory import discover_domains


class TestDiscovery:
    def test_each_subdirectory_is_a_domain(self, corpus_dir):
        assert discover_domains(corpus_dir) == ["alpha_domain", "beta_domain"]

    def test_hidden_directories_are_ignored(self, corpus_dir):
        assert ".hidden" not in discover_domains(corpus_dir)

    def test_a_missing_corpus_is_not_fatal(self, tmp_path):
        assert discover_domains(tmp_path / "nope") == []


class TestDocumentProcessor:
    def test_loads_every_domain(self, corpus_dir):
        corpus = DocumentProcessor(chunk_size=200, chunk_overlap=20).load_domains(corpus_dir)
        assert set(corpus) == {"alpha_domain", "beta_domain"}

    def test_can_load_a_subset(self, corpus_dir):
        corpus = DocumentProcessor().load_domains(corpus_dir, only=["alpha_domain"])
        assert set(corpus) == {"alpha_domain"}

    def test_chunks_carry_provenance(self, corpus_dir):
        corpus = DocumentProcessor(chunk_size=200, chunk_overlap=20).load_domains(corpus_dir)
        metadata = corpus["alpha_domain"][0].metadata
        assert metadata["domain"] == "alpha_domain"
        assert metadata["source_file"] == "guide.md"
        assert metadata["file_type"] == ".md"

    def test_chunking_respects_the_configured_size(self, corpus_dir):
        corpus = DocumentProcessor(chunk_size=200, chunk_overlap=20).load_domains(corpus_dir)
        chunks = corpus["alpha_domain"]
        assert len(chunks) > 1
        assert all(len(c.page_content) <= 260 for c in chunks)

    def test_a_larger_chunk_size_yields_fewer_chunks(self, corpus_dir):
        small = DocumentProcessor(chunk_size=200, chunk_overlap=0).load_domains(corpus_dir)
        large = DocumentProcessor(chunk_size=1500, chunk_overlap=0).load_domains(corpus_dir)
        assert len(large["alpha_domain"]) < len(small["alpha_domain"])

    def test_both_markdown_and_text_are_loaded(self, corpus_dir):
        corpus = DocumentProcessor().load_domains(corpus_dir)
        assert corpus["beta_domain"][0].metadata["file_type"] == ".txt"

    def test_unsupported_files_are_skipped(self, corpus_dir):
        (corpus_dir / "alpha_domain" / "notes.xyz").write_text("ignored", encoding="utf-8")
        corpus = DocumentProcessor().load_domains(corpus_dir)
        assert all(c.metadata["file_type"] != ".xyz" for c in corpus["alpha_domain"])

    def test_an_empty_domain_is_skipped_not_fatal(self, corpus_dir):
        (corpus_dir / "empty_domain").mkdir()
        corpus = DocumentProcessor().load_domains(corpus_dir)
        assert "empty_domain" not in corpus
        assert "alpha_domain" in corpus

    def test_a_missing_corpus_raises(self, tmp_path):
        with pytest.raises(ValueError, match="not found"):
            DocumentProcessor().load_domains(tmp_path / "nope")

    def test_a_bad_url_is_rejected(self):
        with pytest.raises(ValueError, match="Invalid URL"):
            DocumentProcessor().load_url("ftp://example.com/doc")


class TestEndToEndIndexing:
    def test_a_corpus_on_disk_becomes_queryable(self, corpus_dir, embeddings):
        """Discovery → load → chunk → index → retrieve, with no external service."""
        from src.vectorstore.memory_store import InMemoryBackend

        store = InMemoryBackend(embeddings)
        corpus = DocumentProcessor(chunk_size=200, chunk_overlap=20).load_domains(corpus_dir)
        for domain, chunks in corpus.items():
            store.create_collection(domain, chunks)

        assert set(store.loaded_domains()) == {"alpha_domain", "beta_domain"}
        docs = store.retrieve("customs paperwork", domains=["beta_domain"], k=2)
        assert "customs" in docs[0].page_content
