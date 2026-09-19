"""MCP tool registry and the built-in tools, exercised against a fake context."""

from __future__ import annotations

import pytest

from src.mcp_server import examples, tools  # noqa: F401  (import registers the tools)
from src.mcp_server.registry import ToolContext, mcp_tool, registered_tools, takes_context


@pytest.fixture
def context(store, llm):
    """A ToolContext wired to the offline store and model."""
    from src.rag.engine import RAGEngine

    ctx = ToolContext()
    ctx._store = store
    ctx._engine = RAGEngine(store=store, llm=llm)
    return ctx


class TestRegistry:
    def test_built_in_tools_are_registered(self):
        names = {spec.name for spec in registered_tools()}
        assert {
            "query_knowledge_base",
            "list_domains",
            "search_documents",
            "get_domain_info",
        } <= names

    def test_example_tools_are_registered(self):
        names = {spec.name for spec in registered_tools()}
        assert {"summarize_domain", "check_order_status"} <= names

    def test_every_tool_has_a_description(self):
        assert all(spec.description for spec in registered_tools())

    def test_context_injection_is_detected(self):
        assert takes_context(tools.query_knowledge_base)
        assert not takes_context(examples.check_order_status)

    def test_a_custom_tool_can_be_registered(self):
        """The documented extension path in docs/adding-mcp-tools.md."""

        @mcp_tool("test_custom_tool", "A tool added by a fork.")
        def custom(value: str) -> dict:
            return {"echoed": value}

        assert "test_custom_tool" in {s.name for s in registered_tools()}
        assert custom("x") == {"echoed": "x"}


class TestBuiltInTools:
    def test_list_domains(self, context):
        assert set(tools.list_domains(context)) == {"order_service", "payment_service"}

    def test_query_returns_an_answer_and_citations(self, context):
        result = tools.query_knowledge_base(context, "How long is inventory reserved?", ["order_service"])
        assert "thirty minutes" in result["answer"]
        assert result["citations"]
        assert result["domains_searched"] == ["order_service"]

    def test_search_returns_raw_chunks(self, context):
        results = tools.search_documents(context, "idempotency", ["order_service"], top_k=2)
        assert results
        assert {"content", "metadata"} <= set(results[0])

    def test_search_returns_nothing_rather_than_raising(self, context):
        assert tools.search_documents(context, "anything", ["missing"], top_k=2) == []

    def test_domain_info_for_a_known_domain(self, context):
        assert tools.get_domain_info(context, "order_service")["exists"] is True

    def test_domain_info_for_an_unknown_domain_lists_alternatives(self, context):
        result = tools.get_domain_info(context, "nope")
        assert "error" in result
        assert "order_service" in result["available_domains"]


class TestExampleTools:
    def test_summarize_domain(self, context):
        result = examples.summarize_domain(context, "order_service")
        assert result["sampled_chunks"] > 0
        assert result["source_files"]

    def test_summarize_an_unknown_domain(self, context):
        assert "error" in examples.summarize_domain(context, "missing")

    def test_external_lookup_hits_the_fixture(self):
        assert examples.check_order_status("demo-order-001")["status"] == "PAID"

    def test_external_lookup_returns_a_structured_error(self):
        result = examples.check_order_status("no-such-order")
        assert "error" in result
        assert "demo-order-001" in result["known_ids"]
