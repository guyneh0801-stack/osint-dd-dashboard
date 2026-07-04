"""REST API endpoints for the OSINT DD Dashboard.

All routes are mounted under the configured ``API_PREFIX`` (default
``/api``).  Endpoints cover screening job lifecycle, triage queue
management, dashboard statistics, and health checks.
"""

from __future__ import annotations

import asyncio
import shutil
import uuid
from datetime import datetime, timezone
from logging import getLogger
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Response, status

from core.config import settings
from core.models import (
    DashboardStats,
    HealthResponse,
    ScreeningJob,
    ScreeningRequest,
    TriageItem,
    TriageItemUpdate,
)
from core.persistence import State
from services import report_server
from services.screening_engine import NativeScreeningEngine
from services.source_registry import source_registry

logger = getLogger(__name__)

router = APIRouter()

# Active subprocess tasks — used for graceful shutdown tracking.
_running_tasks: set[asyncio.Task] = set()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_state() -> State:
    """Return the singleton state instance.

    This is resolved at call time to avoid circular imports at module
    load time.
    """
    from core.persistence import state

    return state


def _get_logger():
    """Return the application logger."""
    from core.logger import get_logger

    return get_logger(__name__)


async def _enforce_concurrency_limit(state: State) -> None:
    """Raise 429 if too many jobs are currently running."""
    running = await state.list_jobs(status="running", limit=999)
    if len(running) >= settings.MAX_CONCURRENT_JOBS:
        raise HTTPException(
            status_code=429,
            detail=f"Maximum concurrent jobs ({settings.MAX_CONCURRENT_JOBS}) reached",
        )


# ---------------------------------------------------------------------------
# Screening Endpoints
# ---------------------------------------------------------------------------


@router.post("/screening/run", response_model=ScreeningJob, status_code=202)
async def start_screening(
    request: ScreeningRequest,
    background_tasks: BackgroundTasks,
) -> ScreeningJob:
    """Start a new OSINT screening job.

    Validates the request, creates a pending job record, and launches
the screening subprocess as a background task.  Returns the job
immediately so the client can connect to the WebSocket endpoint.
    """
    state = _get_state()
    log = _get_logger()

    await _enforce_concurrency_limit(state)

    job_id = str(uuid.uuid4())
    job = ScreeningJob(
        id=job_id,
        name_en=request.name_en,
        name_he=request.name_he,
        status="pending",
    )
    await state.create_job(job)

    log.info(
        "Screening queued: name_en=%s name_he=%s",
        request.name_en,
        request.name_he,
        extra={"job_id": job_id},
    )

    # Launch subprocess in background
    from services.screener_runner import run_screening

    task = asyncio.create_task(
        run_screening(
            job_id=job_id,
            name_en=request.name_en,
            name_he=request.name_he,
            state=state,
            logger=log,
        ),
        name=f"screening-{job_id}",
    )
    _running_tasks.add(task)
    task.add_done_callback(_running_tasks.discard)

    return job


