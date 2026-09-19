"""Corpus loading and chunking.

The corpus is a directory of directories:

    data/
      order_service/
        overview.md
        api.md
      payment_service/
        overview.md

Each immediate subdirectory is one **knowledge domain** and becomes one vector
collection. Files are loaded by extension (``.md``, ``.txt``, ``.pdf``), tagged
with provenance metadata, and split with a recursive character splitter.

Splitting is deliberately conservative: overlapping windows over a document
lose less at chunk boundaries than clean cuts do, at the cost of some index
size. ``CHUNK_SIZE`` / ``CHUNK_OVERLAP`` tune the trade-off.
"""

from __future__ import annotations

import logging
from pathlib import Path

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from src.config.settings import settings
from src.vectorstore.base import normalize_domain

logger = logging.getLogger(__name__)

SUPPORTED_SUFFIXES = {".md", ".txt", ".pdf"}


class DocumentProcessor:
    def __init__(self, chunk_size: int | None = None, chunk_overlap: int | None = None):
        self.chunk_size = chunk_size or settings.retrieval.chunk_size
        self.chunk_overlap = chunk_overlap or settings.retrieval.chunk_overlap
        self.splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.chunk_size, chunk_overlap=self.chunk_overlap
        )

    # -- loaders ------------------------------------------------------------

    def load_url(self, url: str) -> list[Document]:
        from langchain_community.document_loaders import WebBaseLoader

        if not url.startswith(("http://", "https://")):
            raise ValueError("Invalid URL")
        return WebBaseLoader(url).load()

    def load_text(self, path: str | Path) -> list[Document]:
        from langchain_community.document_loaders import TextLoader

        return TextLoader(str(path), encoding="utf-8").load()

    def load_pdf(self, path: str | Path) -> list[Document]:
        from langchain_community.document_loaders import PyPDFLoader

        return PyPDFLoader(str(path)).load()

    def load_markdown(self, path: str | Path) -> list[Document]:
        # TextLoader rather than a Markdown-aware loader: the latter pulls in
        # heavyweight optional dependencies, and the recursive splitter already
        # prefers heading and paragraph boundaries.
        return self.load_text(path)

    def load_file(self, path: Path) -> list[Document]:
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            return self.load_pdf(path)
        if suffix == ".md":
            return self.load_markdown(path)
        if suffix == ".txt":
            return self.load_text(path)
        raise ValueError(f"Unsupported file type: {suffix}")

    # -- chunking -----------------------------------------------------------

    def split(self, documents: list[Document]) -> list[Document]:
        return self.splitter.split_documents(documents) if documents else []

    def process_sources(self, sources: list[str]) -> list[Document]:
        """Load an ad-hoc list of URLs and/or file paths, then chunk."""
        docs: list[Document] = []
        for src in sources:
            try:
                if src.startswith(("http://", "https://")):
                    docs.extend(self.load_url(src))
                else:
                    path = Path(src)
                    if not path.is_file():
                        logger.warning("Skipping missing source: %s", path.name)
                        continue
                    docs.extend(self.load_file(path))
            except Exception as exc:
                logger.error("Failed to load a source (%s): %s", type(exc).__name__, src)
        if not docs:
            raise ValueError("No documents could be loaded from the given sources")
        return self.split(docs)

    # -- corpus -------------------------------------------------------------

    def load_domains(
        self, data_dir: str | Path = "data", only: list[str] | None = None
    ) -> dict[str, list[Document]]:
        """Load and chunk the corpus, keyed by normalised domain name."""
        root = Path(data_dir)
        if not root.is_dir():
            raise ValueError(f"Corpus directory not found: {data_dir}")

        wanted = {normalize_domain(d) for d in only} if only else None
        corpus: dict[str, list[Document]] = {}

        for folder in sorted(root.iterdir()):
            if not folder.is_dir() or folder.name.startswith("."):
                continue

            domain = normalize_domain(folder.name)
            if wanted is not None and domain not in wanted:
                continue

            documents: list[Document] = []
            for path in sorted(folder.rglob("*")):
                if not path.is_file() or path.suffix.lower() not in SUPPORTED_SUFFIXES:
                    continue
                try:
                    loaded = self.load_file(path)
                except Exception as exc:
                    logger.error("Error loading %s: %s", path.name, type(exc).__name__)
                    continue

                for doc in loaded:
                    doc.metadata.update(
                        {
                            "domain": domain,
                            "source_name": folder.name,
                            "source_file": path.name,
                            "file_type": path.suffix.lower(),
                        }
                    )
                documents.extend(loaded)

            usable = [d for d in documents if isinstance(d.page_content, str) and d.page_content.strip()]
            if not usable:
                logger.warning("Domain '%s' has no readable content, skipping", domain)
                continue

            corpus[domain] = self.split(usable)
            logger.info("Domain '%s': %d chunks", domain, len(corpus[domain]))

        return corpus
