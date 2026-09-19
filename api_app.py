"""HTTP service for the RAG engine.

Two surfaces, deliberately:

* a **native** API (``/query``, ``/query/stream``, ``/domains``) that exposes
  domain scoping and returns citations;
* an **OpenAI-compatible** API (``/v1/chat/completions``, ``/v1/models``) so
  that any client built for the OpenAI wire format — a chat UI, an SDK, an
  agent framework — can point at this service unchanged.

Start-up attaches to whatever is already indexed and returns immediately;
indexing runs on a background thread and is reported through ``/health``. That
keeps a container's readiness probe honest: the service is up and answering
from existing collections while the corpus is still being built.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import threading
from datetime import UTC, datetime

from fastapi import Depends, FastAPI, HTTPException, Request, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field, field_validator

from src.config.settings import settings
from src.rag.engine import RAGEngine
from src.vectorstore.factory import build_knowledge_base, discover_domains, index_domains

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------


class QueryInput(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    domains: list[str] | None = Field(None, max_length=20)

    @field_validator("question")
    @classmethod
    def strip_question(cls, v: str) -> str:
        return v.strip()


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: str | None = None
    messages: list[ChatMessage]
    temperature: float | None = None
    max_tokens: int | None = None
    stream: bool = False
    domains: list[str] | None = None


class IngestRepositoryInput(BaseModel):
    """Index a local source repository as a knowledge domain.

    Disabled unless ``REPO_INGEST_ROOT`` is set: the endpoint reads from the
    server's filesystem, so the operator has to name the one directory it is
    allowed to read from.
    """

    repo_path: str = Field(min_length=1, max_length=500)
    domain_name: str | None = Field(None, max_length=100)


# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------


def verify_api_key(api_key: str = Security(api_key_header)) -> str:
    """Shared-secret auth.

    A single static key is the simplest thing that keeps the service off the
    open internet, and it is all this reference implementation ships. A real
    deployment should put an API gateway or an OIDC-aware proxy in front and
    treat this as a backstop — see ``docs/security.md``.
    """
    expected = settings.api.api_key
    if not expected:
        raise HTTPException(status_code=500, detail="Service authentication is not configured")
    if not api_key or not hmac.compare_digest(
        hashlib.sha256(api_key.encode()).digest(),
        hashlib.sha256(expected.encode()).digest(),
    ):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    return api_key


# --------------------------------------------------------------------------
# App
# --------------------------------------------------------------------------

app = FastAPI(
    title="Multi-Domain RAG Agent API",
    version="1.0.0",
    docs_url="/docs" if settings.api.environment != "production" else None,
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    # Defaults to the local UI only. Widen deliberately via CORS_ALLOW_ORIGINS.
    allow_origins=settings.api.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-API-Key"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def _now() -> str:
    return datetime.now(UTC).isoformat()


@app.on_event("startup")
async def startup() -> None:
    logger.info(
        "Starting up (demo_mode=%s, pipeline=%s, vector_store=%s)",
        settings.demo_mode,
        settings.pipeline,
        settings.vector_store.backend,
    )
    app.state.indexing = False

    # Attach only; indexing is deferred so the service becomes available fast.
    store = build_knowledge_base(init_mode="retrieve")
    app.state.store = store
    app.state.engine = RAGEngine(store=store)
    app.state.model_id = settings.api.model_id

    if settings.init_mode in {"auto", "index"}:
        _start_background_indexing(store)

    logger.info("Ready with %d knowledge domain(s)", len(store.loaded_domains()))


def _start_background_indexing(store) -> None:
    def worker() -> None:
        app.state.indexing = True
        try:
            on_disk = discover_domains(settings.data_dir)
            targets = (
                on_disk
                if settings.init_mode == "index"
                else [d for d in on_disk if d not in store.loaded_domains()]
            )
            if targets:
                logger.info("Background indexing started for %d domain(s)", len(targets))
                index_domains(store, targets)
            logger.info("Background indexing complete: %d domain(s) online", len(store.loaded_domains()))
        except Exception:
            logger.exception("Background indexing failed")
        finally:
            app.state.indexing = False

    threading.Thread(target=worker, daemon=True, name="indexing-worker").start()


# --------------------------------------------------------------------------
# Public routes
# --------------------------------------------------------------------------


@app.get("/")
async def root():
    return {"name": "Multi-Domain RAG Agent API", "version": "1.0.0", "docs": "/docs"}


@app.get("/health")
async def health():
    indexing = getattr(app.state, "indexing", False)
    return {
        "status": "indexing" if indexing else "healthy",
        "domains": len(app.state.store.loaded_domains()) if hasattr(app.state, "store") else 0,
        "demo_mode": settings.demo_mode,
        "timestamp": _now(),
    }


# --------------------------------------------------------------------------
# Native API
# --------------------------------------------------------------------------


@app.get("/domains")
async def list_domains(api_key: str = Depends(verify_api_key)):
    domains = app.state.store.loaded_domains()
    return {"domains": domains, "count": len(domains)}


@app.post("/query")
async def query(payload: QueryInput, api_key: str = Depends(verify_api_key)):
    started = datetime.now(UTC)
    try:
        result = app.state.engine.run({"question": payload.question, "domains": payload.domains})
    except Exception as exc:
        logger.exception("Query failed")
        raise HTTPException(status_code=500, detail="Query processing failed") from exc

    return {
        "answer": result.get("answer", ""),
        "citations": (result.get("metadata") or {}).get("citations", []),
        "response_time": (datetime.now(UTC) - started).total_seconds(),
        "timestamp": _now(),
    }


@app.post("/query/stream")
async def query_stream(payload: QueryInput, api_key: str = Depends(verify_api_key)):
    """Stream an answer as newline-delimited JSON.

    NDJSON rather than SSE because the clients here are ordinary HTTP clients,
    not ``EventSource``; one JSON object per line is trivial to parse
    incrementally in Python and JavaScript alike.
    """
    if payload.domains:
        known = app.state.store.loaded_domains()
        unknown = [d for d in payload.domains if d not in known]
        if unknown:
            raise HTTPException(status_code=400, detail=f"Unknown domain(s): {', '.join(unknown)}")

    async def generate():
        try:
            async for token in app.state.engine.stream(payload.question, domains=payload.domains):
                yield json.dumps({"chunk": token, "done": False}) + "\n"
            yield json.dumps({"chunk": "", "done": True, "timestamp": _now()}) + "\n"
        except Exception:
            logger.exception("Streaming failed")
            yield json.dumps({"error": "Processing failed", "done": True}) + "\n"

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/ingest/repository")
async def ingest_repository(payload: IngestRepositoryInput, api_key: str = Depends(verify_api_key)):
    from pathlib import Path

    root = settings.repo_ingest_root
    if not root:
        raise HTTPException(
            status_code=403,
            detail="Repository ingestion is disabled. Set REPO_INGEST_ROOT to enable it.",
        )

    candidate = Path(payload.repo_path).resolve()
    allowed_root = Path(root).resolve()
    try:
        candidate.relative_to(allowed_root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Path is outside REPO_INGEST_ROOT") from exc
    if not candidate.is_dir():
        raise HTTPException(status_code=400, detail="Path is not a directory")

    try:
        from src.ingestion.code_repository import CodeRepositoryIngestion

        ingestion = CodeRepositoryIngestion(str(candidate), payload.domain_name)
        ok = ingestion.ingest(app.state.store)
    except ImportError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Repository ingestion failed")
        raise HTTPException(status_code=500, detail="Repository ingestion failed") from exc

    if not ok:
        raise HTTPException(status_code=500, detail="Repository ingestion produced no documents")
    return {"status": "success", "domain": ingestion.domain_name, "timestamp": _now()}


# --------------------------------------------------------------------------
# OpenAI-compatible API
# --------------------------------------------------------------------------


@app.get("/v1/models")
async def openai_models():
    model_id = getattr(app.state, "model_id", settings.api.model_id)
    return {
        "object": "list",
        "data": [
            {
                "id": model_id,
                "object": "model",
                "created": int(datetime.now(UTC).timestamp()),
                "owned_by": "multi-domain-rag-agent",
            }
        ],
    }


@app.get("/v1/domains")
async def openai_domains():
    """Unauthenticated domain list, for UIs that populate a picker before login."""
    domains = app.state.store.loaded_domains()
    return {
        "object": "list",
        "data": [{"id": d, "name": d.replace("_", " ").title()} for d in domains],
    }


@app.post("/v1/chat/completions")
async def openai_chat_completions(request: ChatCompletionRequest, api_key: str = Depends(verify_api_key)):
    user_message = next((m.content for m in reversed(request.messages) if m.role == "user"), None)
    if not user_message:
        raise HTTPException(status_code=400, detail="No user message found")

    # Clients that cannot send custom fields can scope with a "[DOMAIN:x] ..."
    # prefix instead. The explicit `domains` field wins when both are present.
    domains = request.domains
    if user_message.startswith("[DOMAIN:") and "]" in user_message:
        prefix, rest = user_message.split("]", 1)
        domains = domains or [prefix.removeprefix("[DOMAIN:").strip()]
        user_message = rest.strip()

    model_id = getattr(app.state, "model_id", settings.api.model_id)
    completion_id = f"chatcmpl-{int(datetime.now(UTC).timestamp() * 1000)}"
    created = int(datetime.now(UTC).timestamp())

    if request.stream:

        async def sse():
            try:
                async for token in app.state.engine.stream(user_message, domains=domains):
                    chunk = {
                        "id": completion_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model_id,
                        "choices": [{"index": 0, "delta": {"content": token}, "finish_reason": None}],
                    }
                    yield f"data: {json.dumps(chunk)}\n\n"
                    await asyncio.sleep(0)
                done = {
                    "id": completion_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model_id,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                }
                yield f"data: {json.dumps(done)}\n\n"
                yield "data: [DONE]\n\n"
            except Exception:
                logger.exception("Chat completion streaming failed")
                yield "data: [DONE]\n\n"

        return StreamingResponse(sse(), media_type="text/event-stream")

    try:
        result = app.state.engine.run({"question": user_message, "domains": domains})
    except Exception as exc:
        logger.exception("Chat completion failed")
        raise HTTPException(status_code=500, detail="Chat completion failed") from exc

    answer = result.get("answer", "")
    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": created,
        "model": model_id,
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": answer}, "finish_reason": "stop"}
        ],
        # Word counts, not tokens: this service does not tokenise, and reporting
        # a fabricated token count would be worse than an obvious approximation.
        "usage": {
            "prompt_tokens": len(user_message.split()),
            "completion_tokens": len(answer.split()),
            "total_tokens": len(user_message.split()) + len(answer.split()),
        },
    }
