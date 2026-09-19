# Extending the system

Every extension point is a registry: a name mapped to a builder function.
Adding an implementation never requires editing the code that consumes it.

| Point | Registry | Selected by |
|---|---|---|
| Chat model | `src/llm/factory.py` | `LLM_PROVIDER` |
| Embeddings | `src/embeddings/factory.py` | `EMBEDDING_PROVIDER` |
| Vector store | `src/vectorstore/factory.py` | `VECTOR_STORE` |
| Pipeline | `src/rag/pipelines.py` | `RAG_PIPELINE` |
| MCP tool | `src/mcp_server/registry.py` | auto-registered on import |
| Knowledge source | `data/<domain>/` | filesystem |
| Prompts | `src/rag/prompts.py` | direct edit |

---

## Your own knowledge base

Each subdirectory of `DATA_DIR` becomes one searchable domain and one vector
collection.

```
data/
  my_service/
    overview.md
    runbook.md
    api-reference.md
  compliance/
    policy.pdf
    procedures.txt
```

```bash
python cli.py index
```

Supported: `.md`, `.txt`, `.pdf`. Folder names are normalised (lowercased,
non-alphanumerics to underscores), so `My Service` and `my-service` both become
`my_service`.

### Optional display metadata

`data/domains.json` supplies labels, groupings and suggested questions. Every
field is optional; a domain with no entry falls back to a title-cased folder
name, and domains not mentioned in any group appear under "Other".

```json
{
  "groups": [
    { "name": "Platform", "domains": ["my_service"] }
  ],
  "domains": {
    "my_service": {
      "label": "My Service",
      "description": "What this domain covers.",
      "faqs": ["How does X work?", "What happens when Y fails?"]
    }
  }
}
```

### Ad-hoc sources

`DocumentProcessor.process_sources` loads a mixed list of URLs and file paths:

```python
from src.ingestion.document_processor import DocumentProcessor
from src.vectorstore.factory import get_vector_store

chunks = DocumentProcessor().process_sources(
    [
        "https://example.com/handbook",
        "/path/to/spec.pdf",
    ]
)
get_vector_store().create_collection("ad_hoc", chunks)
```

### Source code

AST-aware ingestion for Java, YAML, JSON, Markdown, SQL and Gradle:

```bash
pip install -r requirements-code.txt
```

```python
from src.ingestion.code_repository import CodeRepositoryIngestion
from src.vectorstore.factory import get_vector_store

CodeRepositoryIngestion("/path/to/repo", "my_repo").ingest(get_vector_store())
```

Indexed as `repo_my_repo`. Java chunks carry class and method names in
metadata. To support another language, add a branch in `_process_file` with the
matching splitter.

---

## Your own LLM provider

For an OpenAI-compatible endpoint you do not need code — set `LLM_BASE_URL`.
For a different wire format, register a builder returning a LangChain
`BaseChatModel`:

```python
# src/llm/providers/anthropic.py
from langchain_anthropic import ChatAnthropic
from src.config.settings import LLMSettings
from src.llm.factory import register_llm


def build_anthropic(cfg: LLMSettings):
    if not cfg.api_key:
        raise ValueError("LLM_API_KEY is not set")
    return ChatAnthropic(
        api_key=cfg.api_key,
        model=cfg.model,
        temperature=cfg.temperature,
        max_tokens=cfg.max_tokens,
    )


register_llm("anthropic", build_anthropic)
```

Import the module once so registration runs — from `src/llm/__init__.py`, or
wherever your entry point sets up. Then:

```env
LLM_PROVIDER=anthropic
LLM_MODEL=<model-id>
LLM_API_KEY=...
```

Anything satisfying `BaseChatModel` works, including a local model wrapped in
the LangChain interface.

---

## Your own embedding provider

Implement LangChain's `Embeddings` (`embed_documents`, `embed_query`) and
register a builder:

