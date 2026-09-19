"""Persistence for shareable answer links.

A shared answer is a row with a UUID token. The token is the capability: anyone
holding the link can read that one answer and nothing else. There is no listing
endpoint and no enumeration — a random UUID is not guessable, and the share
page is served with ``noindex``.

Sharing is off unless ``CHAT_PERSISTENCE_ENABLED`` is true and a database is
configured.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from src.config.settings import settings

logger = logging.getLogger(__name__)

_pool: Any | None = None


async def _get_pool():
    global _pool
    if _pool is None:
        import asyncpg

        db = settings.database
        _pool = await asyncpg.create_pool(
            min_size=1,
            max_size=4,
            host=db.host,
            port=db.port,
            database=db.name,
            user=db.user,
            password=db.password,
        )
    return _pool


async def create_share(
    question: str,
    answer: str,
    domains: list[str] | None = None,
    created_by: str | None = None,
) -> str:
    """Persist an answer and return the token used to view it."""
    share_id = uuid.uuid4()
    pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO shared_answers ("id", "question", "answer", "domains", "createdBy")
            VALUES ($1, $2, $3, $4::jsonb, $5)
            """,
            share_id,
            question,
            answer,
            json.dumps(domains or []),
            created_by,
        )
    return share_id.hex


async def get_share(token: str) -> dict[str, Any] | None:
    """Look up a shared answer. Returns None for unknown or malformed tokens."""
    try:
        share_id = uuid.UUID(hex=token)
    except (ValueError, AttributeError, TypeError):
        return None

    pool = await _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT "id", "question", "answer", "domains", "createdBy", "createdAt"
            FROM shared_answers WHERE "id" = $1
            """,
            share_id,
        )

    if not row:
        return None

    domains = row["domains"]
    if isinstance(domains, str):
        try:
            domains = json.loads(domains)
        except json.JSONDecodeError:
            domains = []

    return {
        "id": row["id"].hex,
        "question": row["question"],
        "answer": row["answer"],
        "domains": domains or [],
        "created_by": row["createdBy"],
        "created_at": row["createdAt"],
    }
