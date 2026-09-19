"""Pipeline behaviour: retrieval, prompt construction, graph execution."""

from __future__ import annotations

import pytest
from langchain_core.documents import Document

from src.rag.engine import RAGEngine
from src.rag.nodes import RAGNodes
from src.rag.pipelines import available_pipelines, get_pipeline, register_pipeline
from src.rag.prompts import build_answer_prompt, format_context
from src.rag.state import RAGState


class TestState:
    def test_citations_are_deduplicated(self):
        state = RAGState(
            question="q",
            retrieved_docs=[
                Document(page_content="a", metadata={"retrieved_from": "d1", "source_file": "f.md"}),
                Document(page_content="b", metadata={"retrieved_from": "d1", "source_file": "f.md"}),
                Document(page_content="c", metadata={"retrieved_from": "d2", "source_file": "g.md"}),
            ],
        )
        assert state.citations() == [
            {"domain": "d1", "source": "f.md"},
            {"domain": "d2", "source": "g.md"},
        ]

    def test_citations_are_empty_without_retrieval(self):
        assert RAGState(question="q").citations() == []


class TestPrompts:
    def test_context_is_numbered_and_attributed(self, documents):
        for doc in documents:
            doc.metadata["retrieved_from"] = doc.metadata["domain"]
        rendered = format_context(documents)
        assert "[1] (order_service · overview.md)" in rendered
        assert "[3] (payment_service · overview.md)" in rendered

    def test_context_respects_the_character_budget(self):
        docs = [Document(page_content="x" * 500, metadata={}) for _ in range(20)]
        assert len(format_context(docs, max_chars=1200)) <= 1200

    def test_empty_context_renders_empty(self):
        assert format_context([]) == ""

    def test_prompt_carries_the_grounding_instruction(self, documents):
        prompt = build_answer_prompt("why?", documents)
        assert "only the context below" in prompt
        assert "why?" in prompt


class TestNodes:
    def test_retrieve_populates_state(self, store, llm):
        nodes = RAGNodes(store, llm, top_k=2)
        result = nodes.retrieve(RAGState(question="idempotency key", domains=["order_service"]))
        assert result.retrieved_docs
        assert result.question == "idempotency key"

    def test_retrieve_degrades_instead_of_raising(self, store, llm):
        """A domain with no match must not turn into a 500."""
        nodes = RAGNodes(store, llm, top_k=2)
        result = nodes.retrieve(RAGState(question="anything", domains=["missing_domain"]))
        assert result.retrieved_docs == []

    def test_generate_attaches_citations(self, store, llm):
        nodes = RAGNodes(store, llm, top_k=2)
        state = nodes.retrieve(RAGState(question="inventory reservation", domains=["order_service"]))
        result = nodes.generate(state)
        assert result.answer
        assert result.metadata["citations"]

    def test_generate_says_so_when_nothing_was_retrieved(self, store, llm):
        nodes = RAGNodes(store, llm)
        result = nodes.generate(RAGState(question="anything", retrieved_docs=[]))
        assert "nothing to ground" in result.answer.lower()


class TestPipelineRegistry:
    def test_ships_both_pipelines(self):
        assert {"simple_rag", "react_agent"} <= set(available_pipelines())

    def test_unknown_pipeline_is_reported_clearly(self):
        with pytest.raises(ValueError, match="Unknown RAG_PIPELINE"):
            get_pipeline("does_not_exist")

    def test_a_custom_pipeline_can_be_registered(self, store, llm):
        """The documented extension path in docs/adding-workflows.md."""
        from langgraph.graph import END, StateGraph

        def passthrough(nodes):
            graph = StateGraph(RAGState)
            graph.add_node("only", lambda s: s.model_copy(update={"answer": "fixed answer"}))
            graph.set_entry_point("only")
            graph.add_edge("only", END)
            return graph

        register_pipeline("test_passthrough", passthrough)
        assert "test_passthrough" in available_pipelines()

        engine = RAGEngine(store=store, llm=llm, pipeline="test_passthrough")
        assert engine.run({"question": "anything"})["answer"] == "fixed answer"


class TestEngine:
    def test_simple_rag_produces_a_grounded_answer(self, engine):
        result = engine.run({"question": "How long is inventory reserved?", "domains": ["order_service"]})
        assert "thirty minutes" in result["answer"]

    def test_ask_is_a_shorthand(self, engine):
        assert engine.ask("what is a capture?", domains=["payment_service"])

    def test_accepts_a_bare_question_string(self, engine):
        assert engine.run("what is a capture?")["answer"]

    def test_rejects_an_unusable_payload(self, engine):
        with pytest.raises(ValueError, match="must be"):
            engine.run(12345)

    def test_scoping_changes_what_is_retrieved(self, engine):
        payment = engine.run({"question": "authorisation hold", "domains": ["payment_service"]})
        citations = (payment["metadata"] or {}).get("citations", [])
        assert {c["domain"] for c in citations} == {"payment_service"}

    async def test_streaming_yields_the_same_content(self, engine):
        chunks = [c async for c in engine.stream("How long is inventory reserved?", ["order_service"])]
        assert len(chunks) > 1
        assert "thirty minutes" in "".join(chunks)
