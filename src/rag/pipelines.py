"""Pipeline registry — the workflow extension point.

A *pipeline* is a named LangGraph graph that turns a question into an answer.
Two ship with the project:

``simple_rag``
    ``retrieve → generate``. One retrieval pass, one generation. Deterministic
    in shape, one LLM call, predictable latency and cost. The right default.

``react_agent``
    A ReAct agent that calls retrieval as a tool and decides for itself how
    many times to search. Handles multi-part questions that one retrieval pass
    answers badly, at the cost of non-deterministic step count, more LLM calls
    and higher latency.

Select one with ``RAG_PIPELINE``. To add your own, register a builder — see
``docs/adding-workflows.md``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from langgraph.graph import END, StateGraph

from src.rag.nodes import RAGNodes
from src.rag.state import RAGState

logger = logging.getLogger(__name__)

PipelineBuilder = Callable[[RAGNodes], StateGraph]

_REGISTRY: dict[str, PipelineBuilder] = {}


def register_pipeline(name: str, builder: PipelineBuilder) -> None:
    """Register a pipeline builder under ``name``."""
    _REGISTRY[name] = builder


def available_pipelines() -> list[str]:
    return sorted(_REGISTRY)


def _simple_rag(nodes: RAGNodes) -> StateGraph:
    graph = StateGraph(RAGState)
    graph.add_node("retrieve", nodes.retrieve)
    graph.add_node("generate", nodes.generate)
    graph.set_entry_point("retrieve")
    graph.add_edge("retrieve", "generate")
    graph.add_edge("generate", END)
    return graph


def _react_agent(nodes: RAGNodes) -> StateGraph:
    graph = StateGraph(RAGState)
    graph.add_node("agent", nodes.agent_answer)
    graph.set_entry_point("agent")
    graph.add_edge("agent", END)
    return graph


register_pipeline("simple_rag", _simple_rag)
register_pipeline("react_agent", _react_agent)


def get_pipeline(name: str) -> PipelineBuilder:
    builder = _REGISTRY.get(name)
    if builder is None:
        raise ValueError(f"Unknown RAG_PIPELINE '{name}'. Available: {', '.join(available_pipelines())}")
    return builder
