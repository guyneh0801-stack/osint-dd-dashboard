"""Static report file serving helpers.

Provides async functions to serve Markdown and JSON report files with
proper content-type headers and 404 handling.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException, Response
from fastapi.responses import FileResponse

from core.config import settings


async def serve_report(job_id: str, filename: str) -> Response:
    """Serve a report file for *job_id*.

    *filename* should be ``report.md`` or ``report.json``.

    Returns a ``FileResponse`` with the correct content-type, or
    raises 404 if the file does not exist.
    """
    file_path = settings.REPORTS_DIR / job_id / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"Report file not found: {filename}")

    content_type = "text/markdown; charset=utf-8" if filename.endswith(".md") else "application/json"

    return FileResponse(
        path=file_path,
        media_type=content_type,
        filename=f"{job_id}_{filename}",
    )
