"""High-level jurisdiction manager that orchestrates multi-jurisdiction screening.

The manager is the entry point used by the screening engine.  It:

1. Determines which jurisdictions are relevant (from the analyst hint
   or default priority tiers).
2. Runs each jurisdiction's plugin in parallel.
3. Merges and deduplicates results.
4. Applies jurisdiction-specific relevance boosting.
5. Returns a unified list of :class:`JurisdictionMatch` objects.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from services.jurisdiction.base import (
    JurisdictionMatch,
    JurisdictionPlugin,
    JurisdictionRegistry,
    JurisdictionScreeningResult,
)
from services.jurisdiction.factory import JurisdictionConfig, create_config
from core.logger import get_logger

logger = get_logger(__name__)


class JurisdictionManager:
    """Orchestrates jurisdiction-aware screening."""

    def __init__(self, registry: Optional[JurisdictionRegistry] = None) -> None:
        self.registry = registry or JurisdictionRegistry()

    async def screen(
        self,
        name_en: str,
        name_he: Optional[str] = None,
        jurisdiction_hint: Optional[str] = None,
    ) -> List[JurisdictionScreeningResult]:
        """Run jurisdiction-aware screening.

        Parameters
        ----------
        name_en:
            Subject name in English.
        name_he:
            Optional subject name in Hebrew.
        jurisdiction_hint:
            Optional free-text jurisdiction hint.  When *None*, all
            installed plugins are run.

        Returns
        -------
        List[JurisdictionScreeningResult]
            One result object per jurisdiction screened.
        """
        config = create_config(jurisdiction_hint)
        plugins = self._select_plugins(config)

        logger.info(
            "Jurisdiction screening: hint=%s → plugins=%s",
            jurisdiction_hint,
            [p.code for p in plugins],
        )

        # Run all plugins concurrently
        results = await asyncio.gather(
            *[self._run_plugin(p, name_en, name_he) for p in plugins],
            return_exceptions=True,
        )

        output: List[JurisdictionScreeningResult] = []
        for plugin, result in zip(plugins, results):
            if isinstance(result, Exception):
                logger.error(
                    "Jurisdiction plugin %s failed: %s", plugin.code, result
                )
                output.append(
                    JurisdictionScreeningResult(
                        jurisdiction_code=plugin.code,
                        jurisdiction_name=plugin.name,
                        error_messages=[str(result)],
                    )
                )
            else:
                output.append(result)

        return output

    # --- Internal helpers ------------------------------------------------

    def _select_plugins(self, config: JurisdictionConfig) -> List[JurisdictionPlugin]:
        """Select the plugins to run based on the config."""
        if config.jurisdiction_code == "GLOBAL":
            return self.registry.all_plugins()

        plugin = self.registry.get(config.jurisdiction_code)
        if plugin is None:
            return self.registry.all_plugins()
        return [plugin]

    async def _run_plugin(
        self,
        plugin: JurisdictionPlugin,
        name_en: str,
        name_he: Optional[str],
    ) -> JurisdictionScreeningResult:
        """Run a single plugin and enrich its results."""
        # Normalise names if the plugin provides a custom normaliser
        norm_en = plugin.normalise_name(name_en) if plugin.normalise_name else name_en
        norm_he = plugin.normalise_name(name_he) if name_he and plugin.normalise_name else name_he

        result = plugin.screen(norm_en, norm_he)

        # Boost local relevance scores
        for match in result.matches:
            match.local_relevance_score = plugin.relevance_rank(match.raw_match)
            match.jurisdiction_code = plugin.code
            match.jurisdiction_name = plugin.name

        return result
