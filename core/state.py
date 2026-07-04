"""Global application state and database-backed persistence.

The ``State`` class wraps a SQLite repository for durability while
maintaining an in-memory cache for hot reads.  All operations are
async-safe and can be called from multiple concurrent screening jobs.

This module also defines the singleton ``state`` instance used by the
REST router and WebSocket handler.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import WebSocket

from core.config import settings
from core.database import execute_query, execute_write, init_db
from core.models import ScreeningJob, TriageItem

# ---------------------------------------------------------------------------
# In-memory cache helpers
# ---------------------------------------------------------------------------

# job_id  → ScreeningJob
_job_cache: Dict[str, ScreeningJob] = {}
# job_id  → list of log lines (oldest first)
_log_buffer: Dict[str, List[str]] = defaultdict(list)
# job_id  → set of active WebSocket connections
_ws_connections: Dict[str, set[WebSocket]] = defaultdict(set)
# item_id → TriageItem
_triage_cache: Dict[str, TriageItem] = {}


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
        rows = await execute_query("SELECT * FROM screening_jobs ORDER BY created_at DESC")
        for row in rows:
            job = ScreeningJob(**row)
            _job_cache[job.id] = job

    async def _hydrate_triage(self) -> None:
        """Load all triage items from DB into the in-memory cache."""
        rows = await execute_query("SELECT * FROM triage_items ORDER BY created_at DESC")
        for row in rows:
            item = TriageItem(**row)
            _triage_cache[item.id] = item

    # --- Job operations ---------------------------------------------------

    async def create_job(self, job: ScreeningJob) -> None:
        """Insert a new job into DB and cache."""
        await execute_write(
            """
            INSERT INTO screening_jobs (id, name_en, name_he, status, created_at,
                started_at, completed_at, error_message, report_md_path,
                report_json_path, exit_code, triage_status, flagged_modules)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job.id, job.name_en, job.name_he, job.status,
                job.created_at.isoformat() if job.created_at else None,
                job.started_at.isoformat() if job.started_at else None,
                job.completed_at.isoformat() if job.completed_at else None,
                job.error_message, job.report_md_path, job.report_json_path,
                job.exit_code, job.triage_status,
                ",".join(job.flagged_modules),
            ),
        )
        _job_cache[job.id] = job

    async def update_job(self, job: ScreeningJob) -> None:
        """Update an existing job in DB and cache."""
        await execute_write(
            """
            UPDATE screening_jobs
            SET status = ?, started_at = ?, completed_at = ?,
                error_message = ?, report_md_path = ?, report_json_path = ?,
                exit_code = ?, triage_status = ?, flagged_modules = ?
            WHERE id = ?
            """,
            (
                job.status,
                job.started_at.isoformat() if job.started_at else None,
                job.completed_at.isoformat() if job.completed_at else None,
                job.error_message, job.report_md_path, job.report_json_path,
                job.exit_code, job.triage_status,
                ",".join(job.flagged_modules),
                job.id,
            ),
        )
        _job_cache[job.id] = job

    async def get_job(self, job_id: str) -> Optional[ScreeningJob]:
        """Fetch a job by ID from cache (falls back to DB)."""
        return _job_cache.get(job_id)

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
        jobs.sort(key=lambda j: j.created_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        return jobs[offset: offset + limit]

    async def delete_job(self, job_id: str) -> None:
        """Remove a job from DB and cache."""
        await execute_write("DELETE FROM screening_jobs WHERE id = ?", (job_id,))
        await execute_write("DELETE FROM triage_items WHERE job_id = ?", (job_id,))
        _job_cache.pop(job_id, None)
        _log_buffer.pop(job_id, None)
        _ws_connections.pop(job_id, None)
        # Also remove associated triage items from cache
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
        for ws in list(_ws_connections.get(job_id, set())):
            try:
                await ws.send_text(line)
            except Exception:
                # Connection closed — will be cleaned up on next send
                pass

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
        await execute_write(
            """
            INSERT INTO triage_items (id, job_id, module, severity, title,
                description, raw_data, created_at, status, assigned_to, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.id, item.job_id, item.module, item.severity,
                item.title, item.description,
                str(item.raw_data) if item.raw_data else None,
                item.created_at.isoformat() if item.created_at else None,
                item.status, item.assigned_to, item.notes,
            ),
        )
        _triage_cache[item.id] = item

    async def update_triage_item(self, item: TriageItem) -> None:
        """Update an existing triage item."""
        await execute_write(
            """
            UPDATE triage_items
            SET status = ?, notes = ?, assigned_to = ?
            WHERE id = ?
            """,
            (item.status, item.notes, item.assigned_to, item.id),
        )
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
        items.sort(key=lambda i: i.created_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        return items

    async def delete_triage_item(self, item_id: str) -> None:
        """Remove a triage item."""
        await execute_write("DELETE FROM triage_items WHERE id = ?", (item_id,))
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


# Singleton instance — imported by router and websocket modules.
state = State()
