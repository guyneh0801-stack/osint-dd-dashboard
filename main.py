"""FastAPI application entry point for the OSINT DD Dashboard.

The application is structured with a lifespan manager that ensures
required directories exist at startup, initialises the database-backed
persistence layer, recovers any orphaned jobs from a previous crash, and
cancels any running subprocess tasks on graceful shutdown.

Endpoints:
* REST API under ``/api/*``
* WebSocket under ``/ws/screening/{job_id}``
* Static files from ``dist/`` (if present)
* Health check at ``/api/health``

Usage:
    python main.py           # Development with auto-reload
    uvicorn main:app         # Production with uvicorn directly
"""

from __future__ import annotations

import asyncio
import os
import sys
from contextlib import asynccontextmanager
from typing import AsyncGenerator

# ---------------------------------------------------------------------------
# Explicit startup print (visible even before logging is configured)
# ---------------------------------------------------------------------------

print("[OSINT DD] Starting up...", flush=True)

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from api.router import _running_tasks, router
from api.websocket import screening_ws
from core.config import settings
from core.logger import get_logger
from core.persistence import close_db, state

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Lifespan manager
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Handle startup and shutdown events.

    Startup:
    1. Ensure ``reports/`` and ``logs/`` directories exist.
    2. Initialise the persistence layer (create tables, hydrate cache).
    3. Recovery scan: mark orphaned *running* jobs as *failed*.
    4. Log configuration summary (without secrets).

    Shutdown:
    1. Cancel all running screening tasks.
    2. Wait briefly for cleanup.
    3. Dispose the database engine.
    """
    # --- Startup ---
    settings.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    settings.LOGS_DIR.mkdir(parents=True, exist_ok=True)

    # Init persistence layer (create tables, load cache)
    await state.init()

    # Recovery: mark orphaned running jobs as failed
    orphaned = await state.recovery_scan()
    for job in orphaned:
        logger.warning(
            "Recovered orphaned job %s: marked as failed", job.id, extra={"job_id": job.id}
        )

    logger.info(
        "Application starting — reports_dir=%s logs_dir=%s timeout=%ds max_concurrent=%d",
        settings.REPORTS_DIR.resolve(),
        settings.LOGS_DIR.resolve(),
        settings.SCREENING_TIMEOUT,
        settings.MAX_CONCURRENT_JOBS,
    )

    yield

    # --- Shutdown ---
    logger.info("Application shutting down — cancelling %d running tasks", len(_running_tasks))

    for task in list(_running_tasks):
        task.cancel()

    if _running_tasks:
        await asyncio.sleep(1)  # Brief grace period for cleanup

    await close_db()
    logger.info("Shutdown complete")


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

print("[OSINT DD] Creating FastAPI app...", flush=True)

app = FastAPI(
    title=settings.APP_NAME,
    version="1.0.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# REST router
app.include_router(router, prefix=settings.API_PREFIX)

# WebSocket endpoint
@app.websocket("/ws/screening/{job_id}")
async def websocket_endpoint(websocket: WebSocket, job_id: str) -> None:
    """WebSocket entry point for real-time screening log streaming."""
    await screening_ws(websocket, job_id, state)


# Static files (production frontend build)
_dist_path = os.path.join(os.path.dirname(__file__), "dist")
if os.path.isdir(_dist_path):
    app.mount("/", StaticFiles(directory=_dist_path, html=True), name="static")
    logger.info("Serving static files from %s", _dist_path)

print("[OSINT DD] FastAPI app created successfully", flush=True)


# ---------------------------------------------------------------------------
# Entry point — start the server
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    reload = os.environ.get("RELOAD", "false").lower() in ("true", "1", "yes")

    print(f"[OSINT DD] Starting uvicorn — host={host} port={port} reload={reload}", flush=True)

    try:
        uvicorn.run(
            app,  # Pass app object directly instead of string
            host=host,
            port=port,
            reload=reload,
            log_level="info",
        )
    except Exception as exc:
        print(f"[OSINT DD] FAILED to start: {exc}", flush=True)
        sys.exit(1)
