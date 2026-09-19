"""The RAG engine: assembles a pipeline and runs it.

This is the seam the API, the CLI, the MCP server and the UIs all sit behind.
None of them knows which pipeline, model or vector store is configured — they
call :meth:`RAGEngine.run` or :meth:`RAGEngine.stream`.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

from src.config.settings import Settings, settings
from src.rag.nodes import RAGNodes
from src.rag.pipelines import get_pipeline
from src.rag.prompts import build_answer_prompt
from src.rag.state import RAGState

logger = logging.getLogger(__name__)


class RAGEngine:
    def __init__(self, store, llm=None, cfg: Settings | None = None, pipeline: str | None = None):
        self.cfg = cfg or settings
        self.store = store

        if llm is None:
            from src.llm.factory import get_llm

            llm = get_llm(self.cfg.llm)
        self.llm = llm

        self.pipeline_name = pipeline or self.cfg.pipeline
        self.nodes = RAGNodes(store, llm, top_k=self.cfg.retrieval.top_k)
        self.graph = get_pipeline(self.pipeline_name)(self.nodes).compile()
        logger.info("RAG engine ready (pipeline=%s)", self.pipeline_name)

    # -- invocation ---------------------------------------------------------

    @staticmethod
    def _to_state(payload: Any) -> RAGState:
        if isinstance(payload, str):
            return RAGState(question=payload)
        if isinstance(payload, RAGState):
            return payload
        if isinstance(payload, dict):
            return RAGState(**payload)
        raise ValueError("Input must be a question string, a dict, or a RAGState")

    def run(self, payload: Any) -> dict[str, Any]:
        """Run the pipeline to completion and return the final state as a dict."""
        return self.graph.invoke(self._to_state(payload))

    def ask(self, question: str, domains: list[str] | None = None) -> str:
        return self.run({"question": question, "domains": domains}).get("answer", "")

    # -- streaming ----------------------------------------------------------

    async def stream(self, question: str, domains: list[str] | None = None) -> AsyncIterator[str]:
        """Yield answer tokens as they are produced.

        Retrieval runs to completion first (there is nothing to stream from it),
        then the model is streamed directly. Going straight to the model rather
        than filtering ``astream_events`` keeps the token path short and avoids
        depending on LangGraph's event schema, which is still evolving.
        """
        try:
            docs = self.store.retrieve(question, domains=domains, k=self.cfg.retrieval.top_k)
        except ValueError:
            docs = []

        prompt = build_answer_prompt(question, docs)
        async for chunk in self.llm.astream(prompt):
            content = getattr(chunk, "content", "")
            if content:
                yield content

    async def stream_events(self, payload: Any) -> AsyncIterator[Any]:
        """Raw LangGraph events, for callers that want node-level progress."""
        async for event in self.graph.astream_events(self._to_state(payload), version="v2"):
            yield event
