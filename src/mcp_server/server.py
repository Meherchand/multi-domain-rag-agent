"""MCP server exposing the RAG system as tools and resources.

Everything in the registry is bound onto a ``FastMCP`` instance at import time.
The heavy objects (vector store, engine) stay lazy, so the handshake is instant
and the first tool call pays the initialisation cost.
"""

from __future__ import annotations

import functools
import inspect
import logging
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

# Make `src.*` importable however the server is launched (stdio from an editor,
# module, or script).
_ROOT = str(Path(__file__).resolve().parents[2])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.mcp_server import examples, tools  # noqa: F401,E402  (import registers the tools)
from src.mcp_server.registry import ToolContext, registered_tools, takes_context  # noqa: E402

logger = logging.getLogger(__name__)

SERVER_NAME = "knowledge-rag"
INSTRUCTIONS = (
    "Retrieval-augmented question answering over a configurable documentation "
    "corpus. Call `list_domains` first to see which knowledge domains are "
    "indexed, then `query_knowledge_base` for an answer, or `search_documents` "
    "for raw passages you can reason over yourself."
)

mcp = FastMCP(SERVER_NAME, instructions=INSTRUCTIONS)
_context = ToolContext()


def _bind(spec) -> None:
    """Expose a registered tool, injecting the context if the tool wants one."""
    func = spec.func

    if takes_context(func):

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            return func(_context, *args, **kwargs)

        # Hide the injected parameter from the schema MCP advertises.
        signature = inspect.signature(func)
        wrapper.__signature__ = signature.replace(
            parameters=[p for name, p in signature.parameters.items() if name != "context"]
        )
        handler = wrapper
    else:
        handler = func

    mcp.tool(name=spec.name, description=spec.description)(handler)


for _spec in registered_tools():
    _bind(_spec)

logger.info("MCP server '%s' exposing %d tools", SERVER_NAME, len(registered_tools()))


@mcp.resource("knowledge://domains")
def resource_domains() -> str:
    """Markdown list of every indexed knowledge domain."""
    domains = _context.store.loaded_domains()
    if not domains:
        return "No knowledge domains are currently indexed."
    return "# Available knowledge domains\n\n" + "\n".join(f"- {d}" for d in sorted(domains)) + "\n"
