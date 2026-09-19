"""Schema bootstrap for the chat-persistence database.

Applies ``init.sql`` if a database is configured. A connection failure is
logged and swallowed: chat history and share links are optional features, and
the assistant should still answer questions without them.
"""

from __future__ import annotations

import logging
from pathlib import Path

from src.config.settings import PROJECT_ROOT, settings

logger = logging.getLogger(__name__)


def initialize_database() -> bool:
    """Apply the schema. Returns True when the database is ready to use."""
    if not settings.database.enabled:
        logger.info("Chat persistence disabled; skipping database initialisation")
        return False

    try:
        import psycopg2
    except ImportError:
        logger.warning("psycopg2 is not installed; chat persistence unavailable")
        return False

    schema_path = Path(PROJECT_ROOT) / "init.sql"
    if not schema_path.is_file():
        schema_path = Path("/app/init.sql")
    if not schema_path.is_file():
        logger.warning("init.sql not found; skipping database initialisation")
        return False

    db = settings.database
    try:
        conn = psycopg2.connect(
            host=db.host, port=db.port, database=db.name, user=db.user, password=db.password
        )
    except psycopg2.OperationalError as exc:
        logger.warning(
            "Could not connect to the configured chat database (%s); continuing without chat history",
            type(exc).__name__,
        )
        return False

    try:
        with conn, conn.cursor() as cursor:
            cursor.execute(schema_path.read_text(encoding="utf-8"))
        logger.info("Chat database schema is up to date")
        return True
    except Exception:
        logger.exception("Failed to apply the database schema")
        return False
    finally:
        conn.close()
