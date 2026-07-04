"""Litigation search system for the OSINT DD Dashboard.

Search court records and legal cases from multiple litigation sources
(US CourtListener, EU Court of Justice via ECLI) with circuit-breaker
protection and resilient error handling.

Quick start::

    from services.litigation import get_litigation_sources

    sources = get_litigation_sources()
    for source in sources:
        result = await source.query("John Doe", None)
        print(result.source_code, result.status, result.cases_found)
"""

from __future__ import annotations

from .base import CaseResult, LitigationResult, LitigationSource
from .courtlistener import CourtListenerSource
from .ecli import ECLISource

__all__ = [
    # Core types
    "CaseResult",
    "LitigationResult",
    "LitigationSource",
    # Concrete adapters
    "CourtListenerSource",
    "ECLISource",
    # Factory helper
    "get_litigation_sources",
]


def get_litigation_sources() -> list[LitigationSource]:
    """Return all enabled litigation source instances."""
    return [CourtListenerSource(), ECLISource()]
