"""Adverse media screening system for the OSINT DD Dashboard.

Search for negative news coverage about a subject across multiple free
media sources (GDELT Global Database, Google News RSS) with circuit-breaker
protection and mock-safe error handling.

Quick start::

    from services.adverse_media import get_media_sources

    sources = get_media_sources()
    for src in sources:
        result = await src.query("John Doe", None)
        print(result.source_code, result.status, result.articles_found)
"""

from __future__ import annotations

from .base import MediaResult, MediaSource
from .gdelt import GDELTSource
from .google_news import GoogleNewsRSSSource

__all__ = [
    # Core types
    "MediaResult",
    "MediaSource",
    # Concrete adapters
    "GDELTSource",
    "GoogleNewsRSSSource",
    # Factory
    "get_media_sources",
]


def get_media_sources() -> list[MediaSource]:
    """Return all enabled adverse-media source instances.

    Returns:
        A list of instantiated ``MediaSource`` subclasses ready for
        ``query()`` calls.
    """
    return [GDELTSource(), GoogleNewsRSSSource()]
