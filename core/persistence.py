"""Persistence helpers for the OSINT DD Dashboard.

Provides an async database connection pool and high-level CRUD helpers
for jobs, triage items, and buffered logs.  Also includes ``state`` —
the global singleton used by the REST router and WebSocket handler.

Uses aiosqlite for async SQLite and manages connection lifecycle
carefully to avoid "closed connection" errors.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiosqlite
from fastapi import WebSocket

from core.config import settings
from core.database import get_db, init_db
from core.logger import get_logger
from core.models import ScreeningJob, TriageItem

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Type helpers
# ---------------------------------------------------------------------------

# job_id  → ScreeningJob
_JobCache = Dict[str, ScreeningJob]
# job_id  → list of log lines (oldest first)
_LogBuffer = Dict[str, List[str]]
# job_id  → set of active WebSocket connections
_WSConnections = Dict[str, set[WebSocket]]
# item_id → TriageItem
_TriageCache = Dict[str, TriageItem]

# ---------------------------------------------------------------------------
# Global caches (module-level for singleton)
# ---------------------------------------------------------------------------

_job_cache: _JobCache = {}
_log_buffer: _LogBuffer = defaultdict(list)
_ws_connections: _WSConnections = defaultdict(set)
_triage_cache: _TriageCache = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_job(row: aiosqlite.Row) -> ScreeningJob:
    """Convert a DB row to a ScreeningJob model."""
    flagged_modules_raw = row["flagged_modules"]
    flagged_modules = flagged_modules_raw.split(",") if flagged_modules_raw else []
    return ScreeningJob(
        id=row["id"],
        name_en=row["name_en"],
        name_he=row["name_he"],
        status=row["status"],
        created_at=_parse_dt(row["created_at"]),
        started_at=_parse_dt(row["started_at"]),
        completed_at=_parse_dt(row["completed_at"]),
        error_message=row["error_message"],
        report_md_path=row["report_md_path"],
        report_json_path=row["report_json_path"],
        exit_code=row["exit_code"],
        triage_status=row["triage_status"] or "clear",
        flagged_modules=flagged_modules,
    )


def _row_to_triage(row: aiosqlite.Row) -> TriageItem:
    """Convert a DB row to a TriageItem model."""
    raw_data = None
    if row["raw_data"]:
        try:
            raw_data = json.loads(row["raw_data"])
        except json.JSONDecodeError:
            raw_data = {"raw": row["raw_data"]}
    return TriageItem(
        id=row["id"],
        job_id=row["job_id"],
        module=row["module"],
        severity=row["severity"],
        title=row["title"],
        description=row["description"],
        raw_data=raw_data,
        created_at=_parse_dt(row["created_at"]),
        status=row["status"],
        assigned_to=row["assigned_to"],
        notes=row["notes"],
    )


def _parse_dt(value: Any) -> Optional[datetime]:
    """Parse an ISO-8601 datetime string from the database."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    # Handle 'Z' suffix and no-Z formats
    value_str = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(value_str)
    except ValueError:
        return None


def _job_to_row(job: ScreeningJob) -> Tuple:
    """Convert a ScreeningJob to a DB row tuple."""
    return (
        job.id,
        job.name_en,
        job.name_he,
        job.status,
        _fmt_dt(job.created_at),
        _fmt_dt(job.started_at),
        _fmt_dt(job.completed_at),
        job.error_message,
        job.report_md_path,
        job.report_json_path,
        job.exit_code,
        job.triage_status,
        ",".join(job.flagged_modules) if job.flagged_modules else None,
    )


def _fmt_dt(dt: Optional[datetime]) -> Optional[str]:
    """Format a datetime as ISO-8601 string for the database."""
    return dt.isoformat() if dt else None


# ---------------------------------------------------------------------------
# State class
# ---------------------------------------------------------------------------


