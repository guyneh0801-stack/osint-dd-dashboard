"""Pydantic v2 data models for the OSINT DD Dashboard.

All models enforce strict validation and use UUID4 strings for primary
identifiers.  Timestamps are timezone-aware ``datetime`` objects.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Screening Job
# ---------------------------------------------------------------------------

JobStatus = Literal["pending", "running", "completed", "failed", "timeout"]
TriageStatus = Literal["clear", "flagged", "manual_review"]


class ScreeningJob(BaseModel):
    """Represents a single OSINT screening execution.

    The lifecycle is::

        pending → running → completed|failed|timeout
    """

    id: str = Field(..., description="UUID4 identifier for the job.")
    name_en: str = Field(..., description="Subject name in English.")
    name_he: Optional[str] = Field(None, description="Subject name in Hebrew.")
    status: JobStatus = Field("pending", description="Current job status.")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp when the job was created.",
    )
    started_at: Optional[datetime] = Field(
        None, description="UTC timestamp when the job started running."
    )
    completed_at: Optional[datetime] = Field(
        None, description="UTC timestamp when the job finished."
    )
    error_message: Optional[str] = Field(
        None, description="Human-readable error if the job failed."
    )
    report_md_path: Optional[str] = Field(
        None, description="Filesystem path to the generated Markdown report."
    )
    report_json_path: Optional[str] = Field(
        None, description="Filesystem path to the generated JSON data."
    )
    exit_code: Optional[int] = Field(
        None, description="Subprocess exit code (0 = success)."
    )
    triage_status: TriageStatus = Field(
        "clear", description="Aggregated triage verdict."
    )
    flagged_modules: List[str] = Field(
        default_factory=list,
        description="Module names that triggered triage flags.",
    )


# ---------------------------------------------------------------------------
# Triage Item
# ---------------------------------------------------------------------------

ModuleName = Literal["sanctions", "adverse_media", "litigation", "other"]
Severity = Literal["info", "low", "medium", "high", "critical"]
TriageItemStatus = Literal["open", "in_review", "resolved", "false_positive"]


class TriageItem(BaseModel):
    """A single actionable item produced by the triage engine.

    Each flagged module may produce one or more ``TriageItem`` records
    that analysts review through the triage queue UI.
    """

    id: str = Field(..., description="UUID4 identifier for the triage item.")
    job_id: str = Field(..., description="Foreign key to the parent ScreeningJob.")
    module: ModuleName = Field(..., description="Source module that produced the flag.")
    severity: Severity = Field("medium", description="Severity of the finding.")
    title: str = Field(..., description="Short human-readable summary.")
    description: str = Field(..., description="Detailed finding text.")
    raw_data: Optional[Dict[str, Any]] = Field(
        None, description="Relevant JSON subtree from the report."
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp when the item was created.",
    )
    status: TriageItemStatus = Field("open", description="Review status.")
    assigned_to: Optional[str] = Field(None, description="Analyst assigned to review.")
    notes: Optional[str] = Field(None, description="Free-form analyst notes.")


# ---------------------------------------------------------------------------
# Screening Request (API input)
# ---------------------------------------------------------------------------

class ScreeningRequest(BaseModel):
    """Payload for ``POST /api/screening/run``.

    The ``name_en`` field is restricted to a safe character whitelist to
    prevent command-injection when passed to the subprocess.
    """

    name_en: str = Field(
        ...,
        min_length=1,
        max_length=200,
        pattern=r"^[a-zA-Z0-9\s\-\.',]+$",
        description="Subject name in English (alphanumeric + limited punctuation).",
    )
    name_he: Optional[str] = Field(
        None,
        max_length=200,
        description="Subject name in Hebrew (optional).",
    )


# ---------------------------------------------------------------------------
# Dashboard Statistics
# ---------------------------------------------------------------------------

class DashboardStats(BaseModel):
    """Aggregated metrics returned by ``GET /api/stats``."""

    total_jobs: int = Field(0, description="Total number of screening jobs.")
    pending_jobs: int = Field(0, description="Jobs currently pending.")
    running_jobs: int = Field(0, description="Jobs currently running.")
    completed_jobs: int = Field(0, description="Successfully completed jobs.")
    failed_jobs: int = Field(0, description="Failed or timed-out jobs.")
    triage_clear: int = Field(0, description="Jobs with triage_status='clear'.")
    triage_flagged: int = Field(0, description="Jobs with triage_status='flagged'.")
    triage_manual: int = Field(0, description="Jobs with triage_status='manual_review'.")
    open_queue_items: int = Field(0, description="Triage items not yet resolved.")
    avg_duration_seconds: Optional[float] = Field(
        None, description="Average job duration (successful jobs only)."
    )


# ---------------------------------------------------------------------------
# WebSocket Message
# ---------------------------------------------------------------------------

WSMessageType = Literal["log", "status", "error", "complete", "ping"]


class WSMessage(BaseModel):
    """Envelope for every message sent over the WebSocket.

    The ``payload`` field is polymorphic:
    * **log** → ``str`` (raw log line)
    * **status** → ``dict`` (status update)
    * **error** → ``dict`` (error details)
    * **complete** → ``dict`` (completion summary)
    * **ping** → ``str`` (keep-alive)
    """

    type: WSMessageType = Field(..., description="Message category.")
    job_id: str = Field(..., description="Target job identifier.")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp of the message.",
    )
    payload: Union[str, Dict[str, Any]] = Field(
        ..., description="Message body (type-dependent)."
    )


# ---------------------------------------------------------------------------
# Triage Item Update (API input)
# ---------------------------------------------------------------------------

class TriageItemUpdate(BaseModel):
    """Payload for ``PATCH /api/queue/{id}``."""

    status: Optional[TriageItemStatus] = Field(None, description="New review status.")
    notes: Optional[str] = Field(None, description="Analyst notes to append.")
    assigned_to: Optional[str] = Field(None, description="Analyst to assign.")


# ---------------------------------------------------------------------------
# Health Check
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    """Response for ``GET /api/health``."""

    status: Literal["ok", "degraded"] = "ok"
