"""Triage engine that converts screening JSON reports into queue items.

The engine inspects each module's output in the report and creates
``TriageItem`` records for findings that exceed configured severity
thresholds.  Analysts review these items through the triage queue UI.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.config import settings
from core.models import ModuleName, Severity, TriageItem, TriageItemStatus
from core.persistence import State
from core.logger import get_logger

logger = get_logger(__name__)

# Minimum severity that triggers a triage item
DEFAULT_SEVERITY_THRESHOLD = "medium"


def _severity_rank(severity: str) -> int:
    """Return numeric rank for severity comparison."""
    ranks = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
    return ranks.get(severity, 2)


async def process_report(job_id: str, state: State) -> List[TriageItem]:
    """Parse the JSON report for *job_id* and create triage items.

    Returns the list of created items (may be empty if no flags).
    """
    report_path = settings.REPORTS_DIR / job_id / "report.json"
    if not report_path.exists():
        logger.warning("No report found for triage: %s", report_path, extra={"job_id": job_id})
        return []

    try:
        with open(report_path, "r", encoding="utf-8") as f:
            report = json.load(f)
    except Exception as exc:
        logger.error("Failed to parse report for triage: %s", exc, extra={"job_id": job_id})
        return []

    items: List[TriageItem] = []

    # Process each module's results
    modules = report.get("modules", {})
    for module_name, module_data in modules.items():
        module_items = _process_module(job_id, module_name, module_data)
        items.extend(module_items)

    # Persist all items
    for item in items:
        await state.create_triage_item(item)

    # Update job triage status
    job = await state.get_job(job_id)
    if job:
        if items:
            max_severity = max(items, key=lambda i: _severity_rank(i.severity)).severity
            if _severity_rank(max_severity) >= _severity_rank("high"):
                job.triage_status = "flagged"
            else:
                job.triage_status = "manual_review"
            job.flagged_modules = list({i.module for i in items})
        else:
            job.triage_status = "clear"
            job.flagged_modules = []
        await state.update_job(job)

    logger.info(
        "Triage complete: %d items created for job %s", len(items), job_id,
        extra={"job_id": job_id, "item_count": len(items)},
    )
    return items


def _process_module(
    job_id: str,
    module_name: str,
    module_data: Any,
) -> List[TriageItem]:
    """Process a single module's output and return triage items."""
    items: List[TriageItem] = []

    # Handle different module output shapes
    if isinstance(module_data, dict):
        # Check for flagged findings
        findings = module_data.get("findings", [])
        for finding in findings:
            severity = finding.get("severity", "medium")
            if _severity_rank(severity) < _severity_rank(DEFAULT_SEVERITY_THRESHOLD):
                continue
            item = TriageItem(
                id=str(uuid.uuid4()),
                job_id=job_id,
                module=_normalise_module(module_name),
                severity=severity,
                title=finding.get("title", f"{module_name} finding"),
                description=finding.get("description", ""),
                raw_data=finding,
            )
            items.append(item)

        # Check for sanctions hits (special handling)
        if module_name in ("sanctions", "static_sanctions"):
            hits = module_data.get("hits", [])
            for hit in hits:
                item = TriageItem(
                    id=str(uuid.uuid4()),
                    job_id=job_id,
                    module="sanctions",
                    severity="critical",
                    title=f"Sanctions hit: {hit.get('name', 'Unknown')}",
                    description=hit.get("reason", "Name appears on a sanctions list"),
                    raw_data=hit,
                )
                items.append(item)

    elif isinstance(module_data, list):
        # List of findings
        for entry in module_data:
            if isinstance(entry, dict):
                severity = entry.get("severity", "medium")
                if _severity_rank(severity) < _severity_rank(DEFAULT_SEVERITY_THRESHOLD):
                    continue
                item = TriageItem(
                    id=str(uuid.uuid4()),
                    job_id=job_id,
                    module=_normalise_module(module_name),
                    severity=severity,
                    title=entry.get("title", f"{module_name} finding"),
                    description=entry.get("description", ""),
                    raw_data=entry,
                )
                items.append(item)

    return items


def _normalise_module(name: str) -> ModuleName:
    """Map a raw module name to a canonical ModuleName."""
    mapping: Dict[str, ModuleName] = {
        "sanctions": "sanctions",
        "static_sanctions": "sanctions",
        "adverse_media": "adverse_media",
        "litigation": "litigation",
        "pep": "other",
        "corporate": "other",
        "social_media": "other",
    }
    return mapping.get(name, "other")
