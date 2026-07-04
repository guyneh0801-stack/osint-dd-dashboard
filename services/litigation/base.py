"""Abstract base classes for litigation source adapters.

Defines the shared interface that every litigation adapter must implement,
including the circuit-breaker pattern and the ``LitigationResult`` model
used across the court-record search pipeline.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Case result model
# ---------------------------------------------------------------------------


class CaseResult(BaseModel):
    """A single legal case finding.

    Attributes:
        case_name: The name/title of the case.
        case_number: Case number or docket number, if available.
        court: The court where the case was filed or heard.
        date_filed: Date the case was filed (ISO-8601 string).
        date_decided: Date of decision/judgment (ISO-8601 string).
        status: Case status, e.g. ``"open"``, ``"closed"``, ``"appealed"``.
        url: Direct link to the case record.
        jurisdiction: Geographical jurisdiction, e.g. ``"US"``, ``"EU"``.
        case_type: Type of case, e.g. ``"civil"``, ``"criminal"``, ``"bankruptcy"``.
        parties: List of parties involved in the case.
        snippet: Short excerpt or summary of the case.
    """

    case_name: str
    case_number: Optional[str] = None
    court: Optional[str] = None
    date_filed: Optional[str] = None
    date_decided: Optional[str] = None
    status: Optional[str] = None  # "open", "closed", "appealed", etc.
    url: Optional[str] = None
    jurisdiction: Optional[str] = None
    case_type: Optional[str] = None  # "civil", "criminal", "bankruptcy", etc.
    parties: List[str] = []
    snippet: Optional[str] = None


# ---------------------------------------------------------------------------
# Litigation result model
# ---------------------------------------------------------------------------


class LitigationResult(BaseModel):
    """The outcome of screening a subject against a single litigation source.

    Attributes:
        source_code: Machine-readable code, e.g. ``"courtlistener"``.
        source_name: Human-readable name, e.g. ``"CourtListener (US Courts)"``.
        status: One of ``"clear"``, ``"flagged"``, ``"error"``, or ``"timeout"``.
        cases: List of matching cases (empty when *status* is ``"clear"``).
        checked_at: ISO-8601 UTC timestamp of when the check ran.
        source_url: Direct URL to the primary data source.
        cases_found: Number of matching cases found.
    """

    source_code: str
    source_name: str
    status: Literal["clear", "flagged", "error", "timeout"]
    cases: List[CaseResult] = []
    checked_at: str
    source_url: Optional[str] = None
    cases_found: int = 0


# ---------------------------------------------------------------------------
# Abstract source
# ---------------------------------------------------------------------------


class LitigationSource(ABC):
    """Abstract base for every litigation source adapter.

    Subclasses must provide *code*, *name*, and an async ``query``
    implementation.  The base class supplies a simple consecutive-failure
    circuit breaker (5 failures disables the source until restart).
    """

    # Circuit-breaker threshold
    _CIRCUIT_BREAKER_LIMIT: int = 5

    def __init__(self) -> None:
        self._failure_count: int = 0

    # -- Identifiers -------------------------------------------------------

    @property
    @abstractmethod
    def code(self) -> str:
        """Machine-readable source code, e.g. ``"courtlistener"``."""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable source name, e.g. ``"CourtListener (US Courts)"``."""
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

    # -- Query --------------------------------------------------------------

    @abstractmethod
    async def query(self, name_en: str, name_he: Optional[str]) -> LitigationResult:
        """Search for court cases matching *name_en* (and optionally *name_he*).

        Returns a fully populated ``LitigationResult``.  The caller is
        responsible for wrapping this call with timeout and circuit-breaker
        logic.
        """
        ...

    # -- Helpers ------------------------------------------------------------

    def _make_result(
        self,
        status: Literal["clear", "flagged", "error", "timeout"],
        cases: Optional[List[CaseResult]] = None,
        source_url: Optional[str] = None,
        cases_found: int = 0,
    ) -> LitigationResult:
        """Build a ``LitigationResult`` with boilerplate fields pre-filled."""
        return LitigationResult(
            source_code=self.code,
            source_name=self.name,
            status=status,
            cases=cases or [],
            source_url=source_url,
            checked_at=datetime.now(timezone.utc).isoformat(),
            cases_found=cases_found,
        )
