"""Built-in MCP tools.

Four tools cover the system's capabilities at two levels of abstraction:
``query_knowledge_base`` runs the whole pipeline and returns prose, while
``search_documents`` returns raw chunks so that the *calling* agent can do its
own reasoning over them. Which one a client wants depends on whether it has a
model of its own.

``examples.py`` shows how to add tools of your own.
"""

from __future__ import annotations

from typing import Any

from src.mcp_server.registry import ToolContext, mcp_tool


@mcp_tool(
    "query_knowledge_base",
    "Ask a natural-language question and get an answer grounded in the indexed corpus.",
)
def query_knowledge_base(
    context: ToolContext, question: str, domains: list[str] | None = None
) -> dict[str, Any]:
    """Run the full RAG pipeline.

    Args:
        question: The natural-language question to answer.
        domains: Optional knowledge domains to restrict the search to. Omit to
            search everything that is indexed — call ``list_domains`` first to
            see what is available.

    Returns:
        The answer plus the provenance of the passages it was built from.
    """
    result = context.engine.run({"question": question, "domains": domains})
    return {
        "answer": result.get("answer", ""),
        "domains_searched": domains or context.store.loaded_domains(),
        "citations": (result.get("metadata") or {}).get("citations", []),
    }


@mcp_tool("list_domains", "List the knowledge domains available to query.")
def list_domains(context: ToolContext) -> list[str]:
    """Return every indexed knowledge domain."""
    return context.store.loaded_domains()


@mcp_tool(
    "search_documents",
    "Similarity search returning raw document chunks, without generating an answer.",
)
def search_documents(
    context: ToolContext,
    query: str,
    domains: list[str] | None = None,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    """Retrieve passages without running a model over them.

    Useful when the caller wants to reason over the source material itself
    rather than receive a synthesised answer.

    Args:
        query: The search query.
        domains: Optional domains to restrict the search to.
        top_k: Maximum chunks to return per domain.
    """
    try:
        docs = context.store.retrieve(query, domains=domains, k=top_k)
    except ValueError:
        return []
    return [{"content": d.page_content, "metadata": d.metadata} for d in docs]


@mcp_tool("get_domain_info", "Describe one knowledge domain.")
def get_domain_info(context: ToolContext, domain: str) -> dict[str, Any]:
    """Report whether a domain exists and is loaded.

    Args:
        domain: The domain identifier, as returned by ``list_domains``.
    """
    info = context.store.domain_info(domain)
    if not info["exists"]:
        return {
            "error": f"Knowledge domain '{domain}' not found.",
            "available_domains": context.store.loaded_domains(),
        }
    return info
