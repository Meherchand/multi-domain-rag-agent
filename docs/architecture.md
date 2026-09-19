# Architecture

This document explains *why* the system is shaped the way it is. For how to
extend it, see [extending.md](extending.md).

---

## Layering

Four layers, each depending only on the one below it:

```
Interfaces   api_app.py · chainlit_app.py · streamlit_app.py · mcp_app.py · cli.py
Engine       src/rag/        pipelines, nodes, prompts, state
Providers    src/llm/  src/embeddings/  src/vectorstore/
Ingestion    src/ingestion/  +  data/
```

The rule that keeps this honest: **no interface imports a provider directly**.
The API does not know whether it is talking to Milvus or an in-process index,
and the engine does not know whether generation happens over HTTP or in the
same process. Everything is resolved by a factory reading configuration.

The practical consequence is demo mode. Because nothing above the provider
layer names a concrete implementation, swapping all three for offline stubs is
a configuration change of three lines in
[`Settings.resolved()`](../src/config/settings.py) — not a parallel code path
that can rot.

---

## Component responsibilities

### `src/config/settings.py`

The only module that reads `os.environ`. Everything else imports the frozen
`settings` object. This is a deliberate constraint: it means the complete set
of knobs a fork has to understand is one file, and it makes the
"no endpoint is hardcoded" property testable.

`Settings.resolved()` applies demo-mode overrides once, at import, rather than
scattering `if demo_mode` checks through the code.

### `src/llm/`, `src/embeddings/`, `src/vectorstore/`

Three registries with the same shape: a dict from provider name to builder
function, a `register_*` function, and a `get_*` that looks up the configured
name and raises a message listing the alternatives when it misses.

Each ships two implementations — one real, one offline — which is what proves
the abstraction is real rather than a single implementation with an interface
drawn around it.

### `src/vectorstore/base.py`

Holds both the interface and the shared fan-out logic. Subclasses implement
single-domain operations (`create_collection`, `similarity_search`, …); the
base class composes them into `retrieve`, which fans out, tags each hit with
its origin, and skips domains that fail.

That failure handling is the important part: one unavailable collection
degrades an answer, it does not fail a request.

### `src/rag/`

- `state.py` — the object threaded through the graph. Carrying an open
  `metadata` dict means a custom node can record whatever it needs without
  changing the state class.
- `nodes.py` — node functions, free of graph wiring, so they unit-test directly.
- `pipelines.py` — the registry mapping a name to a graph builder.
- `prompts.py` — prompt text and context rendering, isolated so prompts can be
  reviewed and diffed without reading orchestration code.
- `engine.py` — assembles a pipeline and exposes `run` / `stream`.

### `src/mcp_server/`

`registry.py` holds a decorator and a lazily-initialised `ToolContext`.
`server.py` binds everything in the registry onto `FastMCP` at import, using
`inspect` to hide the injected context parameter from the advertised schema.

Laziness matters here: an MCP client expects a handshake in milliseconds, and
building a vector store can take seconds.

---

## Request flow

### `POST /query`

```
client
  → FastAPI
      → verify_api_key            hmac.compare_digest over SHA-256 digests
      → QueryInput validation     length bounds, domain-count cap
      → RAGEngine.run
          → graph.invoke(RAGState)
              → retrieve node
                  → store.retrieve(question, domains, k)
                      → for each domain: similarity_search
                          → embeddings.embed_query
                          → vector search
                      → tag with retrieved_from, merge
              → generate node
                  → build_answer_prompt   numbered, attributed, budgeted
                  → llm.invoke
                  → attach citations
  ← { answer, citations, response_time }
```

### `POST /query/stream`

Retrieval runs to completion first — there is nothing to stream from it — then
the model is streamed directly rather than through `astream_events`. Going
straight to the model keeps the token path short and avoids coupling to
LangGraph's event schema, which is still evolving. The trade-off is that the
streaming path bypasses any custom nodes between retrieve and generate; a
pipeline with a rerank step should stream through `stream_events` instead.

NDJSON rather than SSE, because the consumers are ordinary HTTP clients, not
`EventSource`. One JSON object per line parses incrementally in both Python and
JavaScript with no framing library.

### `POST /v1/chat/completions`

The same engine behind an OpenAI-shaped envelope, so any client built for that
format works unchanged. Two accommodations:

- Clients that cannot send custom fields can scope with a `[DOMAIN:x]` message
  prefix. The explicit `domains` field wins when both are present.
- `usage` reports word counts, not tokens. The service does not tokenise, and
  a fabricated token count would be worse than an obviously approximate one.

---

## RAG flow

### Ingestion

