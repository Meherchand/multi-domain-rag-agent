"""Example MCP tools — copy these when adding your own.

Both operate on synthetic data bundled with the repository. They exist to show
the two shapes a tool usually takes:

1. ``summarize_domain`` — reads from the system's own state (the vector store)
   and derives something from it.
2. ``check_order_status`` — calls an *external* system. Here that system is a
   local JSON fixture, so the demo runs offline; in a fork you would replace
   the fixture read with an HTTP call to your own API.

See ``docs/adding-mcp-tools.md``.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

from src.config.settings import PROJECT_ROOT
from src.mcp_server.registry import ToolContext, mcp_tool

FIXTURES = PROJECT_ROOT / "examples" / "fixtures"


@mcp_tool("summarize_domain", "Summarise what a knowledge domain contains.")
def summarize_domain(context: ToolContext, domain: str) -> dict[str, Any]:
    """Describe a domain by sampling it, rather than by answering a question.

    Args:
        domain: The domain identifier, as returned by ``list_domains``.
    """
    try:
        docs = context.store.retrieve("overview summary purpose", domains=[domain], k=8)
    except ValueError:
        return {"error": f"Knowledge domain '{domain}' has no retrievable content."}

    sources = Counter(str((d.metadata or {}).get("source_file", "unknown")) for d in docs)
    return {
        "domain": domain,
        "sampled_chunks": len(docs),
        "source_files": [{"file": f, "chunks": n} for f, n in sources.most_common()],
        "excerpt": docs[0].page_content[:400] if docs else "",
    }


@mcp_tool("check_order_status", "Look up a demo order by id (synthetic fixture data).")
def check_order_status(order_id: str) -> dict[str, Any]:
    """Example of a tool that reaches an external system.

    This reads a local JSON fixture so the demo works offline. Replace the
    fixture read with a call to your own service, and keep the same contract:
    return a plain dict, and return a structured error rather than raising.

    Args:
        order_id: A demo order identifier, e.g. ``demo-order-001``.
    """
    path = FIXTURES / "orders.json"
    if not path.is_file():
        return {"error": "Demo fixture not found", "order_id": order_id}

    try:
        orders: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"error": "Demo fixture is not valid JSON", "order_id": order_id}

    for order in orders:
        if order.get("order_id") == order_id:
            return order
    return {
        "error": f"No demo order with id '{order_id}'",
        "known_ids": [o.get("order_id") for o in orders],
    }
