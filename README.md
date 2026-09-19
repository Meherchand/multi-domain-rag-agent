# Multi-Domain RAG Agent

A retrieval-augmented question-answering system for documentation, built so
that the model, the embedder, the vector store, the answering pipeline and the
tool surface are all replaceable without touching the core.

Clone it, run `make demo`, and it answers questions about a bundled example
corpus with **no API keys, no vector database, and no network access**. Point
it at your own documents and providers when you are ready.

> This is a reference implementation and a starting point, not a
> production-hardened service. See [Limitations](#limitations).

---

## The problem

Engineering documentation is spread across many repositories, wikis and
handbooks. Full-text search finds the page that contains a word; it does not
answer "what happens if the payment provider times out mid-capture?" — an
answer that lives in three paragraphs across two documents.

Retrieval-augmented generation solves the retrieval half well. The part that
tends to go wrong is everything around it:

- **Provider lock-in.** A gateway URL hardcoded in a config class means
  switching models is a code change, and running the project on a laptop is
  impossible.
- **Scope.** Searching one undifferentiated index across every team's
  documentation returns plausible passages from the wrong system.
- **Runnability.** A RAG project that needs a managed vector database and two
  API keys before it will start is a project nobody can evaluate.

This repository takes a position on all three: **providers are configuration**,
**the corpus is partitioned by domain**, and **the whole thing runs offline**.

---

## Architecture

```
                    ┌──────────────────────────────────────┐
                    │  Clients                             │
                    │  Chainlit UI · Streamlit UI · CLI    │
                    │  MCP clients · OpenAI-compatible SDKs│
                    └───────────────────┬──────────────────┘
                                        │ HTTP / stdio
                    ┌───────────────────▼──────────────────┐
                    │  Interface layer                     │
                    │  FastAPI  ·  MCP server              │
                    │  auth · streaming · tool registry    │
                    └───────────────────┬──────────────────┘
                                        │
                    ┌───────────────────▼──────────────────┐
                    │  RAG engine                          │
                    │  LangGraph pipeline registry         │
                    │  simple_rag  │  react_agent  │  yours│
                    └──────┬────────────────────────┬──────┘
                           │                        │
              ┌────────────▼─────────┐   ┌──────────▼──────────┐
              │  Retrieval           │   │  Generation         │
              │  vector store        │   │  LLM factory        │
              │  ┌─────────────────┐ │   │ ┌─────────────────┐ │
              │  │ milvus │ memory │ │   │ │ openai │ echo   │ │
              │  └─────────────────┘ │   │ └─────────────────┘ │
              └────────────┬─────────┘   └─────────────────────┘
                           │
              ┌────────────▼──────────────────────────────┐
              │  Embeddings factory                       │
              │  ┌──────────────────┐                     │
              │  │ openai │ hashing │                     │
              │  └──────────────────┘                     │
              └────────────┬──────────────────────────────┘
                           │
              ┌────────────▼──────────────────────────────┐
              │  Ingestion                                │
              │  markdown/text/pdf · AST-aware code       │
              └────────────┬──────────────────────────────┘
                           │
              ┌────────────▼──────────────────────────────┐
              │  Corpus: data/<domain>/**                 │
              │  one vector collection per domain         │
              └───────────────────────────────────────────┘
```

Each boxed pair is a registry with a swappable default. The dashed path through
`memory` / `echo` / `hashing` is demo mode, which needs nothing external.

Full detail in **[docs/architecture.md](docs/architecture.md)**.

### The central design decision: one collection per domain

Rather than one index with a metadata filter, each corpus folder becomes its
own vector collection. This buys:

- **Cheap scoping** — restricting a query to two domains skips the other
  collections entirely, instead of over-fetching and post-filtering.
- **Independent lifecycle** — re-index one domain without touching the rest.
- **Blast-radius containment** — a domain that fails to index does not break
  the others.

The cost is fan-out on unscoped queries, which is why the API takes an explicit
`domains` argument and the UIs push you toward using it.

---

## Features

**Retrieval**
- Per-domain vector collections with query fan-out and result merging
- Configurable chunking (recursive character splitting with overlap)
- Provenance metadata on every chunk, surfaced as citations on every answer
- Pluggable backends: Milvus, or an in-process index for demo and tests

**Generation**
- Any OpenAI-compatible chat endpoint (hosted API, vLLM, Ollama, a gateway)
- Prompt construction isolated in one module, with an explicit grounding
  instruction and a hard context budget
- An offline extractive stub so the full path runs with no provider

**Pipelines**
- `simple_rag` — deterministic `retrieve → generate`, one model call
- `react_agent` — a ReAct agent that calls retrieval as a tool and decides for
  itself how many times to search
- A registry so you can add your own graph without forking the engine

**Interfaces**
- Native REST API with domain scoping, citations and NDJSON streaming
- OpenAI-compatible `/v1/chat/completions` (including SSE) so existing clients
  work unchanged
- MCP server over stdio and SSE, with a decorator-based tool registry
- Chainlit and Streamlit UIs, and a CLI

**Operations**
- Non-blocking start-up: serve from existing collections while indexing runs on
  a background thread, reported through `/health`
- Retry with backoff on embedding calls; reconnect-and-retry on vector-store
  admin calls
- Shared-secret API auth, security headers, narrow CORS defaults
- Container image and compose stack; GitHub Actions running lint, tests, a
  secret scan and a container smoke test

---

## Technology

| Area | Choice |
|---|---|
| Orchestration | LangGraph (state machine), LangChain core abstractions |
| API | FastAPI, Uvicorn, Pydantic v2 |
| Vector store | Milvus (`langchain-milvus`, `pymilvus`), or in-process |
| Embeddings | OpenAI-compatible HTTP, or an offline hashing embedder |
| Models | Any OpenAI-compatible chat endpoint |
| Tooling protocol | MCP (`mcp[cli]`, FastMCP) |
| UI | Chainlit, Streamlit |
| Persistence (optional) | Postgres via asyncpg / SQLAlchemy |
| Code ingestion (optional) | LlamaIndex node parsers, tree-sitter |
| Quality | pytest, ruff, GitHub Actions, Docker |

---

## Project structure

```
├── api_app.py              FastAPI service: native + OpenAI-compatible APIs
├── chainlit_app.py         Chainlit UI, share pages
├── streamlit_app.py        Streamlit UI
├── mcp_app.py              MCP server entry point (stdio / SSE)
├── cli.py                  index · ask · chat · domains
│
├── src/
│   ├── config/
│   │   ├── settings.py     Every environment variable, in one place
│   │   └── domains.py      Display metadata loaded from data/domains.json
│   ├── llm/                Chat-model factory + offline echo stub
│   ├── embeddings/         Embedding factory, HTTP embedder, hashing embedder
│   ├── vectorstore/
│   │   ├── base.py         Backend interface; fan-out retrieval
│   │   ├── milvus_store.py Milvus backend
│   │   ├── memory_store.py In-process backend
│   │   └── factory.py      Backend registry + index bootstrap
│   ├── ingestion/          Corpus loading/chunking; AST-aware code ingestion
│   ├── rag/
│   │   ├── state.py        State threaded through the graph
│   │   ├── nodes.py        Node functions (retrieve, generate, agent)
│   │   ├── pipelines.py    Pipeline registry — the workflow extension point
│   │   ├── prompts.py      Prompt construction and context formatting
│   │   └── engine.py       Assembles and runs a pipeline
│   ├── mcp_server/
│   │   ├── registry.py     @mcp_tool decorator, lazy tool context
│   │   ├── tools.py        Built-in tools
│   │   └── examples.py     Example tools to copy
│   └── db/                 Optional chat persistence and share links
│
├── data/                   The corpus. One folder per knowledge domain.
├── examples/fixtures/      Synthetic data for the example MCP tools
├── docs/                   Architecture and extension guides
├── tests/                  Offline test suite
└── scripts/scan_secrets.sh Pre-publish secret check
```

---

## Installation

Requires Python 3.11+.

```bash
git clone <your-fork-url> multi-domain-rag-agent
cd multi-domain-rag-agent
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

`.env` ships with `DEMO_MODE=true`, so nothing else is required to start.

---

## Configuration

All configuration is environment-based and documented in `.env.example`. The
variables that matter most:

| Variable | Purpose |
|---|---|
| `DEMO_MODE` | `true` swaps in offline stubs for the model, embedder and store |
| `LLM_PROVIDER` / `LLM_MODEL` / `LLM_BASE_URL` / `LLM_API_KEY` | Chat model |
| `EMBEDDING_PROVIDER` / `EMBEDDING_MODEL` / `EMBEDDING_BASE_URL` | Embeddings |
| `VECTOR_STORE` / `VECTOR_STORE_URI` / `VECTOR_COLLECTION_PREFIX` | Vector store |
| `RAG_PIPELINE` | `simple_rag` or `react_agent` |
| `DATA_DIR` / `INIT_MODE` | Where the corpus is, and how it is indexed at start-up |
| `API_KEY` | Shared secret for the API's authenticated endpoints |
| `RETRIEVAL_TOP_K` / `CHUNK_SIZE` / `CHUNK_OVERLAP` | Retrieval tuning |

No endpoint is hardcoded anywhere in the source — a test asserts this.

Full reference: **[docs/configuration.md](docs/configuration.md)**.

---

## Running locally

### Demo — offline, no credentials

```bash
make demo
```

Indexes the bundled corpus into an in-process store using the offline embedder,
then answers a question. No network access, no keys.

### CLI

```bash
python cli.py index                              # build the index
python cli.py domains                            # list knowledge domains
python cli.py ask "how are refunds processed?" --domains payment_service
python cli.py chat                               # interactive
```

### API + UI

```bash
make api    # http://localhost:8000  (docs at /docs)
make ui     # http://localhost:8502
```

Or with containers:

```bash
docker compose up api ui
```

### MCP server

```bash
python mcp_app.py                              # stdio
python mcp_app.py --transport sse --port 8100  # HTTP
```

`.vscode/mcp.json` wires it into an MCP-aware editor.

---

## Demo

```console
$ make demo

Multi-Domain RAG Agent — DEMO (offline stubs)
  pipeline     : simple_rag
  llm          : echo
  embeddings   : hashing
  vector store : memory

INFO src.vectorstore.memory_store: Indexed domain 'order_service': 12 chunks
INFO src.vectorstore.memory_store: Indexed domain 'payment_service': 14 chunks
INFO src.vectorstore.base: Retrieved 5 chunks from domain 'order_service'

**Demo mode (echo provider — no model inference).** The passages below are the
retrieved context most relevant to your question.

- Submitting an order places a soft reservation on each line item for 30 minutes.
- Reservations are held in a separate store keyed by `(order_id, sku)` with a TTL.
- order is cancelled by a sweeper that runs every minute.

Sources:
  - order_service/overview.md
  - order_service/api.md
```

Demo mode runs no model — it returns the retrieved passages, labelled as such.
Configure a real provider for generated prose.

Against the HTTP API:

```bash
curl -s -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{"question":"Which payment errors are safe to retry?","domains":["payment_service"]}'
```

```json
{
  "answer": "Only transport errors and explicitly retryable provider codes are retried...",
  "citations": [{ "domain": "payment_service", "source": "troubleshooting.md" }],
  "response_time": 1.83
}
```

---

## Adding your own knowledge base

Each subdirectory of `data/` becomes one searchable domain:

```
data/
  my_service/
    overview.md
    runbook.md
  another_area/
    handbook.pdf
```

```bash
python cli.py index
```

Optionally add labels, groupings and suggested questions in `data/domains.json`
— every field is optional, and a domain with no entry falls back to a
title-cased folder name.

Details, including source-code ingestion: **[docs/extending.md](docs/extending.md)**.

---

## Adding your own workflow

A *workflow* here is a LangGraph pipeline that turns a question into an answer.
Register a builder and select it with `RAG_PIPELINE`:

```python
from langgraph.graph import END, StateGraph
from src.rag.pipelines import register_pipeline
from src.rag.state import RAGState


def rerank_then_answer(nodes):
    graph = StateGraph(RAGState)
    graph.add_node("retrieve", nodes.retrieve)
    graph.add_node("rerank", my_rerank_node)
    graph.add_node("generate", nodes.generate)
    graph.set_entry_point("retrieve")
    graph.add_edge("retrieve", "rerank")
    graph.add_edge("rerank", "generate")
    graph.add_edge("generate", END)
    return graph


register_pipeline("rerank_then_answer", rerank_then_answer)
```

**[docs/adding-workflows.md](docs/adding-workflows.md)**

---

## Adding your own MCP tool

Implement the tool interface and register it with the agent:

```python
from src.mcp_server.registry import ToolContext, mcp_tool


@mcp_tool("check_deployment", "Look up the deployed version of a service.")
def check_deployment(service: str) -> dict:
    """Args:
    service: The service name to look up.
    """
    return {"service": service, "version": "1.4.2", "healthy": True}
```

Tools that need retrieval take `context: ToolContext` as their first parameter;
the registry injects it and hides it from the advertised schema. Two worked
examples are in `src/mcp_server/examples.py`.

**[docs/adding-mcp-tools.md](docs/adding-mcp-tools.md)**

---

## LLM configuration

Anything speaking the OpenAI chat-completions format works. Set the base URL:

```env
# Hosted OpenAI
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o-mini
LLM_BASE_URL=
LLM_API_KEY=sk-...

# Local Ollama
LLM_PROVIDER=openai
LLM_MODEL=llama3.1
LLM_BASE_URL=http://localhost:11434/v1
LLM_API_KEY=ollama

# Self-hosted vLLM or a gateway
LLM_PROVIDER=openai
LLM_BASE_URL=http://localhost:4000/v1
```

For a provider with a different wire format, register a builder in
`src/llm/factory.py` that returns a LangChain `BaseChatModel`.

---

## Extending the architecture

| Extension point | Where | Selected by |
|---|---|---|
| Chat model | `src/llm/factory.py` → `register_llm` | `LLM_PROVIDER` |
| Embeddings | `src/embeddings/factory.py` → `register_embeddings` | `EMBEDDING_PROVIDER` |
| Vector store | `src/vectorstore/factory.py` → `register_backend` | `VECTOR_STORE` |
| Answering pipeline | `src/rag/pipelines.py` → `register_pipeline` | `RAG_PIPELINE` |
| MCP tool | `src/mcp_server/registry.py` → `@mcp_tool` | Auto-registered on import |
| Knowledge source | `data/<domain>/` | Filesystem |
| Prompts | `src/rag/prompts.py` | Direct edit |

Each registry takes a name and a builder, so adding an implementation never
requires editing the code that consumes it.

**[docs/extending.md](docs/extending.md)**

---

## Security

- Secrets come from the environment; nothing credential-shaped is committed.
- `.env` is gitignored; `.env.example` ships empty placeholders.
- Embedding failures log an exception type, never the endpoint, the credential,
  or the input text.
- The API requires a shared secret on every non-health endpoint, compared with
  `hmac.compare_digest`.
- CORS defaults to the local UI only.
- Model output is rendered as sanitised Markdown, never raw HTML.
- Source-code ingestion is disabled unless `REPO_INGEST_ROOT` names an allowed
  root, and paths are checked to resolve inside it.
- `./scripts/scan_secrets.sh` runs in CI and before publishing.

**[docs/security.md](docs/security.md)**

---

## Testing

```bash
make test      # 130 tests, no external services
make lint
make scan-secrets
```

The suite forces the offline providers before any module is imported, so no
test can reach a real endpoint even if one is configured in your shell. It
covers domain partitioning and scoped retrieval, chunking and provenance,
prompt construction and context budgeting, pipeline registration, API contracts
and auth for both API surfaces, the MCP registry and every shipped tool, and
config parsing including a check that no endpoint is hardcoded.

---

## Limitations

Honest about what this is:

- **A reference implementation**, not a hardened service. It has not been load
  tested, and there are no published latency or throughput numbers.
- **Demo mode runs no model.** The `echo` provider returns retrieved passages;
  the `hashing` embedder is lexical, not semantic. Both exist so the project
  runs offline, and neither is a substitute for a real provider.
- **The in-process vector store is brute force** — O(n) per query, all vectors
  in RAM. Fine for the demo corpus; use Milvus for a real one.
- **No reranking, query rewriting, or hybrid search.** Retrieval is dense
  top-k. These are the obvious next improvements, not existing features.
- **No conversational memory.** Each question is answered independently; there
  is no follow-up context.
- **API auth is a single shared secret.** Adequate to keep a service off the
  open internet, not a substitute for an identity provider.
- **No evaluation harness.** There is no measurement of answer quality, so
  changes to chunking or prompts are not currently quantifiable.
- **Only the Milvus and in-memory backends are implemented**, though the
  interface is small enough that others are straightforward.

---

## Future improvements

- An evaluation harness (retrieval recall\@k, answer groundedness) so changes to
  chunking, prompts and top-k can be measured rather than guessed at.
- Hybrid retrieval: BM25 alongside dense vectors, fused by reciprocal rank.
- A cross-encoder reranking node — a natural fit for the pipeline registry.
- Query rewriting and decomposition for multi-part questions.
- Conversational memory with history-aware query reformulation.
- Incremental re-indexing keyed on content hashes, instead of whole-domain
  rebuilds.
- Caching for embeddings and identical queries.
- OpenTelemetry tracing across the retrieve/generate spans.
- More vector-store backends (pgvector, Qdrant, Chroma).

---

## Engineering concepts demonstrated

Retrieval-augmented generation · document chunking and provenance · vector
search over partitioned collections · prompt construction with explicit
grounding and a context budget · agent orchestration with LangGraph ·
ReAct-style tool calling · MCP-based extensibility with a tool registry ·
provider abstraction through registries and factories · configuration-first
design · OpenAI-compatible API design · SSE and NDJSON streaming · non-blocking
start-up with background work · retry, backoff and circuit-style reconnection ·
authentication and secure-by-default headers · offline-first testability ·
containerised deployment · CI with lint, tests and secret scanning.

---

## License

MIT — see [LICENSE](LICENSE).
