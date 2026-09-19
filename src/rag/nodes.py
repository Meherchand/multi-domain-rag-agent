"""Pipeline nodes.

A node is a plain function from ``RAGState`` to ``RAGState``. Keeping them free
of graph wiring means they can be unit-tested directly and reused across
pipelines.
"""

from __future__ import annotations

import logging

from langchain_core.documents import Document
from langchain_core.messages import HumanMessage
from langchain_core.tools import Tool

from src.config.settings import settings
from src.rag.prompts import AGENT_SYSTEM_PROMPT, build_answer_prompt
from src.rag.state import RAGState

logger = logging.getLogger(__name__)


class RAGNodes:
    def __init__(self, store, llm, top_k: int | None = None):
        self.store = store
        self.llm = llm
        self.top_k = top_k or settings.retrieval.top_k
        self._agent = None  # built lazily; only the react pipeline needs it

    # -- retrieval ----------------------------------------------------------

    def retrieve(self, state: RAGState) -> RAGState:
        """Fetch candidate chunks for the question.

        Retrieval failure is not fatal: the generate node is told there is no
        context and says so, which is a better answer than a 500.
        """
        try:
            docs = self.store.retrieve(state.question, domains=state.domains, k=self.top_k)
        except ValueError as exc:
            logger.info("Retrieval returned nothing: %s", exc)
            docs = []
        return state.model_copy(update={"retrieved_docs": docs})

    # -- generation ---------------------------------------------------------

    def generate(self, state: RAGState) -> RAGState:
        prompt = build_answer_prompt(state.question, state.retrieved_docs)
        response = self.llm.invoke(prompt)
        answer = getattr(response, "content", str(response))
        return state.model_copy(
            update={"answer": answer, "metadata": {**state.metadata, "citations": state.citations()}}
        )

    # -- agent --------------------------------------------------------------

    def _tools(self, state: RAGState) -> list[Tool]:
        """Expose retrieval to the agent as a callable tool.

        The domain scope from the request is closed over, so the agent can
        decide *what* to search for but not *where* — scoping stays a property
        of the request, not something the model can widen.
        """
        domains = state.domains

        def search(query: str) -> str:
            try:
                docs: list[Document] = self.store.retrieve(query, domains=domains, k=self.top_k)
            except ValueError:
                return "No documents found for that query."
            state.retrieved_docs.extend(docs)
            return "\n\n".join(
                f"[{i}] {(d.metadata or {}).get('source_file', 'chunk')}\n{d.page_content}"
                for i, d in enumerate(docs[:8], start=1)
            )

        return [
            Tool(
                name="search_knowledge_base",
                description=(
                    "Search the indexed documentation corpus. Input: a natural-language "
                    "search query. Returns the most relevant passages."
                ),
                func=search,
            )
        ]

    def agent_answer(self, state: RAGState) -> RAGState:
        """Let a ReAct agent drive retrieval itself, over several turns.

        Compared with the fixed retrieve-then-generate pipeline this trades
        determinism and latency for the ability to decompose a question and
        search more than once.
        """
        from langgraph.prebuilt import create_react_agent

        agent = create_react_agent(self.llm, tools=self._tools(state), prompt=AGENT_SYSTEM_PROMPT)
        result = agent.invoke({"messages": [HumanMessage(content=state.question)]})

        messages = result.get("messages", [])
        answer = getattr(messages[-1], "content", "") if messages else ""
        return state.model_copy(
            update={
                "answer": answer or "Could not generate an answer.",
                "metadata": {
                    **state.metadata,
                    "pipeline": "react_agent",
                    "agent_steps": len(messages),
                    "citations": state.citations(),
                },
            }
        )
