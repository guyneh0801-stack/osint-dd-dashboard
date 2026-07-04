"""Async subprocess runner for the screening engine.

Provides ``run_screening`` — a coroutine that executes the
``dd_screener.py`` script in a subprocess, streams stdout/stderr
through the WebSocket log buffer, parses the JSON report on success,
and updates the job record with results or failure details.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from core.config import settings
from core.models import WSMessage
from core.persistence import State
from core.logger import get_logger

logger = get_logger(__name__)


async def run_screening(
    job_id: str,
    name_en: str,
    name_he: Optional[str],
    state: State,
    logger,
) -> None:
    """Run the screening subprocess for *job_id*.

    This function is designed to be called via ``asyncio.create_task``
    so it runs in the background while the main process continues to
    serve HTTP/WebSocket requests.

    Steps:
    1. Mark job as ``running``.
    2. Spawn ``python dd_screener.py --job-id {id} --name-en {name} ...``
    3. Stream stdout/stderr lines via WebSocket.
    4. On subprocess exit:
       * Exit code 0 → parse JSON report, generate Markdown, mark
         ``completed``.
       * Exit code non-zero → mark ``failed`` with stderr tail.
       * Timeout → kill subprocess, mark ``timeout``.
    """
    job = await state.get_job(job_id)
    if job is None:
        logger.error("Job not found in runner", extra={"job_id": job_id})
        return

    # Mark as running
    job.status = "running"
    job.started_at = datetime.now(timezone.utc)
    await state.update_job(job)

    # Prepare report directory
    report_dir = settings.REPORTS_DIR / job_id
    report_dir.mkdir(parents=True, exist_ok=True)
    report_json = report_dir / "report.json"
    report_md = report_dir / "report.md"

    # Build command
    cmd = [
        "python", "dd_screener.py",
        "--job-id", job_id,
        "--name-en", name_en,
        "--output-json", str(report_json),
        "--output-md", str(report_md),
    ]
    if name_he:
        cmd.extend(["--name-he", name_he])

    logger.info(
        "Starting screening subprocess: %s", " ".join(cmd),
        extra={"job_id": job_id},
    )

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(settings.BASE_DIR),
        )
    except Exception as exc:
        logger.error("Failed to start subprocess: %s", exc, extra={"job_id": job_id})
        job.status = "failed"
        job.error_message = f"Failed to start subprocess: {exc}"
        job.completed_at = datetime.now(timezone.utc)
        await state.update_job(job)
        return

    # Stream stdout/stderr
    async def _read_stream(stream, stream_name):
        """Read lines from a stream and broadcast via WebSocket."""
        while True:
            line = await stream.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip("\n")
            if text:
                msg = WSMessage(type="log", job_id=job_id, payload=text)
                await state.append_log(job_id, msg.model_dump_json())

    # Race stdout, stderr, and timeout
    try:
        await asyncio.wait_for(
            asyncio.gather(
                _read_stream(proc.stdout, "stdout"),
                _read_stream(proc.stderr, "stderr"),
                proc.wait(),
            ),
            timeout=settings.SCREENING_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.warning("Screening timed out after %ds", settings.SCREENING_TIMEOUT, extra={"job_id": job_id})
        try:
            proc.kill()
            await proc.wait()
        except Exception:
            pass
        job.status = "timeout"
        job.error_message = f"Screening timed out after {settings.SCREENING_TIMEOUT}s"
        job.completed_at = datetime.now(timezone.utc)
        job.exit_code = -1
        await state.update_job(job)
        return

    exit_code = proc.returncode
    job.exit_code = exit_code
    job.completed_at = datetime.now(timezone.utc)

    if exit_code == 0 and report_json.exists():
        # Success — parse report
        try:
            with open(report_json, "r", encoding="utf-8") as f:
                report_data = json.load(f)
            job.report_md_path = str(report_md) if report_md.exists() else None
            job.report_json_path = str(report_json)
            job.status = "completed"
            logger.info("Screening completed successfully", extra={"job_id": job_id})
        except Exception as exc:
            job.status = "failed"
            job.error_message = f"Report parsing error: {exc}"
            logger.error("Report parsing error: %s", exc, extra={"job_id": job_id})
    else:
        job.status = "failed"
        # Collect last stderr lines for error message
        stderr_remaining = await proc.stderr.read() if proc.stderr else b""
        stderr_tail = stderr_remaining.decode("utf-8", errors="replace")[-500:]
        job.error_message = f"Subprocess exited with code {exit_code}. stderr: {stderr_tail}"
        logger.error(
            "Screening failed with exit code %d", exit_code,
            extra={"job_id": job_id},
        )

    await state.update_job(job)
