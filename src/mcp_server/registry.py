"""Tool registry for the MCP server.

MCP lets an external client (an IDE, a desktop assistant, another agent) call
this system's capabilities directly. The registry exists so that adding a tool
does not mean editing the server: write a function, decorate it with
:func:`mcp_tool`, make sure the module is imported, and it is exposed.

Tools receive a :class:`ToolContext`, which carries the vector store and the
RAG engine. Both are built lazily on first use, so an MCP handshake stays
instant even when the backing store is slow to come up.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ToolContext:
    """What a tool is given. Both fields are lazily initialised."""

    _store: Any | None = None
    _engine: Any | None = None

    @property
    def store(self):
        if self._store is None:
            from src.vectorstore.factory import build_knowledge_base

            logger.info("Initialising knowledge base for MCP tools")
            self._store = build_knowledge_base()
        return self._store

    @property
    def engine(self):
        if self._engine is None:
            from src.rag.engine import RAGEngine

            self._engine = RAGEngine(store=self.store)
        return self._engine


@dataclass
class ToolSpec:
    name: str
    description: str
    func: Callable[..., Any]


_TOOLS: dict[str, ToolSpec] = {}


def mcp_tool(name: str, description: str = "") -> Callable:
    """Register a function as an MCP tool.

    The function may take a ``context: ToolContext`` first parameter; if it
    does, the registry injects it and hides it from the tool's public schema.
    """

    def decorator(func: Callable) -> Callable:
        _TOOLS[name] = ToolSpec(name=name, description=description or (func.__doc__ or "").strip(), func=func)
        return func

    return decorator


def registered_tools() -> list[ToolSpec]:
    return [_TOOLS[k] for k in sorted(_TOOLS)]


def takes_context(func: Callable) -> bool:
    params = list(inspect.signature(func).parameters)
    return bool(params) and params[0] == "context"