class State:
    """Shared application state with database-backed persistence.

    All mutations go through ``_persist_*`` helpers that write to SQLite
    and then update the in-memory cache for fast reads.
    """

    # --- Lifecycle --------------------------------------------------------

    async def init(self) -> None:
        """Initialise the database and hydrate caches."""
        await init_db()
        await self._hydrate_jobs()
        await self._hydrate_triage()

    async def _hydrate_jobs(self) -> None:
        """Load all jobs from DB into the in-memory cache."""
        async with get_db() as db:
            async with db.execute(
                "SELECT * FROM screening_jobs ORDER BY created_at DESC"
            ) as cursor:
                rows = await cursor.fetchall()
        for row in rows:
            job = _row_to_job(row)
            _job_cache[job.id] = job

    async def _hydrate_triage(self) -> None:
        """Load all triage items from DB into the in-memory cache."""
        async with get_db() as db:
            async with db.execute(
                "SELECT * FROM triage_items ORDER BY created_at DESC"
            ) as cursor:
                rows = await cursor.fetchall()
        for row in rows:
            item = _row_to_triage(row)
            _triage_cache[item.id] = item

    # --- Job operations ---------------------------------------------------

    async def create_job(self, job: ScreeningJob) -> None:
        """Insert a new job into DB and cache."""
        row = _job_to_row(job)
        async with get_db() as db:
            await db.execute(
                """
                INSERT INTO screening_jobs (id, name_en, name_he, status, created_at,
                    started_at, completed_at, error_message, report_md_path,
                    report_json_path, exit_code, triage_status, flagged_modules)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                row,
            )
            await db.commit()
        _job_cache[job.id] = job

    async def update_job(self, job: ScreeningJob) -> None:
        """Update an existing job in DB and cache."""
        async with get_db() as db:
            await db.execute(
                """
                UPDATE screening_jobs
                SET status = ?, started_at = ?, completed_at = ?,
                    error_message = ?, report_md_path = ?, report_json_path = ?,
                    exit_code = ?, triage_status = ?, flagged_modules = ?
                WHERE id = ?
                """,
                (
                    job.status,
                    _fmt_dt(job.started_at),
                    _fmt_dt(job.completed_at),
                    job.error_message,
                    job.report_md_path,
                    job.report_json_path,
                    job.exit_code,
                    job.triage_status,
                    ",".join(job.flagged_modules) if job.flagged_modules else None,
                    job.id,
                ),
            )
            await db.commit()
        _job_cache[job.id] = job

    async def get_job(self, job_id: str) -> Optional[ScreeningJob]:
        """Fetch a job by ID from cache (falls back to DB)."""
        if job_id in _job_cache:
            return _job_cache[job_id]
        # Fallback to DB
        async with get_db() as db:
            async with db.execute(
                "SELECT * FROM screening_jobs WHERE id = ?", (job_id,)
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            return None
        job = _row_to_job(row)
        _job_cache[job_id] = job
        return job

    async def list_jobs(
        self,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[ScreeningJob]:
        """List jobs, optionally filtered by status.

        Results are ordered by creation time (newest first).
        """
        jobs = list(_job_cache.values())
        if status:
            jobs = [j for j in jobs if j.status == status]
        jobs.sort(
            key=lambda j: j.created_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        return jobs[offset : offset + limit]

    async def delete_job(self, job_id: str) -> None:
        """Remove a job and its associated triage items from DB and cache."""
        async with get_db() as db:
            await db.execute("DELETE FROM screening_jobs WHERE id = ?", (job_id,))
            await db.execute("DELETE FROM triage_items WHERE job_id = ?", (job_id,))
            await db.commit()
        _job_cache.pop(job_id, None)
        _log_buffer.pop(job_id, None)
        _ws_connections.pop(job_id, None)
        # Remove associated triage items from cache
        for item_id, item in list(_triage_cache.items()):
            if item.job_id == job_id:
                _triage_cache.pop(item_id, None)

    # --- Recovery ---------------------------------------------------------

    async def recovery_scan(self) -> List[ScreeningJob]:
        """Mark orphaned running jobs as failed.

        Called on startup to recover from a previous crash.
        """
        orphaned = []
        for job in list(_job_cache.values()):
            if job.status == "running":
                job.status = "failed"
                job.completed_at = datetime.now(timezone.utc)
                job.error_message = "Server restarted while job was running"
                await self.update_job(job)
                orphaned.append(job)
        return orphaned

    # --- Log buffering ----------------------------------------------------

    async def append_log(self, job_id: str, line: str) -> None:
        """Append a log line to the buffer and broadcast to all WebSockets."""
        _log_buffer[job_id].append(line)
        # Broadcast to all connected WebSockets for this job
        dead_sockets = set()
        for ws in list(_ws_connections.get(job_id, set())):
            try:
                await ws.send_text(line)
            except Exception:
                dead_sockets.add(ws)
        # Clean up dead sockets
        if dead_sockets and job_id in _ws_connections:
            _ws_connections[job_id] -= dead_sockets

    async def get_buffered_logs(self, job_id: str) -> List[str]:
        """Return all buffered log lines for a job (oldest first)."""
        return list(_log_buffer.get(job_id, []))

    # --- WebSocket connection management ----------------------------------

    async def register_ws(self, job_id: str, ws: WebSocket) -> None:
        """Register a WebSocket connection for a job."""
        _ws_connections[job_id].add(ws)

    async def unregister_ws(self, job_id: str, ws: WebSocket) -> None:
        """Unregister a WebSocket connection for a job."""
        conns = _ws_connections.get(job_id)
        if conns:
            conns.discard(ws)
            if not conns:
                del _ws_connections[job_id]

    # --- Triage operations ------------------------------------------------

    async def create_triage_item(self, item: TriageItem) -> None:
        """Insert a new triage item into DB and cache."""
        async with get_db() as db:
            await db.execute(
                """
                INSERT INTO triage_items (id, job_id, module, severity, title,
                    description, raw_data, created_at, status, assigned_to, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.id,
                    item.job_id,
                    item.module,
                    item.severity,
                    item.title,
                    item.description,
                    json.dumps(item.raw_data) if item.raw_data else None,
                    _fmt_dt(item.created_at),
                    item.status,
                    item.assigned_to,
                    item.notes,
                ),
            )
            await db.commit()
        _triage_cache[item.id] = item

    async def update_triage_item(self, item: TriageItem) -> None:
        """Update an existing triage item."""
        async with get_db() as db:
            await db.execute(
                """
                UPDATE triage_items
                SET status = ?, notes = ?, assigned_to = ?
                WHERE id = ?
                """,
                (item.status, item.notes, item.assigned_to, item.id),
            )
            await db.commit()
        _triage_cache[item.id] = item

    async def get_triage_item(self, item_id: str) -> Optional[TriageItem]:
        """Fetch a triage item by ID."""
        return _triage_cache.get(item_id)

    async def get_triage_items(
        self,
        job_id: Optional[str] = None,
        status: Optional[str] = None,
        module: Optional[str] = None,
    ) -> List[TriageItem]:
        """List triage items with optional filters."""
        items = list(_triage_cache.values())
        if job_id:
            items = [i for i in items if i.job_id == job_id]
        if status:
            items = [i for i in items if i.status == status]
        if module:
            items = [i for i in items if i.module == module]
        items.sort(
            key=lambda i: i.created_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        return items

    async def delete_triage_item(self, item_id: str) -> None:
        """Remove a triage item."""
        async with get_db() as db:
            await db.execute("DELETE FROM triage_items WHERE id = ?", (item_id,))
            await db.commit()
        _triage_cache.pop(item_id, None)

    # --- Statistics -------------------------------------------------------

    async def get_stats(self) -> Dict[str, Any]:
        """Compute aggregate dashboard statistics."""
        jobs = list(_job_cache.values())
        triage_items = list(_triage_cache.values())

        total = len(jobs)
        pending = sum(1 for j in jobs if j.status == "pending")
        running = sum(1 for j in jobs if j.status == "running")
        completed = sum(1 for j in jobs if j.status == "completed")
        failed = sum(1 for j in jobs if j.status in ("failed", "timeout"))
        triage_clear = sum(1 for j in jobs if j.triage_status == "clear")
        triage_flagged = sum(1 for j in jobs if j.triage_status == "flagged")
        triage_manual = sum(1 for j in jobs if j.triage_status == "manual_review")
        open_items = sum(1 for i in triage_items if i.status in ("open", "in_review"))

        # Average duration (successful jobs only)
        durations = []
        for j in jobs:
            if j.status == "completed" and j.started_at and j.completed_at:
                durations.append((j.completed_at - j.started_at).total_seconds())
        avg_duration = sum(durations) / len(durations) if durations else None

        return {
            "total_jobs": total,
            "pending_jobs": pending,
            "running_jobs": running,
            "completed_jobs": completed,
            "failed_jobs": failed,
            "triage_clear": triage_clear,
            "triage_flagged": triage_flagged,
            "triage_manual": triage_manual,
            "open_queue_items": open_items,
            "avg_duration_seconds": round(avg_duration, 1) if avg_duration is not None else None,
        }


# Singleton instance — imported by router, websocket, and main modules.
state = State()


# ---------------------------------------------------------------------------
# Module-level close helper (called on shutdown)
# ---------------------------------------------------------------------------


async def close_db() -> None:
    """Close the database connection pool on application shutdown."""
    from core.database import close_pool

    await close_pool()
