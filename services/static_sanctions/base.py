"""Abstract base for static sanctions list adapters (XML download + cache + search)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class SanctionsEntry(BaseModel):
    """A single entry parsed from a sanctions list.

    Attributes:
        name: Primary name of the sanctioned individual or entity.
        type: One of ``"individual"``, ``"entity"``, ``"vessel"``, ``"aircraft"``.
        program: Sanctions program code (e.g. ``"SDGT"``).
        dates: Associated dates (birth dates, issue dates, etc.).
        identifiers: Passport numbers, national IDs, etc.
        aliases: Alternative names (AKA).
        addresses: Physical addresses.
        nationality: Nationality or country of origin.
        source_list: Human-readable name of the source list.
    """

    name: str
    type: str  # "individual" | "entity" | "vessel" | "aircraft"
    program: Optional[str] = None
    dates: List[str] = []
    identifiers: List[str] = []
    aliases: List[str] = []
    addresses: List[str] = []
    nationality: Optional[str] = None
    source_list: Optional[str] = None


class StaticSanctionsResult(BaseModel):
    """The outcome of screening a subject against a static sanctions list.

    Attributes:
        source_code: Machine-readable code, e.g. ``"ofac_xml"``.
        source_name: Human-readable name, e.g. ``"OFAC SDN (XML)"``.
        status: One of ``"clear"``, ``"flagged"``, ``"error"``, ``"timeout"``,
            or ``"not_downloaded"``.
        matches: Structured match records with entry, score, and matched_on field.
        checked_at: ISO-8601 UTC timestamp.
        source_url: URL of the primary data source.
        list_date: When the remote list was last updated (if known).
        total_entries: Total number of entries in the cached list.
        cache_age_hours: Age of the cached file in hours.
    """

    source_code: str
    source_name: str
    status: Literal["clear", "flagged", "error", "timeout", "not_downloaded"]
    matches: List[Dict[str, Any]] = []
    checked_at: str
    source_url: Optional[str] = None
    list_date: Optional[str] = None
    total_entries: int = 0
    cache_age_hours: Optional[float] = None


# ---------------------------------------------------------------------------
# Abstract source
# ---------------------------------------------------------------------------


class StaticSanctionsSource(ABC):
    """Abstract base for static sanctions list adapters.

    Subclasses must provide *code*, *name*, *xml_url*,
    *cache_file_name*, ``download_and_parse``, and ``query``
    implementations.  The base class supplies a simple consecutive-failure
    circuit breaker (5 failures disables the source until restart).
    """

    # Circuit-breaker threshold
    _CIRCUIT_BREAKER_LIMIT: int = 5

    def __init__(self) -> None:
        self._failure_count: int = 0
        self._entries: List[SanctionsEntry] = []
        self._loaded: bool = False

    # -- Identifiers -------------------------------------------------------

    @property
    @abstractmethod
    def code(self) -> str:
        """Machine-readable source code, e.g. ``"ofac_xml"``."""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable source name, e.g. ``"OFAC SDN (XML)"``."""
        ...

    @property
    @abstractmethod
    def xml_url(self) -> str:
        """URL to download the XML sanctions list."""
        ...

    @property
    @abstractmethod
    def cache_file_name(self) -> str:
        """Local filename to use when caching the downloaded file."""
        ...

    # -- Circuit breaker ---------------------------------------------------

    @property
    def failure_count(self) -> int:
        """Number of consecutive failures recorded for this source."""
        return self._failure_count

    def record_failure(self) -> None:
        """Increment the consecutive-failure counter."""
        self._failure_count += 1

    def record_success(self) -> None:
        """Reset the consecutive-failure counter to zero."""
        self._failure_count = 0

    def is_available(self) -> bool:
        """Return ``True`` when the source is healthy (fewer than 5 consecutive failures).

        Once the failure threshold is reached the source stays disabled
        for the lifetime of the process.  This is intentional: a manual
        restart (or operator intervention) is required to re-enable it.
        """
        return self._failure_count < self._CIRCUIT_BREAKER_LIMIT

    # -- Download & parse --------------------------------------------------

    @abstractmethod
    async def download_and_parse(self, cache_dir: str) -> bool:
        """Download the XML file to *cache_dir* and parse entries.

        Returns ``True`` when the file was successfully downloaded and
        parsed.  On failure the source should record a failure via
        ``record_failure`` and return ``False``.
        """
        ...

    # -- Query -------------------------------------------------------------

    @abstractmethod
    async def query(
        self, name_en: str, name_he: Optional[str], cache_dir: str
    ) -> StaticSanctionsResult:
        """Screen *name_en* (and optionally *name_he*) against this static list.

        Returns a fully populated ``StaticSanctionsResult``.  The caller
        is responsible for wrapping this call with timeout logic.
        """
        ...

    # -- Helpers -----------------------------------------------------------

    def _make_result(
        self,
        status: Literal["clear", "flagged", "error", "timeout", "not_downloaded"],
        matches: Optional[List[Dict[str, Any]]] = None,
        source_url: Optional[str] = None,
        total_entries: int = 0,
        cache_age_hours: Optional[float] = None,
    ) -> StaticSanctionsResult:
        """Build a ``StaticSanctionsResult`` with boilerplate fields pre-filled."""
        return StaticSanctionsResult(
            source_code=self.code,
            source_name=self.name,
            status=status,
            matches=matches or [],
            checked_at=datetime.now(timezone.utc).isoformat(),
            source_url=source_url,
            total_entries=total_entries,
            cache_age_hours=cache_age_hours,
        )
