"""Abstract base for adverse-media source adapters.

Defines the shared interface that every media adapter must implement,
including the circuit-breaker pattern and the ``MediaResult`` model
used across the adverse-media screening pipeline.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------


class MediaResult(BaseModel):
    """The outcome of screening a subject against a single adverse-media source.

    Attributes:
        source_code: Machine-readable code, e.g. ``"gdelt"``.
        source_name: Human-readable name, e.g. ``"GDELT Global Database"``.
        status: One of ``"clear"``, ``"flagged"``, ``"error"``, or ``"timeout"``.
        articles: Structured article records (empty when *status* is ``"clear"``).
                  Each article contains ``title``, ``url``, ``date``, ``source``,
                  and ``snippet`` keys.
        checked_at: ISO-8601 UTC timestamp of when the check ran.
        source_url: Direct URL to the primary data source (if available).
        articles_found: Total number of articles discovered by the source.
    """

    source_code: str
    source_name: str
    status: Literal["clear", "flagged", "error", "timeout"]
    articles: List[Dict[str, Any]] = []
    checked_at: str
    source_url: Optional[str] = None
    articles_found: int = 0


# ---------------------------------------------------------------------------
# Abstract source
# ---------------------------------------------------------------------------


class MediaSource(ABC):
    """Abstract base for every adverse-media source adapter.

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
        """Machine-readable source code, e.g. ``"gdelt"``."""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable source name, e.g. ``"GDELT Global Database"``."""
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
    async def query(self, name_en: str, name_he: Optional[str]) -> MediaResult:
        """Search for adverse media about *name_en* (and optionally *name_he*).

        Returns a fully populated ``MediaResult``.  The caller is
        responsible for wrapping this call with timeout and circuit-breaker
        logic.
        """
        ...

    # -- Helpers ------------------------------------------------------------

    def _make_result(
        self,
        status: Literal["clear", "flagged", "error", "timeout"],
        articles: Optional[List[Dict[str, Any]]] = None,
        source_url: Optional[str] = None,
        articles_found: int = 0,
    ) -> MediaResult:
        """Build a ``MediaResult`` with boilerplate fields pre-filled."""
        return MediaResult(
            source_code=self.code,
            source_name=self.name,
            status=status,
            articles=articles or [],
            checked_at=datetime.now(timezone.utc).isoformat(),
            source_url=source_url,
            articles_found=articles_found,
        )
