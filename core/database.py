"""Async SQLite database layer for the OSINT DD Dashboard.

Provides connection management, schema migration, and low-level query
helpers.  Uses ``aiosqlite`` for async compatibility with FastAPI.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional

import aiosqlite

from core.config import settings
from core.logger import get_logger

logger = get_logger(__name__)

# Connection pool — shared across the application
_pool: Optional[aiosqlite.Connection] = None
_pool_lock = asyncio.Lock()

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
-- Screening jobs table
CREATE TABLE IF NOT EXISTS screening_jobs (
    id            TEXT PRIMARY KEY,
    name_en       TEXT NOT NULL,
    name_he       TEXT,
    status        TEXT NOT NULL DEFAULT 'pending',
    created_at    TEXT,
    started_at    TEXT,
    completed_at  TEXT,
    error_message TEXT,
    report_md_path TEXT,
    report_json_path TEXT,
    exit_code     INTEGER,
    triage_status TEXT DEFAULT 'clear',
    flagged_modules TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON screening_jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON screening_jobs(created_at DESC);

-- Triage items table
CREATE TABLE IF NOT EXISTS triage_items (
    id            TEXT PRIMARY KEY,
    job_id        TEXT NOT NULL REFERENCES screening_jobs(id) ON DELETE CASCADE,
    module        TEXT NOT NULL,
    severity      TEXT NOT NULL DEFAULT 'medium',
    title         TEXT NOT NULL,
    description   TEXT NOT NULL,
    raw_data      TEXT,
    created_at    TEXT,
    status        TEXT NOT NULL DEFAULT 'open',
    assigned_to   TEXT,
    notes         TEXT
);

CREATE INDEX IF NOT EXISTS idx_triage_job   ON triage_items(job_id);
CREATE INDEX IF NOT EXISTS idx_triage_status ON triage_items(status);
"""

# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------


async def _get_connection() -> aiosqlite.Connection:
    """Return (or create) the singleton database connection."""
    global _pool
    if _pool is None or _pool._running is False:
        async with _pool_lock:
            if _pool is None or _pool._running is False:
                db_path = settings.DATA_DIR / "osint_dd.db"
                db_path.parent.mkdir(parents=True, exist_ok=True)
                _pool = await aiosqlite.connect(db_path)
                _pool.row_factory = aiosqlite.Row
                logger.debug("Database connection established: %s", db_path)
    return _pool


@asynccontextmanager
async def get_db() -> AsyncGenerator[aiosqlite.Connection, None]:
    """Async context manager yielding a database connection."""
    db = await _get_connection()
    try:
        yield db
    except Exception:
        await db.rollback()
        raise


async def close_pool() -> None:
    """Close the database connection pool."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.debug("Database connection closed")


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------


async def init_db() -> None:
    """Create tables and indexes if they do not exist."""
    async with get_db() as db:
        await db.executescript(SCHEMA_SQL)
        await db.commit()
    logger.info("Database schema initialised")


# ---------------------------------------------------------------------------
# Low-level query helpers
# ---------------------------------------------------------------------------


async def execute_query(
    sql: str,
    parameters: Optional[tuple] = None,
) -> List[Dict[str, Any]]:
    """Execute a SELECT query and return results as dicts."""
    async with get_db() as db:
        async with db.execute(sql, parameters or ()) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


async def execute_write(
    sql: str,
    parameters: Optional[tuple] = None,
) -> None:
    """Execute an INSERT, UPDATE, or DELETE statement."""
    async with get_db() as db:
        await db.execute(sql, parameters or ())
        await db.commit()
