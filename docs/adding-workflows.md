# Adding a workflow (pipeline)

A **workflow** here is a LangGraph graph that turns a question into an answer.
It is not a business-process engine — the system orchestrates retrieval and
generation, not multi-service transactions.

Two ship with the project:

| Name | Shape | Use it when |
|---|---|---|
| `simple_rag` | `retrieve → generate` | The default. One model call, predictable cost and latency. |
| `react_agent` | Agent calls retrieval as a tool, repeatedly | The question has parts that one retrieval pass answers badly. |

Select one with `RAG_PIPELINE`.

---

## The pieces

**State** ([`src/rag/state.py`](../src/rag/state.py)) — one object threaded
through the graph:

```python
class RAGState(BaseModel):
    question: str
    domains: list[str] | None
    retrieved_docs: list[Document]
    answer: str
    metadata: dict[str, Any]  # free slot for pipeline-specific data
```

Use `metadata` for anything your pipeline needs to record. That is what it is
for, and it means you rarely have to change the state class.

**Nodes** ([`src/rag/nodes.py`](../src/rag/nodes.py)) — functions from state to
state. `RAGNodes` provides `retrieve`, `generate` and `agent_answer`, and gives
you `self.store`, `self.llm` and `self.top_k`.

**Builder** — a function taking `RAGNodes` and returning an *uncompiled*
`StateGraph`. The engine compiles it.

---

## A worked example: rerank before generating

Dense top-k retrieval returns chunks that are similar to the *query*, which is
not the same as chunks that answer it. A reranking pass in between usually
helps, and it is a good shape for a custom pipeline: one new node, existing
nodes either side.

```python
# src/rag/custom_pipelines.py
from langgraph.graph import END, StateGraph

from src.rag.pipelines import register_pipeline
from src.rag.state import RAGState


def make_rerank_node(nodes, keep: int = 4):
    """Score retrieved chunks against the question and keep the best few."""

    def rerank(state: RAGState) -> RAGState:
        if len(state.retrieved_docs) <= keep:
            return state

        # Swap this for a cross-encoder in a real deployment. The point of the
        # node is the position in the graph, not this particular scorer.
        terms = {w for w in state.question.lower().split() if len(w) > 3}
        scored = sorted(
            state.retrieved_docs,
            key=lambda d: len(terms & set(d.page_content.lower().split())),
            reverse=True,
        )
        best = scored[:keep]

        return state.model_copy(
            update={
                "retrieved_docs": best,
                "metadata": {
                    **state.metadata,
                    "reranked_from": len(state.retrieved_docs),
                    "reranked_to": len(best),
                },
            }
        )

    return rerank


def rerank_pipeline(nodes) -> StateGraph:
    graph = StateGraph(RAGState)
    graph.add_node("retrieve", nodes.retrieve)
    graph.add_node("rerank", make_rerank_node(nodes))
    graph.add_node("generate", nodes.generate)

    graph.set_entry_point("retrieve")
    graph.add_edge("retrieve", "rerank")
    graph.add_edge("rerank", "generate")
    graph.add_edge("generate", END)
    return graph


register_pipeline("rerank_rag", rerank_pipeline)
```

Import the module once so registration runs — add it to `src/rag/__init__.py`,
or import it from your entry point. Then:

```env
RAG_PIPELINE=rerank_rag
```

```bash
python cli.py ask "how are refunds processed?" --domains payment_service
```

---

## Conditional branches

A graph does not have to be linear. Route on state with `add_conditional_edges`
— for example, skip generation entirely when retrieval found nothing:

```python
def route_after_retrieval(state: RAGState) -> str:
    return "generate" if state.retrieved_docs else "no_context"


def no_context(state: RAGState) -> RAGState:
    return state.model_copy(update={"answer": "The indexed documentation does not cover this question."})


graph.add_node("no_context", no_context)
graph.add_conditional_edges(
    "retrieve", route_after_retrieval, {"generate": "generate", "no_context": "no_context"}
)
graph.add_edge("no_context", END)
```

This saves a model call on questions the corpus cannot answer — worth having
once volume matters.

---

## Calling an external system from a node

A node is ordinary Python, so it can call anything. Two rules:

1. **Fail soft.** A node that raises fails the whole request. Catch, record the
   failure in `metadata`, and return usable state.
2. **Bound it.** Set a timeout. A hanging dependency should degrade the answer,
   not hold the request open.

```python
def enrich(state: RAGState) -> RAGState:
    try:
        data = my_client.fetch(state.question, timeout=5)
    except Exception as exc:
        return state.model_copy(
            update={"metadata": {**state.metadata, "enrichment_error": type(exc).__name__}}
        )
    return state.model_copy(update={"metadata": {**state.metadata, "enrichment": data}})
```

---

## Streaming

`RAGEngine.stream` retrieves, then streams the model directly. It **does not
run your custom nodes** — if your pipeline has a rerank or enrichment step, its
streamed answers will differ from its non-streamed ones.

Either stream through the graph instead:

```python
async for event in engine.stream_events({"question": q, "domains": d}):
    ...
```

or leave streaming on `simple_rag` and use your pipeline for non-streaming
calls. Be deliberate about which; a silent divergence between the two paths is
a confusing bug to chase later.

---

## Testing

```python
from src.rag.engine import RAGEngine
from src.rag.pipelines import available_pipelines


def test_rerank_pipeline(store, llm):
    import src.rag.custom_pipelines  # noqa: F401  — registers it

    assert "rerank_rag" in available_pipelines()

    engine = RAGEngine(store=store, llm=llm, pipeline="rerank_rag")
    result = engine.run({"question": "idempotency", "domains": ["order_service"]})

    assert result["answer"]
    assert result["metadata"]["reranked_to"] <= 4
```

The `store` and `llm` fixtures are offline, so this needs no external service.
`tests/test_rag_pipeline.py::TestPipelineRegistry` has a working example.

---

## Choosing between a pipeline and a tool

| You want | Build |
|---|---|
| Every question to go through an extra step | A **pipeline node** |
| The model to decide whether a step runs | A **tool** on `react_agent` |
| An external agent to call a capability | An **MCP tool** |

See [adding-mcp-tools.md](adding-mcp-tools.md).
