"""Native screening engine — runs all OSINT adapters in-process.

Replaces the subprocess-based dd_screener.py approach with direct
async calls to all data source adapters. Results are stored directly
in HybridState and streamed via WebSocket.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.config import settings
from core.logger import get_logger
from core.models import ScreeningJob, TriageItem, WSMessage
from services.jurisdiction import get_jurisdiction_sources
from services.adverse_media import get_media_sources
from services.litigation import get_litigation_sources
from services.static_sanctions import get_static_sanctions_sources, StaticSanctionsDownloader

logger = get_logger(__name__)

# Module enable flags — read from config.json or environment variables
_cfg = None
try:
    from core.config_file import config_file
    _cfg = config_file
except Exception:
    pass

def _module_flag(key: str, env_var: str, default: bool = True) -> bool:
    """Read a boolean module flag: env var > config.json > default."""
    import os
    env_val = os.environ.get(env_var, "").lower()
    if env_val in ("true", "1", "yes"):
        return True
    if env_val in ("false", "0", "no"):
        return False
    if _cfg is not None:
        return _cfg.get_bool(key, default)
    return default

ENABLE_JURISDICTION = _module_flag("enable_jurisdiction", "DD_ENABLE_JURISDICTION")
ENABLE_ADVERSE_MEDIA = _module_flag("enable_adverse_media", "DD_ENABLE_ADVERSE_MEDIA")
ENABLE_LITIGATION = _module_flag("enable_litigation", "DD_ENABLE_LITIGATION")
ENABLE_STATIC_SANCTIONS = _module_flag("enable_static_sanctions", "DD_ENABLE_STATIC_SANCTIONS")

SANCTIONS_CACHE_DIR = os.environ.get(
    "SANCTIONS_CACHE_DIR", str(Path(settings.DATABASE_PATH).parent / "sanctions_cache")
)


class NativeScreeningEngine:
    """Orchestrates all OSINT data source adapters for a screening job."""

    def __init__(self, state):
        self.state = state
        self.downloader = StaticSanctionsDownloader(SANCTIONS_CACHE_DIR)

    async def run_screening(
        self, job_id: str, name_en: str, name_he: Optional[str]
    ) -> None:
        """Run a complete native screening using all enabled adapters."""
        job = await self.state.get_job(job_id)
        if job is None:
            logger.error("Job not found", extra={"job_id": job_id})
            return

        # Update to running
        job.status = "running"
        job.started_at = datetime.now(timezone.utc)
        await self.state.update_job(job)
        await self._broadcast_status(job_id, "running", 0)

        all_results: Dict[str, Any] = {
            "subject": {"name_en": name_en, "name_he": name_he},
            "started_at": datetime.now(timezone.utc).isoformat(),
            "jurisdiction": [],
            "adverse_media": [],
            "litigation": [],
            "static_sanctions": [],
        }
        total_sources = 0
        completed_sources = 0

        try:
            # --- Jurisdiction sources (parallel) ---
            if ENABLE_JURISDICTION:
                await self._broadcast_log(job_id, "Starting jurisdiction checks...")
                sources = [
                    s for s in get_jurisdiction_sources() if s.is_available()
                ]
                total_sources += len(sources)
                tasks = [
                    self._run_jurisdiction_source(s, name_en, name_he, job_id)
                    for s in sources
                ]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for r in results:
                    if isinstance(r, Exception):
                        logger.warning("Jurisdiction source failed: %s", r)
                    else:
                        all_results["jurisdiction"].append(r)
                        completed_sources += 1
                        await self._broadcast_progress(
                            job_id, completed_sources, total_sources
                        )

            # --- Static sanctions (parallel) ---
            if ENABLE_STATIC_SANCTIONS:
                await self._broadcast_log(
                    job_id, "Starting static sanctions download/search..."
                )
                sources = [
                    s for s in get_static_sanctions_sources() if s.is_available()
                ]
                total_sources += len(sources)
                tasks = [
                    self._run_static_source(s, name_en, name_he, job_id)
                    for s in sources
                ]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for r in results:
                    if isinstance(r, Exception):
                        logger.warning("Static sanctions source failed: %s", r)
                    else:
                        all_results["static_sanctions"].append(r)
                        completed_sources += 1
                        await self._broadcast_progress(
                            job_id, completed_sources, total_sources
                        )

            # --- Adverse media (parallel) ---
            if ENABLE_ADVERSE_MEDIA:
                await self._broadcast_log(job_id, "Starting adverse media checks...")
                sources = [s for s in get_media_sources() if s.is_available()]
                total_sources += len(sources)
                tasks = [
                    self._run_media_source(s, name_en, name_he, job_id)
                    for s in sources
                ]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for r in results:
                    if isinstance(r, Exception):
                        logger.warning("Adverse media source failed: %s", r)
                    else:
                        all_results["adverse_media"].append(r)
                        completed_sources += 1
                        await self._broadcast_progress(
                            job_id, completed_sources, total_sources
                        )

            # --- Litigation (parallel) ---
            if ENABLE_LITIGATION:
                await self._broadcast_log(job_id, "Starting litigation checks...")
                sources = [
                    s for s in get_litigation_sources() if s.is_available()
                ]
                total_sources += len(sources)
                tasks = [
                    self._run_litigation_source(s, name_en, name_he, job_id)
                    for s in sources
                ]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for r in results:
                    if isinstance(r, Exception):
                        logger.warning("Litigation source failed: %s", r)
                    else:
                        all_results["litigation"].append(r)
                        completed_sources += 1
                        await self._broadcast_progress(
                            job_id, completed_sources, total_sources
                        )

            # --- Finalize ---
            all_results["completed_at"] = datetime.now(timezone.utc).isoformat()

            # Save JSON report
            report_dir = settings.REPORTS_DIR / job_id
            report_dir.mkdir(parents=True, exist_ok=True)
            json_path = report_dir / "report.json"
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(all_results, f, indent=2, ensure_ascii=False, default=str)

            # Generate Markdown report
            md_path = report_dir / "report.md"
            md_content = self._generate_markdown_report(all_results, name_en, name_he)
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(md_content)

            # Update job
            job.status = "completed"
            job.completed_at = datetime.now(timezone.utc)
            job.report_md_path = str(md_path)
            job.report_json_path = str(json_path)
            job.exit_code = 0

            # Run triage on results
            await self._run_native_triage(job_id, all_results)

            await self.state.update_job(job)
            await self._broadcast_status(job_id, "completed", 100)
            await self._broadcast_complete(job, all_results)

            logger.info(
                "Native screening completed: sources=%d/%d",
                completed_sources,
                total_sources,
                extra={"job_id": job_id},
            )

        except asyncio.CancelledError:
            job.status = "failed"
            job.completed_at = datetime.now(timezone.utc)
            job.error_message = "Screening cancelled"
            await self.state.update_job(job)
            raise
        except Exception as exc:
            logger.exception(
                "Native screening failed: %s", exc, extra={"job_id": job_id}
            )
            job.status = "failed"
            job.completed_at = datetime.now(timezone.utc)
            job.error_message = str(exc)
            await self.state.update_job(job)
            await self._broadcast_status(job_id, "failed", 0)

    # --- Source runners ---

    async def _run_jurisdiction_source(
        self, source, name_en: str, name_he: Optional[str], job_id: str
    ) -> Dict[str, Any]:
        await self._broadcast_log(job_id, f"  -> {source.name}...")
        result = await source.query(name_en, name_he)
        status_emoji = {
            "flagged": "🚩",
            "clear": "✅",
            "error": "⚠️",
            "timeout": "⏱️",
        }.get(result.status, "❓")
        finding_count = len(result.findings) if result.status == "flagged" else 0
        await self._broadcast_log(
            job_id,
            f"  <- {source.name}: {status_emoji} {result.status.upper()} "
            f"({finding_count} findings)",
        )
        return result.model_dump()

    async def _run_static_source(
        self, source, name_en: str, name_he: Optional[str], job_id: str
    ) -> Dict[str, Any]:
        await self._broadcast_log(job_id, f"  -> {source.name}...")
        result = await source.query(name_en, name_he, SANCTIONS_CACHE_DIR)
        status_emoji = {
            "flagged": "🚩",
            "clear": "✅",
            "error": "⚠️",
            "not_downloaded": "📥",
        }.get(result.status, "❓")
        match_count = len(result.matches) if result.status == "flagged" else 0
        await self._broadcast_log(
            job_id,
            f"  <- {source.name}: {status_emoji} {result.status.upper()} "
            f"({match_count} matches from {result.total_entries} entries)",
        )
        return result.model_dump()

    async def _run_media_source(
        self, source, name_en: str, name_he: Optional[str], job_id: str
    ) -> Dict[str, Any]:
        await self._broadcast_log(job_id, f"  -> {source.name}...")
        result = await source.query(name_en, name_he)
        status_emoji = {
            "flagged": "🚩",
            "clear": "✅",
            "error": "⚠️",
            "timeout": "⏱️",
        }.get(result.status, "❓")
        article_count = result.articles_found
        await self._broadcast_log(
            job_id,
            f"  <- {source.name}: {status_emoji} {result.status.upper()} "
            f"({article_count} articles)",
        )
        return result.model_dump()

    async def _run_litigation_source(
        self, source, name_en: str, name_he: Optional[str], job_id: str
    ) -> Dict[str, Any]:
        await self._broadcast_log(job_id, f"  -> {source.name}...")
        result = await source.query(name_en, name_he)
        status_emoji = {
            "flagged": "🚩",
            "clear": "✅",
            "error": "⚠️",
            "timeout": "⏱️",
        }.get(result.status, "❓")
        case_count = result.cases_found
        await self._broadcast_log(
            job_id,
            f"  <- {source.name}: {status_emoji} {result.status.upper()} "
            f"({case_count} cases)",
        )
        return result.model_dump()

    # --- Triage ---

    async def _run_native_triage(
        self, job_id: str, results: Dict[str, Any]
    ) -> None:
        """Create triage items from native screening results."""
        flagged_modules: List[str] = []

        # Check jurisdiction results
        for jr in results.get("jurisdiction", []):
            if jr.get("status") == "flagged":
                flagged_modules.append("sanctions")
                for finding in jr.get("findings", []):
                    item = TriageItem(
                        id=str(uuid.uuid4()),
                        job_id=job_id,
                        module="sanctions",
                        severity="high",
                        title=f"Sanctions match: {finding.get('matched_name', 'Unknown')}",
                        description=f"Matched on {jr.get('jurisdiction_name', 'Unknown')}: "
                        f"{json.dumps(finding, ensure_ascii=False)[:500]}",
                        raw_data=finding,
                    )
                    await self.state.add_triage_item(item)

        # Check static sanctions
        for sr in results.get("static_sanctions", []):
            if sr.get("status") == "flagged":
                flagged_modules.append("sanctions")
                for match in sr.get("matches", [])[:5]:
                    entry = match.get("entry", {})
                    item = TriageItem(
                        id=str(uuid.uuid4()),
                        job_id=job_id,
                        module="sanctions",
                        severity="critical",
                        title=f"Static sanctions match: {entry.get('name', 'Unknown')}",
                        description=f"Matched on {sr.get('source_name')}: "
                        f"{entry.get('name', '')} ({entry.get('type', '')}) - "
                        f"Program: {entry.get('program', 'N/A')}",
                        raw_data=match,
                    )
                    await self.state.add_triage_item(item)

        # Check adverse media
        for mr in results.get("adverse_media", []):
            if mr.get("status") == "flagged":
                flagged_modules.append("adverse_media")
                for art in mr.get("articles", [])[:5]:
                    has_negative = art.get("has_negative_keywords", False)
                    item = TriageItem(
                        id=str(uuid.uuid4()),
                        job_id=job_id,
                        module="adverse_media",
                        severity="critical" if has_negative else "medium",
                        title=f"Adverse media: {art.get('title', 'Unknown')[:100]}",
                        description=f"Source: {art.get('source', 'Unknown')}\n"
                        f"Date: {art.get('date', 'N/A')}\n"
                        f"URL: {art.get('url', '')}",
                        raw_data=art,
                    )
                    await self.state.add_triage_item(item)

        # Check litigation
        for lr in results.get("litigation", []):
            if lr.get("status") == "flagged":
                flagged_modules.append("litigation")
                for case in lr.get("cases", [])[:5]:
                    item = TriageItem(
                        id=str(uuid.uuid4()),
                        job_id=job_id,
                        module="litigation",
                        severity="high",
                        title=f"Litigation: {case.get('case_name', 'Unknown')[:100]}",
                        description=f"Court: {case.get('court', 'N/A')}\n"
                        f"Type: {case.get('case_type', 'N/A')}\n"
                        f"Status: {case.get('status', 'N/A')}\n"
                        f"URL: {case.get('url', '')}",
                        raw_data=case,
                    )
                    await self.state.add_triage_item(item)

        # Update job triage status
        job = await self.state.get_job(job_id)
        if job and flagged_modules:
            job.triage_status = "flagged"
            job.flagged_modules = list(set(flagged_modules))
        elif job:
            job.triage_status = "clear"
        await self.state.update_job(job)

    # --- Report generation ---

    def _generate_markdown_report(
        self,
        results: Dict[str, Any],
        name_en: str,
        name_he: Optional[str],
    ) -> str:
        """Generate a Markdown report from native screening results."""
        lines = [
            "# OSINT Due Diligence Report",
            "",
            f"**Subject:** {name_en}"
            + (f" / {name_he}" if name_he else ""),
            f"**Report Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
            "**Engine:** Native Screening Engine v2",
            "",
            "---",
            "",
        ]

        # Executive Summary
        lines.extend(["## Executive Summary", ""])
        total_flags = 0
        for section in [
            "jurisdiction",
            "static_sanctions",
            "adverse_media",
            "litigation",
        ]:
            for r in results.get(section, []):
                if r.get("status") == "flagged":
                    total_flags += 1
        if total_flags == 0:
            lines.extend(
                ["✅ **CLEAR** — No flags raised across all checked sources.", ""]
            )
        else:
            lines.extend(
                [
                    f"🚩 **{total_flags} FLAG(S) RAISED** — Manual review recommended.",
                    "",
                ]
            )

        # Jurisdiction Results
        lines.extend(["## Sanctions Screening", ""])
        for jr in results.get("jurisdiction", []):
            status_icon = (
                "🚩"
                if jr.get("status") == "flagged"
                else "✅"
                if jr.get("status") == "clear"
                else "⚠️"
            )
            lines.append(
                f"### {status_icon} {jr.get('jurisdiction_name', 'Unknown')}"
            )
            lines.append(f"- **Status:** {jr.get('status', 'unknown').upper()}")
            if jr.get("findings"):
                lines.append(
                    f"- **Findings:** {len(jr.get('findings', []))}"
                )
                for f in jr["findings"]:
                    lines.append(
                        f"  - {f.get('matched_name', 'Unknown')} "
                        f"(score: {f.get('match_score', 'N/A')})"
                    )
            lines.append("")

        # Static Sanctions
        lines.extend(["## Static Sanctions Lists", ""])
        for sr in results.get("static_sanctions", []):
            status_icon = (
                "🚩"
                if sr.get("status") == "flagged"
                else "✅"
                if sr.get("status") == "clear"
                else "📥"
            )
            lines.append(f"### {status_icon} {sr.get('source_name', 'Unknown')}")
            lines.append(f"- **Status:** {sr.get('status', 'unknown').upper()}")
            lines.append(f"- **Total Entries:** {sr.get('total_entries', 0)}")
            if sr.get("matches"):
                lines.append(f"- **Matches:** {len(sr.get('matches', []))}")
                for m in sr["matches"]:
                    entry = m.get("entry", {})
                    lines.append(
                        f"  - {entry.get('name', 'Unknown')} "
                        f"({entry.get('type', '')}) — "
                        f"matched on: {m.get('matched_on', 'name')}"
                    )
            lines.append("")

        # Adverse Media
        lines.extend(["## Adverse Media Screening", ""])
        for mr in results.get("adverse_media", []):
            status_icon = "🚩" if mr.get("status") == "flagged" else "✅"
            lines.append(f"### {status_icon} {mr.get('source_name', 'Unknown')}")
            lines.append(f"- **Status:** {mr.get('status', 'unknown').upper()}")
            lines.append(
                f"- **Articles Found:** {mr.get('articles_found', 0)}"
            )
            if mr.get("articles"):
                for a in mr["articles"]:
                    negative_icon = "⚠️" if a.get("has_negative_keywords") else ""
                    lines.append(
                        f"  - [{a.get('title', 'Unknown')[:80]}]"
                        f"({a.get('url', '')}) {negative_icon}"
                    )
                    lines.append(
                        f"    Source: {a.get('source', 'N/A')} | "
                        f"Date: {a.get('date', 'N/A')}"
                    )
            lines.append("")

        # Litigation
        lines.extend(["## Litigation Screening", ""])
        for lr in results.get("litigation", []):
            status_icon = "🚩" if lr.get("status") == "flagged" else "✅"
            lines.append(f"### {status_icon} {lr.get('source_name', 'Unknown')}")
            lines.append(f"- **Status:** {lr.get('status', 'unknown').upper()}")
            lines.append(f"- **Cases Found:** {lr.get('cases_found', 0)}")
            if lr.get("cases"):
                for c in lr["cases"]:
                    lines.append(
                        f"  - **{c.get('case_name', 'Unknown')[:80]}**"
                    )
                    lines.append(
                        f"    Court: {c.get('court', 'N/A')} | "
                        f"Type: {c.get('case_type', 'N/A')} | "
                        f"Status: {c.get('status', 'N/A')}"
                    )
                    if c.get("url"):
                        lines.append(f"    [View Case]({c['url']})")
            lines.append("")

        # Red Team Analysis
        lines.extend(
            [
                "---",
                "",
                "## Red Team Analysis",
                "",
                "### Confidence Assessment",
                "| Factor | Assessment |",
                "|--------|-----------|",
                "| Data Source Diversity | High — Multiple independent sources consulted |",
                "| Name Matching | Substring + alias matching applied |",
                "| Temporal Coverage | Current (APIs queried in real-time) |",
                "| False Positive Risk | Medium — Substring matching may over-match |",
                "",
                "### Limitations",
                "- Free API tiers have rate limits — bulk screening may be throttled",
                "- Static sanctions lists are cached for 24h — very recent additions may be missed",
                "- Adverse media results may include unrelated individuals with similar names",
                "- Litigation coverage is limited to publicly accessible court databases",
                "",
                "### Recommendations",
                "1. Review all flagged items manually before making compliance decisions",
                "2. For high-risk subjects, consider upgrading to paid API tiers",
                "3. Cross-reference findings across multiple sources for higher confidence",
                "4. Document all decisions for audit purposes",
                "",
                "---",
                "*Report generated by OSINT DD Dashboard Native Engine*",
            ]
        )

        return "\n".join(lines)

    # --- WebSocket helpers ---

    async def _broadcast_log(self, job_id: str, message: str) -> None:
        msg = WSMessage(
            type="log", job_id=job_id, payload=f"[NATIVE] {message}"
        )
        try:
            await self.state.broadcast_to_job(job_id, msg.model_dump_json())
        except Exception:
            pass

    async def _broadcast_status(
        self, job_id: str, status: str, progress: int
    ) -> None:
        msg = WSMessage(
            type="status",
            job_id=job_id,
            payload={"status": status, "progress": progress},
        )
        try:
            await self.state.broadcast_to_job(job_id, msg.model_dump_json())
        except Exception:
            pass

    async def _broadcast_progress(
        self, job_id: str, completed: int, total: int
    ) -> None:
        pct = int((completed / total * 100)) if total > 0 else 0
        await self._broadcast_status(job_id, "running", pct)

    async def _broadcast_complete(
        self, job: ScreeningJob, results: Dict[str, Any]
    ) -> None:
        msg = WSMessage(
            type="complete",
            job_id=job.id,
            payload={
                "status": job.status,
                "triage_status": job.triage_status,
                "flagged_modules": job.flagged_modules,
                "sources_checked": (
                    len(results.get("jurisdiction", []))
                    + len(results.get("adverse_media", []))
                    + len(results.get("litigation", []))
                    + len(results.get("static_sanctions", []))
                ),
            },
        )
        try:
            await self.state.broadcast_to_job(job.id, msg.model_dump_json())
        except Exception:
            pass
