# Configuration

Everything is environment-based. Copy `.env.example` to `.env` and edit; the
file is gitignored.

```bash
cp .env.example .env
```

All variables are read in [`src/config/settings.py`](../src/config/settings.py)
and nowhere else. No endpoint has a hardcoded default in the source — a test
asserts it.

---

## Demo mode

```env
DEMO_MODE=true
```

Overrides three providers at once, regardless of what else is configured:

| Provider | Demo value | What it does |
|---|---|---|
| `LLM_PROVIDER` | `echo` | Returns retrieved passages. No inference, no network. |
| `EMBEDDING_PROVIDER` | `hashing` | Deterministic lexical vectors, computed locally. |
| `VECTOR_STORE` | `memory` | In-process brute-force index. |

The point is that the repository is runnable and reviewable by someone with no
credentials. It is not a lightweight production mode — see
[Limitations](../README.md#limitations).

Set `DEMO_MODE=false` to use the providers below.

---

## Chat model

```env
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o-mini
LLM_BASE_URL=
LLM_API_KEY=
LLM_TEMPERATURE=0.2
LLM_TOP_P=0.9
LLM_MAX_TOKENS=1500
LLM_TIMEOUT=120
```

`openai` means *the OpenAI wire format*, not the hosted service. Anything
speaking chat-completions works; `LLM_BASE_URL` is what distinguishes them.

| Target | `LLM_BASE_URL` | Notes |
|---|---|---|
| Hosted OpenAI | *(empty)* | Uses the SDK default |
| Ollama | `http://localhost:11434/v1` | Any non-empty `LLM_API_KEY` |
| vLLM | `http://localhost:8001/v1` | |
| A gateway or proxy | its base URL | |

Low `LLM_TEMPERATURE` is deliberate: for a documentation assistant, faithfulness
to the retrieved text matters more than variety.

For a provider with a different wire format, register a builder — see
[extending.md](extending.md).

---

## Embeddings

```env
EMBEDDING_PROVIDER=openai
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_BASE_URL=
EMBEDDING_API_KEY=
EMBEDDING_DIMENSIONS=384
EMBEDDING_BATCH_SIZE=8
EMBEDDING_MAX_CHARS=6000
EMBEDDING_TIMEOUT=120
```

`EMBEDDING_BASE_URL` accepts either a base (`http://host/v1`) or a full
endpoint (`http://host/v1/embeddings`); both normalise to the same thing.

- `EMBEDDING_DIMENSIONS` applies to the `hashing` provider. For `openai` the
  model decides.
- `EMBEDDING_BATCH_SIZE` — raise it to index faster, lower it if the provider
  rejects large batches.
- `EMBEDDING_MAX_CHARS` — inputs are truncated to this, so one oversized chunk
  cannot fail a whole batch.

> **Changing the embedding model invalidates the index.** Vectors from
> different models are not comparable. Re-index with `INIT_MODE=index`.

---

## Vector store

```env
VECTOR_STORE=milvus
VECTOR_STORE_URI=http://localhost:19530
VECTOR_STORE_USER=
VECTOR_STORE_PASSWORD=
VECTOR_STORE_DB=default
VECTOR_COLLECTION_PREFIX=kb_
```

| Backend | Use |
|---|---|
| `milvus` | A real corpus. Local, self-hosted or managed. |
| `memory` | Demo and tests. O(n) per query, all vectors in RAM. |

Credentials are optional — a locally started Milvus has auth disabled.

`VECTOR_COLLECTION_PREFIX` namespaces this project's collections, so several
applications can share one Milvus instance. Collections are named
`<prefix><domain>`.

A local Milvus is bundled:

```bash
docker compose --profile vectordb up -d milvus
```

---

## Corpus and indexing

```env
DATA_DIR=data
INIT_MODE=auto
CHUNK_SIZE=900
CHUNK_OVERLAP=120
RETRIEVAL_TOP_K=5
MAX_QUERY_CHARS=1000
```

| `INIT_MODE` | Behaviour |
|---|---|
| `retrieve` | Attach to existing collections only. Fastest start-up. |
| `auto` | Attach, then index missing domains. *(default)* |
| `index` | Re-index everything. Use after changing chunking or the embedder. |

Tuning guidance:

- **`CHUNK_SIZE`** — larger chunks carry more context per hit but dilute
  similarity, so retrieval gets less precise. Smaller chunks retrieve
  precisely but may not contain the whole answer. 600–1200 is a reasonable
  range for prose.
- **`CHUNK_OVERLAP`** — roughly 10–15% of chunk size. Guards against an answer
  being cut in half by a boundary.
- **`RETRIEVAL_TOP_K`** — per domain, so an unscoped query over five domains
  retrieves up to `5 × top_k` chunks. Raise it for recall, lower it for
  precision and prompt cost.

---

## Pipeline

```env
RAG_PIPELINE=simple_rag
```

| Pipeline | Shape | Trade-off |
|---|---|---|
| `simple_rag` | `retrieve → generate` | One model call, predictable latency. The default. |
| `react_agent` | Agent calls retrieval as a tool | Handles multi-part questions; non-deterministic step count, higher latency and cost. |

Add your own: [adding-workflows.md](adding-workflows.md).

---

## API service

```env
API_KEY=
API_BASE_URL=http://localhost:8000
API_MODEL_ID=multi-domain-rag
ENVIRONMENT=development
CORS_ALLOW_ORIGINS=http://localhost:8502
PUBLIC_BASE_URL=http://localhost:8502
```

- **`API_KEY`** — required on every endpoint except `/health` and `/v1/models`.
  Generate one with:
  ```bash
  python -c "import secrets; print(secrets.token_urlsafe(32))"
  ```
- **`API_BASE_URL`** — where the *UIs* find the API, not where the API binds.
- **`ENVIRONMENT=production`** disables `/docs`.
- **`CORS_ALLOW_ORIGINS`** — comma-separated. Defaults to the local UI only;
  widen deliberately.

---

## UI

```env
APP_NAME=Knowledge Assistant
```

Domain labels, groupings and suggested questions come from
`data/domains.json`, not the environment — they belong with the corpus. Every
field is optional.

---

## Chat persistence (optional)

Enables chat history and shareable answer links. Needs Postgres.

```env
CHAT_PERSISTENCE_ENABLED=true
DB_HOST=localhost
DB_PORT=5432
DB_NAME=ragagent
DB_USERNAME=ragagent
DB_PASSWORD=
CHAINLIT_AUTH_SECRET=
```

```bash
docker compose --profile postgres up -d postgres
```

The schema in `init.sql` is applied automatically at start-up. If the database
is unreachable, the assistant still answers questions — only history and
sharing are disabled.

---

## UI authentication (optional)

```env
OAUTH_GOOGLE_CLIENT_ID=
OAUTH_GOOGLE_CLIENT_SECRET=
```

Leave empty to run anonymously, which is what you want locally. Supplying a
client ID enables the sign-in flow. The callback accepts any account the
provider authenticates — restricting *which* accounts is deployment policy;
add the check in `chainlit_app.py`. See [security.md](security.md).

---

## Source-code ingestion (optional)

```env
REPO_INGEST_ROOT=
```

`POST /ingest/repository` reads from the server's filesystem, so it is disabled
until an operator names the one directory it may read. Paths are then checked
to resolve inside that root.

Needs the optional extra:

```bash
pip install -r requirements-code.txt
```

---

## Precedence

1. Process environment
2. `.env`
3. Defaults in `settings.py`
4. `DEMO_MODE=true` overrides the three provider selections last

So `DEMO_MODE=true python cli.py ask "..."` works regardless of what `.env`
says about providers.
