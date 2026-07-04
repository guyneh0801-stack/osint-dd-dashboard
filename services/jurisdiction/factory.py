"""Factory for creating jurisdiction-specific screening configurations.

Given a jurisdiction hint (free text or canonical code), the factory
returns a :class:`JurisdictionConfig` object that tells the screening
engine which data sources to query and how to rank results.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from services.jurisdiction.base import JurisdictionRegistry


@dataclass
class JurisdictionConfig:
    """Immutable configuration for a jurisdiction-specific screening run."""

    jurisdiction_code: str
    jurisdiction_name: str
    source_codes: List[str] = field(default_factory=list)
    name_normaliser: Optional[callable] = None
    relevance_boost: float = 1.0
    extra_params: dict = field(default_factory=dict)


def create_config(hint: Optional[str] = None) -> JurisdictionConfig:
    """Build a :class:`JurisdictionConfig` from an optional jurisdiction hint.

    Parameters
    ----------
    hint:
        Free-text hint such as ``"United States"``, ``"US"``, ``"OFAC"``,
        or *None* for global screening (all sources).

    Returns
    -------
    JurisdictionConfig
        A config object ready to pass to the screening engine.
    """
    registry = JurisdictionRegistry()

    if hint is None or not hint.strip():
        # Global screening — use all sources from all plugins
        all_sources = []
        for plugin in registry.all_plugins():
            all_sources.extend(plugin.relevant_sources())
        return JurisdictionConfig(
            jurisdiction_code="GLOBAL",
            jurisdiction_name="Global",
            source_codes=list(dict.fromkeys(all_sources)),  # preserve order, dedup
        )

    # Try to resolve the hint
    code = registry.resolve_hint(hint)
    if code is None:
        # Unrecognised hint — fall back to global
        all_sources = []
        for plugin in registry.all_plugins():
            all_sources.extend(plugin.relevant_sources())
        return JurisdictionConfig(
            jurisdiction_code="GLOBAL",
            jurisdiction_name="Global",
            source_codes=list(dict.fromkeys(all_sources)),
        )

    plugin = registry.get(code)
    if plugin is None:
        # Should not happen, but handle defensively
        return JurisdictionConfig(
            jurisdiction_code=code,
            jurisdiction_name=code,
            source_codes=[],
        )

    return JurisdictionConfig(
        jurisdiction_code=plugin.code,
        jurisdiction_name=plugin.name,
        source_codes=plugin.relevant_sources(),
        name_normaliser=plugin.normalise_name,
        relevance_boost=1.0,
    )
