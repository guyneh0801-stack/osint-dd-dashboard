"""Source registry — tracks all available OSINT data sources and their health."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from services.jurisdiction import get_jurisdiction_sources
from services.adverse_media import get_media_sources
from services.litigation import get_litigation_sources
from services.static_sanctions import get_static_sanctions_sources


@dataclass
class SourceInfo:
    """Metadata about a single OSINT data source."""

    code: str
    name: str
    category: str  # "sanctions" | "adverse_media" | "litigation" | "static_sanctions"
    status: str  # "available" | "unavailable" | "disabled"
    priority_tier: int = 3
    description: str = ""
    requires_api_key: bool = False
    api_key_env_var: Optional[str] = None
    is_free: bool = True


class SourceRegistry:
    """Registry of all OSINT data sources with health check capability."""

    _instance: Optional["SourceRegistry"] = None

    def __new__(cls) -> "SourceRegistry":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._sources: List[SourceInfo] = []
        return cls._instance

    def refresh(self) -> List[SourceInfo]:
        """Rebuild the source list with current availability."""
        self._sources = []

        # Jurisdiction sources
        for s in get_jurisdiction_sources():
            self._sources.append(
                SourceInfo(
                    code=s.code,
                    name=s.name,
                    category="sanctions",
                    status="available" if s.is_available() else "unavailable",
                    priority_tier=getattr(s, "priority_tier", 3),
                    description="Sanctions list via OpenSanctions",
                    is_free=True,
                )
            )

        # Static sanctions
        for s in get_static_sanctions_sources():
            self._sources.append(
                SourceInfo(
                    code=s.code,
                    name=s.name,
                    category="static_sanctions",
                    status="available" if s.is_available() else "unavailable",
                    description="Static XML sanctions list",
                    is_free=True,
                )
            )

        # Adverse media
        for s in get_media_sources():
            self._sources.append(
                SourceInfo(
                    code=s.code,
                    name=s.name,
                    category="adverse_media",
                    status="available" if s.is_available() else "unavailable",
                    description="Adverse media search",
                    is_free=True,
                )
            )

        # Litigation
        for s in get_litigation_sources():
            requires_key = s.code == "courtlistener"
            self._sources.append(
                SourceInfo(
                    code=s.code,
                    name=s.name,
                    category="litigation",
                    status="available" if s.is_available() else "unavailable",
                    description="Court records search",
                    requires_api_key=requires_key,
                    api_key_env_var="COURTLISTENER_TOKEN" if requires_key else None,
                    is_free=True,
                )
            )

        return self._sources

    def get_sources(
        self, category: Optional[str] = None
    ) -> List[SourceInfo]:
        """Return all sources, optionally filtered by category."""
        if not self._sources:
            self.refresh()
        if category:
            return [s for s in self._sources if s.category == category]
        return self._sources

    def get_source(self, code: str) -> Optional[SourceInfo]:
        """Return a single source by its code."""
        for s in self._sources:
            if s.code == code:
                return s
        return None

    def get_health_summary(self) -> dict:
        """Return a health summary across all sources."""
        sources = self.refresh()
        total = len(sources)
        available = sum(1 for s in sources if s.status == "available")
        unavailable = sum(1 for s in sources if s.status == "unavailable")
        by_category: dict = {}
        for s in sources:
            by_category.setdefault(s.category, {"total": 0, "available": 0})
            by_category[s.category]["total"] += 1
            if s.status == "available":
                by_category[s.category]["available"] += 1

        return {
            "overall": "healthy" if unavailable == 0 else "degraded" if available > 0 else "unhealthy",
            "total_sources": total,
            "available": available,
            "unavailable": unavailable,
            "by_category": by_category,
        }


# Singleton instance
source_registry = SourceRegistry()
