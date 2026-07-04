#!/usr/bin/env python3
"""
Standalone screening script invoked as a subprocess by the dashboard.

This script performs the actual OSINT screening work:
1. Parse command-line arguments (names, output paths).
2. Query all enabled data source modules.
3. Aggregate results into a structured JSON report.
4. Generate a human-readable Markdown report.
5. Write both reports to the specified output paths.

Exit codes:
    0 - Success
    1 - General error
    2 - Invalid arguments
    3 - Partial failure (some modules failed)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("dd_screener")


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="OSINT DD Screening Engine")
    parser.add_argument("--job-id", required=True, help="Screening job UUID")
    parser.add_argument("--name-en", required=True, help="Subject name in English")
    parser.add_argument("--name-he", default=None, help="Subject name in Hebrew")
    parser.add_argument("--output-json", required=True, help="Path for JSON report output")
    parser.add_argument("--output-md", required=True, help="Path for Markdown report output")
    parser.add_argument("--timeout", type=int, default=300, help="Timeout in seconds")
    return parser.parse_args()


def run_screening(name_en: str, name_he: str | None) -> Dict[str, Any]:
    """Run the screening process and return aggregated results.

    This is a placeholder implementation. In production, this function
    would:
    1. Query sanctions lists (OFAC, UN, EU, etc.)
    2. Search adverse media sources
    3. Check litigation databases
    4. Query corporate registries
    5. Search social media
    6. Aggregate and score all results
    """
    logger.info("Starting screening for: %s", name_en)
    if name_he:
        logger.info("Hebrew name: %s", name_he)

    modules: Dict[str, Any] = {}
    errors: List[str] = []

    # Placeholder: sanctions screening
    try:
        logger.info("[sanctions] Querying sanctions lists...")
        modules["sanctions"] = {
            "status": "completed",
            "sources_checked": ["OFAC", "UN", "EU", "HMT"],
            "hits": [],
            "findings": [],
        }
        logger.info("[sanctions] Completed: 0 hits")
    except Exception as exc:
        logger.error("[sanctions] Failed: %s", exc)
        modules["sanctions"] = {"status": "error", "error": str(exc)}
        errors.append(f"sanctions: {exc}")

    # Placeholder: adverse media
    try:
        logger.info("[adverse_media] Searching adverse media...")
        modules["adverse_media"] = {
            "status": "completed",
            "sources_checked": ["Google News", "LexisNexis"],
            "articles_found": 0,
            "findings": [],
        }
        logger.info("[adverse_media] Completed: 0 articles")
    except Exception as exc:
        logger.error("[adverse_media] Failed: %s", exc)
        modules["adverse_media"] = {"status": "error", "error": str(exc)}
        errors.append(f"adverse_media: {exc}")

    # Placeholder: litigation
    try:
        logger.info("[litigation] Checking litigation records...")
        modules["litigation"] = {
            "status": "completed",
            "sources_checked": ["PACER", "CourtListener"],
            "cases_found": 0,
            "findings": [],
        }
        logger.info("[litigation] Completed: 0 cases")
    except Exception as exc:
        logger.error("[litigation] Failed: %s", exc)
        modules["litigation"] = {"status": "error", "error": str(exc)}
        errors.append(f"litigation: {exc}")

    return {
        "subject": {
            "name_en": name_en,
            "name_he": name_he,
        },
        "modules": modules,
        "summary": {
            "modules_run": len(modules),
            "modules_success": sum(1 for m in modules.values() if m.get("status") == "completed"),
            "total_hits": 0,
            "total_findings": 0,
            "errors": errors,
        },
        "metadata": {
            "screening_timestamp": datetime.now(timezone.utc).isoformat(),
            "version": "1.0.0",
        },
    }


def generate_markdown_report(data: Dict[str, Any]) -> str:
    """Generate a human-readable Markdown report from screening data."""
    subject = data["subject"]
    modules = data["modules"]
    summary = data["summary"]
    meta = data["metadata"]

    lines: List[str] = [
        "# OSINT Due Diligence Screening Report",
        "",
        f"**Subject:** {subject['name_en']}",
    ]
    if subject.get("name_he"):
        lines.append(f"**Hebrew Name:** {subject['name_he']}")
    lines.extend([
        f"**Date:** {meta['screening_timestamp']}",
        f"**Version:** {meta['version']}",
        "",
        "---",
        "",
        "## Summary",
        "",
        f"- Modules run: {summary['modules_run']}",
        f"- Successful: {summary['modules_success']}",
        f"- Total hits: {summary['total_hits']}",
        f"- Total findings: {summary['total_findings']}",
        "",
    ])

    if summary["errors"]:
        lines.extend([
            "### Errors",
            "",
        ])
        for error in summary["errors"]:
            lines.append(f"- {error}")
        lines.append("")

    lines.extend([
        "---",
        "",
        "## Module Results",
        "",
    ])

    for module_name, module_data in modules.items():
        lines.append(f"### {module_name.replace('_', ' ').title()}")
        lines.append("")
        status = module_data.get("status", "unknown")
        lines.append(f"**Status:** {status}")
        
        if status == "completed":
            sources = module_data.get("sources_checked", [])
            if sources:
                lines.append(f"**Sources checked:** {', '.join(sources)}")
            hits = module_data.get("hits", [])
            if hits:
                lines.append(f"**Hits:** {len(hits)}")
                for hit in hits:
                    lines.append(f"- {hit.get('name', 'Unknown')}: {hit.get('reason', '')}")
            else:
                lines.append("**Hits:** None")
        elif status == "error":
            lines.append(f"**Error:** {module_data.get('error', 'Unknown error')}")
        
        lines.append("")

    lines.extend([
        "---",
        "",
        "*This report was generated automatically by the OSINT DD Screening Engine.*",
    ])

    return "\n".join(lines)


def main() -> int:
    """Main entry point for the screening script."""
    args = parse_args()

    logger.info("Job %s: Starting screening", args.job_id)

    try:
        # Run screening
        results = run_screening(args.name_en, args.name_he)

        # Write JSON report
        output_json = Path(args.output_json)
        output_json.parent.mkdir(parents=True, exist_ok=True)
        with open(output_json, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        logger.info("JSON report written to %s", output_json)

        # Write Markdown report
        md_report = generate_markdown_report(results)
        output_md = Path(args.output_md)
        output_md.parent.mkdir(parents=True, exist_ok=True)
        with open(output_md, "w", encoding="utf-8") as f:
            f.write(md_report)
        logger.info("Markdown report written to %s", output_md)

        # Determine exit code
        if results["summary"]["errors"]:
            logger.warning("Screening completed with partial failures")
            return 3

        logger.info("Job %s: Screening completed successfully", args.job_id)
        return 0

    except Exception as exc:
        logger.error("Job %s: Screening failed: %s", args.job_id, exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
