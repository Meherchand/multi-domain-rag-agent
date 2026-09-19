"""Shared state passed between pipeline nodes.

LangGraph threads one object through the graph; every node takes it and returns
it. Keeping the state explicit (rather than passing loose arguments) is what
makes a pipeline composable: a new node can read what earlier nodes produced
without any node knowing who runs before or after it.
"""

from __future__ import annotations

from typing import Any

from langchain_core.documents import Document
from pydantic import BaseModel, ConfigDict, Field


class RAGState(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    question: str
    domains: list[str] | None = None
    retrieved_docs: list[Document] = Field(default_factory=list)
    answer: str = ""
    # Free-form slot for pipeline-specific data (tool traces, scores, timings).
    # Custom nodes can use it without changing this class.
    metadata: dict[str, Any] = Field(default_factory=dict)

    def citations(self) -> list[dict[str, str]]:
        """De-duplicated provenance for the retrieved chunks."""
        seen: dict[str, dict[str, str]] = {}
        for doc in self.retrieved_docs:
            meta = doc.metadata or {}
            key = f"{meta.get('retrieved_from', '')}/{meta.get('source_file', '')}"
            if key not in seen:
                seen[key] = {
                    "domain": str(meta.get("retrieved_from") or meta.get("domain") or ""),
                    "source": str(meta.get("source_file") or meta.get("file_path") or ""),
                }
        return list(seen.values())