@router.get("/screening", response_model=List[ScreeningJob])
async def list_screenings(
    status: Optional[str] = Query(None, description="Filter by job status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> List[ScreeningJob]:
    """List screening jobs with optional status filter and pagination.

    Results are ordered by creation time (newest first).
    """
    state = _get_state()
    if status and status not in ("pending", "running", "completed", "failed", "timeout"):
        raise HTTPException(status_code=400, detail="Invalid status filter")
    return await state.list_jobs(status=status, limit=limit, offset=offset)


@router.get("/screening/{job_id}", response_model=ScreeningJob)
async def get_screening(job_id: str) -> ScreeningJob:
    """Get a single screening job by ID."""
    state = _get_state()
    job = await state.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Screening job not found")
    return job


@router.get("/screening/{job_id}/report")
async def get_report(job_id: str):
    """Serve the Markdown report for a screening job.

    Returns ``text/markdown`` if the report exists, 404 otherwise.
    """
    return await report_server.serve_report(job_id, "report.md")


@router.get("/screening/{job_id}/json")
async def get_json_report(job_id: str):
    """Serve the raw JSON data file for a screening job.

    Returns ``application/json`` if the file exists, 404 otherwise.
    """
    return await report_server.serve_report(job_id, "report.json")


@router.delete("/screening/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_screening(job_id: str) -> Response:
    """Delete a screening job and its associated report files.

    Also removes any triage items linked to the job.
    """
    state = _get_state()
    log = _get_logger()

    job = await state.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Screening job not found")

    # If running, we delete anyway — the runner will handle its own cleanup
    await state.delete_job(job_id)

    # Remove triage items for this job
    items = await state.get_triage_items(job_id=job_id)
    for item in items:
        try:
            await state.delete_triage_item(item.id)
        except Exception:
            pass

    # Remove report files
    report_dir = settings.REPORTS_DIR / job_id
    if report_dir.exists():
        try:
            shutil.rmtree(report_dir)
        except OSError as exc:
            log.warning("Failed to remove report dir: %s", exc, extra={"job_id": job_id})

    log.info("Screening deleted", extra={"job_id": job_id})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Triage Queue Endpoints
# ---------------------------------------------------------------------------


@router.get("/queue", response_model=List[TriageItem])
async def list_queue(
    status: Optional[str] = Query(None, description="Filter by item status"),
    module: Optional[str] = Query(None, description="Filter by source module"),
    job_id: Optional[str] = Query(None, description="Filter by job ID"),
) -> List[TriageItem]:
    """List triage queue items with optional filters."""
    state = _get_state()
    if status and status not in ("open", "in_review", "resolved", "false_positive"):
        raise HTTPException(status_code=400, detail="Invalid status filter")
    if module and module not in ("sanctions", "adverse_media", "litigation", "other"):
        raise HTTPException(status_code=400, detail="Invalid module filter")
    return await state.get_triage_items(
        job_id=job_id, status=status, module=module
    )


@router.patch("/queue/{item_id}", response_model=TriageItem)
async def update_queue_item(
    item_id: str,
    update: TriageItemUpdate,
) -> TriageItem:
    """Update the status, notes, or assignment of a triage item."""
    state = _get_state()
    item = await state.get_triage_item(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Triage item not found")

    if update.status is not None:
        item.status = update.status
    if update.notes is not None:
        # Append new notes with timestamp
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        if item.notes:
            item.notes = f"{item.notes}\n[{timestamp}] {update.notes}"
        else:
            item.notes = f"[{timestamp}] {update.notes}"
    if update.assigned_to is not None:
        item.assigned_to = update.assigned_to

    await state.update_triage_item(item)
    return item


# ---------------------------------------------------------------------------
# Stats & Health
# ---------------------------------------------------------------------------


@router.get("/stats", response_model=DashboardStats)
async def get_stats() -> DashboardStats:
    """Return aggregated dashboard statistics."""
    state = _get_state()
    raw = await state.get_stats()
    return DashboardStats(**raw)


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Health check endpoint for monitoring and load balancers."""
    return HealthResponse(status="ok")


# ---------------------------------------------------------------------------
# Data Sources Endpoints
# ---------------------------------------------------------------------------


@router.get("/sources")
async def list_sources(
    category: Optional[str] = Query(None),
) -> List[dict]:
    """List all available OSINT data sources with their current status.

    Optionally filter by category: sanctions, adverse_media,
    litigation, or static_sanctions.
    """
    sources = source_registry.refresh()
    if category:
        sources = [s for s in sources if s.category == category]
    return [
        {
            "code": s.code,
            "name": s.name,
            "category": s.category,
            "status": s.status,
            "priority_tier": s.priority_tier,
            "description": s.description,
            "requires_api_key": s.requires_api_key,
            "api_key_env_var": s.api_key_env_var,
            "is_free": s.is_free,
        }
        for s in sources
    ]


@router.get("/sources/health")
async def sources_health() -> dict:
    """Return aggregate health status for all OSINT data sources."""
    return source_registry.get_health_summary()


# ---------------------------------------------------------------------------
# Native Screening Endpoint
# ---------------------------------------------------------------------------


@router.post("/screening/run-native", response_model=ScreeningJob, status_code=202)
async def start_native_screening(
    request: ScreeningRequest,
    background_tasks: BackgroundTasks,
) -> ScreeningJob:
    """Start a native screening using in-process adapters (no subprocess).

    Runs all enabled OSINT data source adapters directly in the
    application process, streaming results via WebSocket. This is
    faster than the subprocess-based approach and provides better
    error handling and progress visibility.
    """
    state = _get_state()
    log = _get_logger()

    await _enforce_concurrency_limit(state)

    job_id = str(uuid.uuid4())
    job = ScreeningJob(
        id=job_id,
        name_en=request.name_en,
        name_he=request.name_he,
        status="pending",
    )
    await state.create_job(job)

    log.info(
        "Native screening queued: name_en=%s name_he=%s",
        request.name_en,
        request.name_he,
        extra={"job_id": job_id},
    )

    # Launch native engine
    engine = NativeScreeningEngine(state)
    task = asyncio.create_task(
        engine.run_screening(job_id, request.name_en, request.name_he),
        name=f"native-screening-{job_id}",
    )
    _running_tasks.add(task)
    task.add_done_callback(_running_tasks.discard)

    return job
