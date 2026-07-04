"""Static sanctions downloader system for the OSINT DD Dashboard.

Downloads and caches XML sanctions lists from OFAC, EU, and UN,
then provides a search interface for screening subjects against
locally-stored static copies of the lists.

Quick start::

    from services.static_sanctions import get_static_sanctions_sources

    sources = get_static_sanctions_sources()
    for source in sources:
        result = await source.query("John Doe", None, "/tmp/sanctions_cache")
        print(result.source_code, result.status)
"""

from __future__ import annotations

from .base import SanctionsEntry, StaticSanctionsResult, StaticSanctionsSource
from .downloader import StaticSanctionsDownloader
from .ofac import OFACXMLSource
from .eu import EUSanctionsXMLSource
from .un import UNConsolidatedXMLSource

__all__ = [
    "SanctionsEntry",
    "StaticSanctionsResult",
    "StaticSanctionsSource",
    "StaticSanctionsDownloader",
    "OFACXMLSource",
    "EUSanctionsXMLSource",
    "UNConsolidatedXMLSource",
    "get_static_sanctions_sources",
]


def get_static_sanctions_sources() -> list:
    """Return a list of all configured static sanctions source adapters."""
    return [OFACXMLSource(), EUSanctionsXMLSource(), UNConsolidatedXMLSource()]
