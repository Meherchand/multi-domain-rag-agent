"""Source-code ingestion: index a codebase as a knowledge domain.

Prose splitters are a poor fit for code — a fixed character window cuts through
the middle of a method and produces chunks that retrieve badly. This module
splits **structurally** instead:

* ``.java`` — AST-aware splitting, plus class and method names lifted out of
  the parse tree into metadata so a retrieved chunk can say what it belongs to;
* ``.yml`` / ``.yaml`` / ``.properties`` — config-aware splitting with small
  windows, because config keys are dense and individually meaningful;
* ``.json`` — structural node parsing;
* ``.md`` — heading-aware parsing;
* ``.sql`` / ``.gradle`` — sentence splitting, which is a reasonable default.

Optional dependency: this needs the ``code`` extra (``llama-index-core`` and
``tree-sitter``). Without it, importing the module raises a clear error and the
rest of the system is unaffected.

Two safety properties are enforced, because this walks a user-supplied path:

* every file is checked to resolve *inside* the repository root, so a symlink
  out of the tree cannot pull in arbitrary files;
* metadata is flattened to scalars, since vector stores reject nested values.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from langchain_core.documents import Document

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".java", ".yml", ".yaml", ".properties", ".sql", ".md", ".json", ".gradle"}


class CodeRepositoryIngestion:
    def __init__(self, repo_path: str, domain_name: str | None = None):
        try:
            from llama_index.core.node_parser import (  # noqa: F401
                CodeSplitter,
                JSONNodeParser,
                MarkdownNodeParser,
                SentenceSplitter,
            )
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise ImportError(
                "Code-repository ingestion needs the optional 'code' extra: "
                "pip install -r requirements-code.txt"
            ) from exc

        from llama_index.core.node_parser import (
            CodeSplitter,
            JSONNodeParser,
            MarkdownNodeParser,
            SentenceSplitter,
        )

        self.repo_path = Path(repo_path).resolve()
        if not self.repo_path.is_dir():
            raise ValueError("Repository path does not exist or is not a directory")

        self.domain_name = domain_name or self.repo_path.name
        self.java_parser = self._init_tree_sitter()

        self.split_java = CodeSplitter(language="java", chunk_lines=40, chunk_lines_overlap=8, max_chars=800)
        self.split_config = CodeSplitter(
            language="yaml", chunk_lines=25, chunk_lines_overlap=5, max_chars=400
        )
        self.split_prose = SentenceSplitter(chunk_size=400, chunk_overlap=40)
        self.split_json = JSONNodeParser()
        self.split_markdown = MarkdownNodeParser()

    @staticmethod
    def _init_tree_sitter():
        """Return a Java parser, or ``None`` if tree-sitter is unavailable.

        Metadata extraction is a nice-to-have; ingestion still works without it.
        """
        try:
            import tree_sitter
            import tree_sitter_java

            parser = tree_sitter.Parser()
            parser.language = tree_sitter.Language(tree_sitter_java.language())
            return parser
        except Exception:
            logger.warning("tree-sitter Java support unavailable; skipping code metadata extraction")
            return None

    # -- metadata -----------------------------------------------------------

    def _java_metadata(self, content: str) -> dict[str, Any]:
        meta: dict[str, Any] = {"class_name": "", "method_names": ""}
        if self.java_parser is None:
            return meta

        try:
            import tree_sitter
            import tree_sitter_java

            language = tree_sitter.Language(tree_sitter_java.language())
            root = self.java_parser.parse(bytes(content, "utf8")).root_node

            class_matches = tree_sitter.Query(
                language, "(class_declaration name: (identifier) @class_name)"
            ).matches(root)
            if class_matches:
                node = class_matches[0][1]["class_name"][0]
                meta["class_name"] = content[node.start_byte : node.end_byte]

            method_matches = tree_sitter.Query(
                language, "(method_declaration name: (identifier) @method_name)"
            ).matches(root)
            names = [
                content[m[1]["method_name"][0].start_byte : m[1]["method_name"][0].end_byte]
                for m in method_matches[:10]
            ]
            meta["method_names"] = ",".join(names)
        except Exception as exc:
            logger.warning("Could not extract Java metadata: %s", type(exc).__name__)
        return meta

    @staticmethod
    def _flatten(metadata: dict[str, Any]) -> dict[str, Any]:
        """Collapse metadata to scalars; vector stores reject nested values."""
        flat: dict[str, Any] = {}
        for key, value in metadata.items():
            if isinstance(value, (str, int, float, bool)):
                flat[key] = value
            elif isinstance(value, list):
                flat[key] = ",".join(str(v) for v in value[:10])
            elif value is None:
                flat[key] = ""
            else:
                flat[key] = str(value)
        return flat

    def _within_repo(self, path: Path) -> bool:
        try:
            path.resolve().relative_to(self.repo_path)
            return True
        except ValueError:
            logger.warning("Skipping a file that resolves outside the repository root")
            return False

    # -- processing ---------------------------------------------------------

    def _process_file(self, path: Path) -> list[Document]:
        from llama_index.core import Document as LlamaDocument

        if not self._within_repo(path):
            return []

        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return []
        if not content.strip():
            return []

        suffix = path.suffix.lower()
        metadata: dict[str, Any] = {
            "domain": self.domain_name,
            "file_path": str(path.relative_to(self.repo_path)),
            "file_name": path.name,
            "language": "",
            "class_name": "",
            "method_names": "",
        }

        try:
            if suffix == ".java":
                metadata["language"] = "java"
                metadata.update(self._java_metadata(content))
                nodes = self.split_java.get_nodes_from_documents([LlamaDocument(text=content)])
            elif suffix in {".yml", ".yaml", ".properties"}:
                metadata["language"] = "yaml"
                nodes = self.split_config.get_nodes_from_documents([LlamaDocument(text=content)])
            elif suffix == ".json":
                metadata["language"] = "json"
                nodes = self.split_json.get_nodes_from_documents([LlamaDocument(text=content)])
            elif suffix == ".md":
                metadata["language"] = "markdown"
                nodes = self.split_markdown.get_nodes_from_documents([LlamaDocument(text=content)])
            elif suffix in {".sql", ".gradle"}:
                metadata["language"] = suffix.lstrip(".")
                nodes = self.split_prose.get_nodes_from_documents([LlamaDocument(text=content)])
            else:
                return []
        except Exception as exc:
            logger.error("Failed to split %s: %s", path.name, type(exc).__name__)
            return []

        flat = self._flatten(metadata)
        return [Document(page_content=node.text, metadata=dict(flat)) for node in nodes]

    def scan(self) -> list[Document]:
        documents: list[Document] = []
        for path in sorted(self.repo_path.rglob("*")):
            if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS:
                documents.extend(self._process_file(path))
        logger.info("Repository scan complete: %d chunks", len(documents))
        return documents

    def ingest(self, store) -> bool:
        """Scan the repository and index it as ``repo_<name>``."""
        documents = self.scan()
        if not documents:
            logger.warning("No supported files found in the repository")
            return False
        domain = f"repo_{self.domain_name.replace('-', '_').replace(' ', '_').lower()}"
        return store.create_collection(domain, documents)