```python
from langchain_core.embeddings import Embeddings
from src.config.settings import EmbeddingSettings
from src.embeddings.factory import register_embeddings


class SentenceTransformerEmbeddings(Embeddings):
    def __init__(self, model_name: str):
        from sentence_transformers import SentenceTransformer

        self.model = SentenceTransformer(model_name)

    def embed_documents(self, texts):
        return self.model.encode(texts, normalize_embeddings=True).tolist()

    def embed_query(self, text):
        return self.embed_documents([text])[0]


register_embeddings(
    "sentence-transformers",
    lambda cfg: SentenceTransformerEmbeddings(cfg.model),
)
```

Normalise your vectors if you plan to use the in-memory backend — it assumes
unit length for the fast path (and falls back to a full cosine if not).

> Re-index after switching embedders. Vectors from different models are not
> comparable.

---

## Your own vector store

Subclass `VectorStoreBackend` and implement the six single-domain methods. The
base class supplies fan-out retrieval, origin tagging and per-domain failure
isolation.

```python
from langchain_core.documents import Document
from src.vectorstore.base import VectorStoreBackend, normalize_domain
from src.vectorstore.factory import register_backend


class QdrantBackend(VectorStoreBackend):
    def __init__(self, embeddings, cfg):
        self.embeddings = embeddings
        self.prefix = cfg.vector_store.collection_prefix
        # ... connect ...

    def create_collection(self, domain, documents) -> bool: ...
    def has_collection(self, domain) -> bool: ...
    def drop_collection(self, domain) -> None: ...
    def list_domains(self) -> list[str]: ...
    def loaded_domains(self) -> list[str]: ...
    def load_domain(self, domain) -> bool: ...
    def similarity_search(self, query, domain, k) -> list[Document]: ...


register_backend("qdrant", lambda embeddings, cfg: QdrantBackend(embeddings, cfg))
```

Two conventions to honour:

- **Prefix your collection names** with `cfg.vector_store.collection_prefix`,
  so the project can share a cluster.
- **`list_domains` returns what exists in storage**; `loaded_domains` returns
  what this process is ready to query. They differ before `load_domain` runs.

---

## Your own pipeline

See [adding-workflows.md](adding-workflows.md).

## Your own MCP tool

See [adding-mcp-tools.md](adding-mcp-tools.md).

---

## Your own prompts

Prompts live in [`src/rag/prompts.py`](../src/rag/prompts.py) — deliberately in
code, not configuration, because a prompt change alters behaviour and belongs
in review.

- `ANSWER_PROMPT` — used by `simple_rag`. The grounding rules are the part that
  matters; keep an instruction to say when the context does not cover the
  question.
- `AGENT_SYSTEM_PROMPT` — used by `react_agent`.
- `format_context` — how chunks are rendered. It numbers and attributes them
  and enforces a character budget; keep the budget if you change the format.

---

## Your own API

To expose an endpoint the engine can serve, add a route to `api_app.py`:

```python
@app.post("/summarize")
async def summarize(payload: QueryInput, api_key: str = Depends(verify_api_key)):
    result = app.state.engine.run(
        {
            "question": f"Summarise what is known about: {payload.question}",
            "domains": payload.domains,
        }
    )
    return {"summary": result.get("answer", "")}
```

To call an external API *from* the system, add an MCP tool
([adding-mcp-tools.md](adding-mcp-tools.md)) or a pipeline node
([adding-workflows.md](adding-workflows.md)), depending on whether the caller
should be an external agent or the pipeline itself.

---

## Testing an extension

The suite runs offline. Reuse the fixtures in `tests/conftest.py`:

```python
def test_my_backend(embeddings, documents):
    backend = MyBackend(embeddings, settings)
    backend.create_collection("test_domain", documents)
    assert backend.retrieve("query", domains=["test_domain"], k=2)
```

```bash
make test
```

Keep it offline: mock the transport rather than contacting a real endpoint, as
`tests/test_embeddings.py` does.
