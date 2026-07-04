"""Base abstractions for jurisdiction-aware screening.

Each supported legal / regulatory jurisdiction implements a
:class:`JurisdictionPlugin` that knows how to:

* Map a free-text jurisdiction hint to a canonical code.
* Identify the data sources (sanctions lists, watch-lists, court
  systems) that are relevant for that jurisdiction.
* Translate local name conventions into the normalised forms used by
  the screening engine.
* Rank results by local relevance (e.g. a hit on a national sanctions
  list outranks a foreign one).

The :class:`JurisdictionRegistry` collects all installed plugins and
provides a unified lookup interface.
"""

from __future__ import annotations

import importlib
import pkgutil
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Type

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Data models for source-based jurisdiction screening (used by sources.py)
# ---------------------------------------------------------------------------

class JurisdictionResult(BaseModel):
    """Result from a single jurisdiction source check."""

    jurisdiction_code: str = ""
    jurisdiction_name: str = ""
    status: str = "clear"  # "clear" | "flagged" | "error" | "timeout"
    findings: List[Dict[str, Any]] = Field(default_factory=list)
    checked_at: str = ""
    source_url: Optional[str] = None


class JurisdictionSource(ABC):
    """Abstract base for jurisdiction-based sanctions sources.

    Each concrete subclass represents one sanctions list from a
    specific jurisdiction.  Used by :py:mod:`services.jurisdiction.sources`.
    """

    _CIRCUIT_BREAKER_LIMIT: int = 5

    def __init__(self) -> None:
        self._failure_count: int = 0

    @property
    @abstractmethod
    def code(self) -> str:
        """Short canonical code, e.g. ``"us_ofac"``, ``"un"``."""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable name, e.g. ``"US OFAC SDN"``."""
        ...

    @property
    def priority_tier(self) -> int:
        """Lower = higher priority.  Override in subclasses if needed."""
        return 3

    def record_failure(self) -> None:
        self._failure_count += 1

    def record_success(self) -> None:
        self._failure_count = 0

    def is_available(self) -> bool:
        return self._failure_count < self._CIRCUIT_BREAKER_LIMIT

    @abstractmethod
    async def query(
        self, name_en: str, name_he: Optional[str]
    ) -> JurisdictionResult:
        """Run the jurisdiction check and return a :class:`JurisdictionResult`."""
        ...

    def _make_result(
        self,
        status: str,
        findings: Optional[List[Dict[str, Any]]] = None,
        source_url: Optional[str] = None,
    ) -> JurisdictionResult:
        return JurisdictionResult(
            jurisdiction_code=self.code,
            jurisdiction_name=self.name,
            status=status,
            findings=findings or [],
            checked_at=datetime.now(timezone.utc).isoformat(),
            source_url=source_url,
        )


# ---------------------------------------------------------------------------
# Data models for plugin-based jurisdiction screening (used by factory.py)
# ---------------------------------------------------------------------------

class JurisdictionMatch(BaseModel):
    """A single sanctions / watch-list match augmented with jurisdiction metadata."""

    source_name: str = Field(..., description="Human-readable source name")
    source_code: str = Field(..., description="Machine source code")
    jurisdiction_code: str = Field(..., description="Canonical jurisdiction code")
    jurisdiction_name: str = Field(..., description="Human-readable jurisdiction name")
    local_relevance_score: float = Field(
        ..., ge=0.0, le=1.0, description="Relevance within this jurisdiction"
    )
    raw_match: Dict[str, Any] = Field(
        default_factory=dict, description="Original match data"
    )


class JurisdictionScreeningResult(BaseModel):
    """Aggregated results for a single jurisdiction."""

    jurisdiction_code: str
    jurisdiction_name: str
    matches: List[JurisdictionMatch] = Field(default_factory=list)
    sources_checked: List[str] = Field(default_factory=list)
    error_messages: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Plugin interface
# ---------------------------------------------------------------------------

class JurisdictionPlugin(ABC):
    """Abstract base class for jurisdiction-specific screening plugins.

    Attributes
    ----------
    code :
        Short canonical code, e.g. ``"US"``, ``"EU"``, ``"UK"``.
    name :
        Human-readable name, e.g. ``"United States"``.
    priority_tier :
        Lower numbers = higher priority.  Used when the analyst does not
        specify a jurisdiction — the system runs tier-1 plugins first.
    """

    code: str = ""
    name: str = ""
    priority_tier: int = 99

    @abstractmethod
    def canonicalise_hint(self, hint: str) -> Optional[str]:
        """Convert a free-text jurisdiction hint to this plugin's *code*.

        Returns *None* if the hint does not map to this jurisdiction.
        """
        ...

    @abstractmethod
    def relevant_sources(self) -> List[str]:
        """Return the list of source codes relevant for this jurisdiction.

        Example: ``["OFAC", "BIS", "FBI"]`` for the United States.
        """
        ...

    @abstractmethod
    def normalise_name(self, name: str) -> str:
        """Translate a name into the canonical form used by this jurisdiction.

        For most jurisdictions this is a no-op (return the name unchanged),
        but plugins may swap name order, expand abbreviations, etc.
        """
        ...

    @abstractmethod
    def relevance_rank(self, match: Dict[str, Any]) -> float:
        """Return a local-relevance score for *match*.

        Higher = more relevant for this jurisdiction.  The score is
        combined with the global entity-resolution score to produce the
        final ranking.
        """
        ...

    def screen(self, name_en: str, name_he: Optional[str] = None) -> JurisdictionScreeningResult:
        """Run jurisdiction-specific screening.

        The default implementation does nothing — concrete plugins may
        override this to add bespoke logic (e.g. querying a national
        court API).
        """
        return JurisdictionScreeningResult(
            jurisdiction_code=self.code,
            jurisdiction_name=self.name,
            sources_checked=[],
            matches=[],
            error_messages=[],
        )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class JurisdictionRegistry:
    """Collects and queries all installed :class:`JurisdictionPlugin`s."""

    _plugins: Dict[str, JurisdictionPlugin] = {}
    _initialised: bool = False

    @classmethod
    def _ensure_loaded(cls) -> None:
        if cls._initialised:
            return
        cls._initialised = True
        # Auto-discover plugins in the jurisdiction package
        import services.jurisdiction as pkg

        for _finder, mod_name, _ispkg in pkgutil.iter_modules(pkg.__path__, pkg.__name__ + "."):
            if mod_name.endswith((".base", ".factory", ".manager")):
                continue
            try:
                mod = importlib.import_module(mod_name)
                for attr in dir(mod):
                    obj = getattr(mod, attr)
                    if (
                        isinstance(obj, type)
                        and issubclass(obj, JurisdictionPlugin)
                        and obj is not JurisdictionPlugin
                        and getattr(obj, "code", "")
                    ):
                        instance = obj()
                        cls._plugins[instance.code] = instance
            except Exception:
                # Skip broken plugins
                pass

    @classmethod
    def all_plugins(cls) -> List[JurisdictionPlugin]:
        """Return all registered plugins sorted by priority tier."""
        cls._ensure_loaded()
        return sorted(cls._plugins.values(), key=lambda p: p.priority_tier)

    @classmethod
    def get(cls, code: str) -> Optional[JurisdictionPlugin]:
        """Fetch a plugin by its canonical code."""
        cls._ensure_loaded()
        return cls._plugins.get(code.upper())

    @classmethod
    def resolve_hint(cls, hint: str) -> Optional[str]:
        """Convert a free-text hint to the best-matching jurisdiction code."""
        cls._ensure_loaded()
        hint_norm = hint.strip().upper()
        for plugin in cls._plugins.values():
            code = plugin.canonicalise_hint(hint_norm)
            if code:
                return code
        return None

    @classmethod
    def relevant_sources_for(cls, code: str) -> List[str]:
        """Return the data sources relevant for *code*."""
        plugin = cls.get(code)
        if plugin is None:
            return []
        return plugin.relevant_sources()