```
data/<domain>/**  →  load by extension  →  attach provenance  →  split  →  embed  →  collection
```

Provenance (`domain`, `source_file`, `file_type`) is attached *before*
splitting, so every chunk inherits it and every answer can cite its source.

Chunking is recursive character splitting with overlap. Overlapping windows
lose less at boundaries than clean cuts, at the cost of index size;
`CHUNK_SIZE` and `CHUNK_OVERLAP` tune that trade-off.

### Retrieval

Scoped queries hit only the named collections. Unscoped queries fan out across
all of them and merge — correct, but linear in the number of domains, which is
why the interface makes scoping explicit.

### Generation

`format_context` renders chunks numbered and attributed, enforcing a character
budget so no prompt can silently exceed a context window. The prompt's
grounding instruction is the main defence against the failure mode that matters
for a documentation assistant: a confident answer assembled from the model's
priors rather than from the corpus.

---

## Agent flow (`react_agent`)

```
question → agent
             ├─ search_knowledge_base(query)  ──┐
             ├─ search_knowledge_base(query')   │  as many times as it decides
             └─ final answer  ←─────────────────┘
```

Retrieval is exposed as a tool with the request's domain scope closed over: the
agent chooses *what* to search for, never *where*. Scoping stays a property of
the request rather than something the model can widen.

Versus `simple_rag`: better on multi-part questions that one retrieval pass
answers badly; worse on determinism, latency and cost. `simple_rag` is the
default for that reason.

---

## MCP flow

```
MCP client
  → FastMCP (stdio | SSE)
      → bound handler
          → ToolContext        lazily builds store + engine on first call
          → tool function
  ← structured result
```

Tools return plain dicts and lists rather than prose, and return structured
errors rather than raising — a calling agent can branch on a dict, but can only
guess at an exception.

Two levels of abstraction are exposed deliberately: `query_knowledge_base` runs
the pipeline and returns an answer, while `search_documents` returns raw chunks
for a client that has a model of its own.

---

## Start-up and indexing

`INIT_MODE` controls how the index comes up:

| Mode | Behaviour |
|---|---|
| `retrieve` | Attach to existing collections only. Never reads the corpus. |
| `auto` | Attach, then index only the domains that are missing. *(default)* |
| `index` | Re-index every domain, dropping what was there. |

The API always attaches with `retrieve` first and defers indexing to a
background thread, so start-up returns immediately. `/health` reports
`indexing` while that thread runs.

This keeps a container's readiness probe honest: the service really is up and
answering from existing collections while the corpus is still being built. The
alternative — blocking start-up on a full re-index — makes a deploy look hung
and trips orchestrator timeouts.

---

## Failure handling

| Failure | Response | Why |
|---|---|---|
| Embedding endpoint 429/5xx | Retry 3× with backoff on a pooled session | Bulk indexing is exactly when rate limits bite |
| Embedding endpoint hard failure | `RuntimeError`, log the exception type only | Endpoint, credential and input text stay out of logs |
| Vector store connection drops | Reconnect and retry once | Survives an idle timeout without hiding a real outage |
| One domain fails during retrieval | Log, skip, continue | Degrade the answer, do not fail the request |
| No documents retrieved | Generate anyway; the prompt tells the model to say so | A truthful "not covered" beats a 500 |
| Background indexing fails | Log, flag cleared, service keeps serving | Existing collections still answer |
| Database unreachable | Chat history and sharing disabled, assistant still works | Persistence is optional |
| tree-sitter missing | Code ingestion proceeds without metadata extraction | A nice-to-have should not be a hard dependency |

The pattern throughout: **an optional component failing must not take a
required one with it.**

---

## Configuration boundaries

| Belongs in the environment | Belongs in the repository |
|---|---|
| Endpoints, credentials, model names | Prompt text |
| Vector-store URI, collection prefix | Chunking strategy |
| Chunk size, overlap, top-k | Graph topology |
| Corpus directory, init mode | Tool schemas |
| API key, CORS origins, ports | Domain metadata (`data/domains.json`) |

The line is: anything that differs between two deployments of the *same* code
is configuration. Anything that changes the system's behaviour as a program is
code, and belongs in review.

---

## Extension points

Summarised in [extending.md](extending.md). Each is a registry keyed by name,
so adding an implementation never requires editing its consumer.

| Point | Registry |
|---|---|
| Chat model | `src/llm/factory.py` |
| Embeddings | `src/embeddings/factory.py` |
| Vector store | `src/vectorstore/factory.py` |
| Pipeline | `src/rag/pipelines.py` |
| MCP tool | `src/mcp_server/registry.py` |
