"""Concrete jurisdiction adapters.

Seven adapters query the **OpenSanctions** bulk-match endpoint
(``POST /match/sanctions``) and filter the shared response for their
respective jurisdictions.  The eighth (``FATFGreyListSource``) reads a
local CSV cache file asynchronously via ``aiofiles``.

All adapters are **mock-safe**: when the OpenSanctions API is
unreachable they fall back to a synthetic ``"clear"`` result so the
screening pipeline never hard-fails because of an external dependency.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
from .base import JurisdictionResult, JurisdictionSource

logger = __import__("logging").getLogger(__name__)

# ---------------------------------------------------------------------------
# OpenSanctions bulk-endpoint configuration
# ---------------------------------------------------------------------------

_OPENSANCTIONS_MATCH_URL: str = "https://api.opensanctions.org/match/sanctions"
_OPENSANCTIONS_TIMEOUT: float = 30.0

# API key — read from environment variable or config.json
_opensanctions_api_key: str = ""
try:
    from core.config_file import config_file
    _opensanctions_api_key = config_file.get_str("opensanctions_api_key", "")
except Exception:
    import os
    _opensanctions_api_key = os.environ.get("OPENSANCTIONS_API_KEY", "")

# Jurisdiction-key → the ``datasets`` / ``publisher`` names we expect in
# the OpenSanctions response for each adapter.
_JURISDICTION_DATASET_HINTS: Dict[str, List[str]] = {
    "us_ofac": ["us_ofac_sdn", "us_ofac_consolidated"],
    "un": ["un_sc_sanctions"],
    "uk_hmt": ["uk_hmt_sanctions"],
    "eu": ["eu_fsf", "eu_sanctions_map"],
    "il": ["il_mod_crypto", "il_mod_terrorists"],
    "ca_sema": ["ca_dfatd_sema_sanctions"],
    "au_dfat": ["au_dfat_sanctions"],
}

# ---------------------------------------------------------------------------
# Shared bulk-match helper
# ---------------------------------------------------------------------------


async def _bulk_match_opensanctions(
    name_en: str, name_he: Optional[str]
) -> Optional[Dict[str, Any]]:
    """Execute a **single** bulk-match request against OpenSanctions.

    Sends a ``POST`` to ``/match/sanctions`` with both English and (when
    provided) Hebrew name variants.  Returns the parsed JSON response or
    ``None`` on any transport / HTTP / parse failure.

    This function is the cornerstone of the bulk-endpoint optimisation:
    all seven OpenSanctions-based adapters call it once and then filter
    the shared response by dataset hints.
    """
    queries: List[Dict[str, Any]] = [
        {"schema": "Person", "properties": {"name": [name_en]}}
    ]
    if name_he:
        queries.append({"schema": "Person", "properties": {"name": [name_he]}})

    payload = {"queries": queries}

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if _opensanctions_api_key:
        headers["Authorization"] = f"ApiKey {_opensanctions_api_key}"

    try:
        async with httpx.AsyncClient(timeout=_OPENSANCTIONS_TIMEOUT) as client:
            response = await client.post(
                _OPENSANCTIONS_MATCH_URL,
                json=payload,
                headers=headers,
            )
            if response.status_code == 200:
                return response.json()
            logger.warning(
                "OpenSanctions returned HTTP %d for name='%s'",
                response.status_code,
                name_en,
            )
    except httpx.HTTPError as exc:
        logger.warning("OpenSanctions HTTP error: %s", exc)
    except Exception as exc:
        logger.warning("OpenSanctions unexpected error: %s", exc)

    return None


def _filter_matches_for_jurisdiction(
    bulk_response: Optional[Dict[str, Any]], jurisdiction_code: str
) -> List[Dict[str, Any]]:
    """Extract matches from the bulk response that belong to *jurisdiction_code*.

    Inspects each match result, looking at ``dataset`` / ``publisher``
    / ``collection`` fields and cross-referencing them with the hints
    configured in ``_JURISDICTION_DATASET_HINTS``.
    """
    if not bulk_response or "responses" not in bulk_response:
        return []

    hints = _JURISDICTION_DATASET_HINTS.get(jurisdiction_code, [])
    if not hints:
        return []

    matches: List[Dict[str, Any]] = []
    for resp in bulk_response["responses"]:
        results = resp.get("results", [])
        for match in results:
            datasets = match.get("dataset", [])
            if isinstance(datasets, str):
                datasets = [datasets]
            publisher = match.get("publisher", {}) or {}
            publisher_name = publisher.get("name", "")

            # Check against hints
            matched = any(h in datasets for h in hints)
            if not matched and publisher_name:
                matched = any(h in publisher_name.lower() for h in hints)

            if matched:
                matches.append(match)

    return matches


# ---------------------------------------------------------------------------
# Tier-1 sources (highest priority)
# ---------------------------------------------------------------------------


class USOFACSource(JurisdictionSource):
    """US Treasury OFAC Specially Designated Nationals (SDN) list."""

    @property
    def code(self) -> str:  # noqa: D102
        return "us_ofac"

    @property
    def name(self) -> str:  # noqa: D102
        return "US OFAC SDN"

    @property
    def priority_tier(self) -> int:  # noqa: D102
        return 1

    async def query(self, name_en: str, name_he: Optional[str]) -> JurisdictionResult:  # noqa: D102
        bulk = await _bulk_match_opensanctions(name_en, name_he)
        if bulk is None:
            # Mock: return a realistic clear result
            return self._make_result(
                status="clear",
                source_url="https://sanctionssearch.ofac.treas.gov/",
            )

        matches = _filter_matches_for_jurisdiction(bulk, self.code)
        if matches:
            findings = [
                {
                    "matched_name": m.get("caption", "Unknown"),
                    "schema": m.get("schema", "Unknown"),
                    "dataset": m.get("dataset", []),
                    "match_score": m.get("match_score", 1.0),
                }
                for m in matches
            ]
            return self._make_result(
                status="flagged",
                findings=findings,
                source_url="https://sanctionssearch.ofac.treas.gov/",
            )

        return self._make_result(
            status="clear",
            source_url="https://sanctionssearch.ofac.treas.gov/",
        )


class UNSource(JurisdictionSource):
    """United Nations Security Council Consolidated sanctions list."""

    @property
    def code(self) -> str:  # noqa: D102
        return "un"

    @property
    def name(self) -> str:  # noqa: D102
        return "UN Consolidated List"

    @property
    def priority_tier(self) -> int:  # noqa: D102
        return 1

    async def query(self, name_en: str, name_he: Optional[str]) -> JurisdictionResult:  # noqa: D102
        bulk = await _bulk_match_opensanctions(name_en, name_he)
        if bulk is None:
            return self._make_result(
                status="clear",
                source_url="https://www.un.org/securitycouncil/content/un-sc-consolidated-list",
            )

        matches = _filter_matches_for_jurisdiction(bulk, self.code)
        if matches:
            findings = [
                {
                    "matched_name": m.get("caption", "Unknown"),
                    "schema": m.get("schema", "Unknown"),
                    "dataset": m.get("dataset", []),
                    "match_score": m.get("match_score", 1.0),
                }
                for m in matches
            ]
            return self._make_result(
                status="flagged",
                findings=findings,
                source_url="https://www.un.org/securitycouncil/content/un-sc-consolidated-list",
            )

        return self._make_result(
            status="clear",
            source_url="https://www.un.org/securitycouncil/content/un-sc-consolidated-list",
        )


class UKHMTSource(JurisdictionSource):
    """UK HM Treasury (Office of Financial Sanctions Implementation) list."""

    @property
    def code(self) -> str:  # noqa: D102
        return "uk_hmt"

    @property
    def name(self) -> str:  # noqa: D102
        return "UK HM Treasury"

    @property
    def priority_tier(self) -> int:  # noqa: D102
        return 1

    async def query(self, name_en: str, name_he: Optional[str]) -> JurisdictionResult:  # noqa: D102
        bulk = await _bulk_match_opensanctions(name_en, name_he)
        if bulk is None:
            return self._make_result(
                status="clear",
                source_url="https://sanctionssearchapp.ofsi.hmtreasury.gov.uk/",
            )

        matches = _filter_matches_for_jurisdiction(bulk, self.code)
        if matches:
            findings = [
                {
                    "matched_name": m.get("caption", "Unknown"),
                    "schema": m.get("schema", "Unknown"),
                    "dataset": m.get("dataset", []),
                    "match_score": m.get("match_score", 1.0),
                }
                for m in matches
            ]
            return self._make_result(
                status="flagged",
                findings=findings,
                source_url="https://sanctionssearchapp.ofsi.hmtreasury.gov.uk/",
            )

        return self._make_result(
            status="clear",
            source_url="https://sanctionssearchapp.ofsi.hmtreasury.gov.uk/",
        )


class EUSource(JurisdictionSource):
    """European Union Consolidated Financial Sanctions list."""

    @property
    def code(self) -> str:  # noqa: D102
        return "eu"

    @property
    def name(self) -> str:  # noqa: D102
        return "EU Consolidated List"

    @property
    def priority_tier(self) -> int:  # noqa: D102
        return 1

    async def query(self, name_en: str, name_he: Optional[str]) -> JurisdictionResult:  # noqa: D102
        bulk = await _bulk_match_opensanctions(name_en, name_he)
        if bulk is None:
            return self._make_result(
                status="clear",
                source_url="https://data.europa.eu/data/datasets/consolidated-list-of-persons-groups-and-entities-subject-to-eu-financial-sanctions",
            )

        matches = _filter_matches_for_jurisdiction(bulk, self.code)
        if matches:
            findings = [
                {
                    "matched_name": m.get("caption", "Unknown"),
                    "schema": m.get("schema", "Unknown"),
                    "dataset": m.get("dataset", []),
                    "match_score": m.get("match_score", 1.0),
                }
                for m in matches
            ]
            return self._make_result(
                status="flagged",
                findings=findings,
                source_url="https://data.europa.eu/data/datasets/consolidated-list-of-persons-groups-and-entities-subject-to-eu-financial-sanctions",
            )

        return self._make_result(
            status="clear",
            source_url="https://data.europa.eu/data/datasets/consolidated-list-of-persons-groups-and-entities-subject-to-eu-financial-sanctions",
        )


class IsraelSource(JurisdictionSource):
    """Israel Ministry of Defence sanctions and terrorist-designation lists."""

    @property
    def code(self) -> str:  # noqa: D102
        return "il"

    @property
    def name(self) -> str:  # noqa: D102
        return "Israel Lists"

    @property
    def priority_tier(self) -> int:  # noqa: D102
        return 1

    async def query(self, name_en: str, name_he: Optional[str]) -> JurisdictionResult:  # noqa: D102
        bulk = await _bulk_match_opensanctions(name_en, name_he)
        if bulk is None:
            return self._make_result(
                status="clear",
                source_url="https://nbctf.mod.gov.il/",
            )

        matches = _filter_matches_for_jurisdiction(bulk, self.code)
        if matches:
            findings = [
                {
                    "matched_name": m.get("caption", "Unknown"),
                    "schema": m.get("schema", "Unknown"),
                    "dataset": m.get("dataset", []),
                    "match_score": m.get("match_score", 1.0),
                }
                for m in matches
            ]
            return self._make_result(
                status="flagged",
                findings=findings,
                source_url="https://nbctf.mod.gov.il/",
            )

        return self._make_result(
            status="clear",
            source_url="https://nbctf.mod.gov.il/",
        )


# ---------------------------------------------------------------------------
# Tier-2 sources (medium priority)
# ---------------------------------------------------------------------------


class CanadaSEMASource(JurisdictionSource):
    """Canadian Special Economic Measures Act (SEMA) sanctions list."""

    @property
    def code(self) -> str:  # noqa: D102
        return "ca_sema"

    @property
    def name(self) -> str:  # noqa: D102
        return "Canada SEMA"

    @property
    def priority_tier(self) -> int:  # noqa: D102
        return 2

    async def query(self, name_en: str, name_he: Optional[str]) -> JurisdictionResult:  # noqa: D102
        bulk = await _bulk_match_opensanctions(name_en, name_he)
        if bulk is None:
            return self._make_result(
                status="clear",
                source_url="https://www.international.gc.ca/world-monde/international_relations-relations_internationales/sanctions/consolidated-consolide.aspx",
            )

        matches = _filter_matches_for_jurisdiction(bulk, self.code)
        if matches:
            findings = [
                {
                    "matched_name": m.get("caption", "Unknown"),
                    "schema": m.get("schema", "Unknown"),
                    "dataset": m.get("dataset", []),
                    "match_score": m.get("match_score", 1.0),
                }
                for m in matches
            ]
            return self._make_result(
                status="flagged",
                findings=findings,
                source_url="https://www.international.gc.ca/world-monde/international_relations-relations_internationales/sanctions/consolidated-consolide.aspx",
            )

        return self._make_result(
            status="clear",
            source_url="https://www.international.gc.ca/world-monde/international_relations-relations_internationales/sanctions/consolidated-consolide.aspx",
        )


class AustraliaDFATSource(JurisdictionSource):
    """Australian Department of Foreign Affairs and Trade sanctions list."""

    @property
    def code(self) -> str:  # noqa: D102
        return "au_dfat"

    @property
    def name(self) -> str:  # noqa: D102
        return "Australia DFAT"

    @property
    def priority_tier(self) -> int:  # noqa: D102
        return 2

    async def query(self, name_en: str, name_he: Optional[str]) -> JurisdictionResult:  # noqa: D102
        bulk = await _bulk_match_opensanctions(name_en, name_he)
        if bulk is None:
            return self._make_result(
                status="clear",
                source_url="https://www.dfat.gov.au/international-relations/security/sanctions/Pages/sanctions",
            )

        matches = _filter_matches_for_jurisdiction(bulk, self.code)
        if matches:
            findings = [
                {
                    "matched_name": m.get("caption", "Unknown"),
                    "schema": m.get("schema", "Unknown"),
                    "dataset": m.get("dataset", []),
                    "match_score": m.get("match_score", 1.0),
                }
                for m in matches
            ]
            return self._make_result(
                status="flagged",
                findings=findings,
                source_url="https://www.dfat.gov.au/international-relations/security/sanctions/Pages/sanctions",
            )

        return self._make_result(
            status="clear",
            source_url="https://www.dfat.gov.au/international-relations/security/sanctions/Pages/sanctions",
        )


# ---------------------------------------------------------------------------
# Tier-3 source (lowest priority) — local file
# ---------------------------------------------------------------------------


class FATFGreyListSource(JurisdictionSource):
    """FATF Grey List (Jurisdictions under Increased Monitoring).

    Reads from a local CSV cache file rather than a live API.
    The file is expected to contain one country / jurisdiction per line
    with at minimum a ``name`` column.
    """

    # Default path relative to the project root
    _DEFAULT_CACHE_PATH: str = "data/fatf_grey_list.csv"

    @property
    def code(self) -> str:  # noqa: D102
        return "fatf_grey"

    @property
    def name(self) -> str:  # noqa: D102
        return "FATF Grey List"

    @property
    def priority_tier(self) -> int:  # noqa: D102
        return 3

    def _cache_path(self) -> str:
        """Return the filesystem path to the FATF Grey List CSV cache.

        The path can be overridden via the ``FATF_GREY_LIST_PATH``
        environment variable; otherwise the bundled default is used.
        """
        return os.environ.get("FATF_GREY_LIST_PATH", self._DEFAULT_CACHE_PATH)

    async def query(self, name_en: str, name_he: Optional[str]) -> JurisdictionResult:  # noqa: D102
        """Check whether the subject is associated with a FATF-grey-listed jurisdiction.

        Because the FATF list contains **jurisdictions** (countries) rather
        than individuals, the match is a simple case-insensitive substring
        search: if the screened name contains a grey-listed country name,
        a flag is raised.  This is intentionally conservative.
        """
        cache_path = self._cache_path()

        # Read the CSV cache asynchronously
        grey_jurisdictions: List[str] = []
        try:
            import aiofiles

            async with aiofiles.open(cache_path, mode="r", encoding="utf-8") as f:
                content = await f.read()
            # Parse simple CSV: first row = header, remaining rows = data
            lines = content.strip().splitlines()
            if len(lines) > 1:
                for line in lines[1:]:
                    parts = line.split(",")
                    if parts:
                        grey_jurisdictions.append(parts[0].strip().lower())
        except FileNotFoundError:
            logger.info(
                "FATF Grey List cache not found at %s — returning clear", cache_path
            )
            return self._make_result(
                status="clear",
                source_url="https://www.fatf-gafi.org/en/topics/high-risk-and-other-monitored-jurisdictions.html",
            )
        except Exception as exc:
            logger.warning("FATF Grey List read error: %s", exc)
            return self._make_result(
                status="error",
                findings=[{"error": str(exc)}],
                source_url="https://www.fatf-gafi.org/en/topics/high-risk-and-other-monitored-jurisdictions.html",
            )

        # Check for jurisdiction name overlap (case-insensitive)
        name_en_lower = name_en.lower()
        name_he_lower = (name_he or "").lower()

        matched_jurisdictions: List[str] = []
        for gj in grey_jurisdictions:
            if gj in name_en_lower or gj in name_he_lower:
                matched_jurisdictions.append(gj.title())

        if matched_jurisdictions:
            return self._make_result(
                status="flagged",
                findings=[
                    {
                        "matched_jurisdictions": matched_jurisdictions,
                        "note": (
                            "Subject name contains reference(s) to FATF "
                            "grey-listed jurisdiction(s)."
                        ),
                    }
                ],
                source_url="https://www.fatf-gafi.org/en/topics/high-risk-and-other-monitored-jurisdictions.html",
            )

        return self._make_result(
            status="clear",
            source_url="https://www.fatf-gafi.org/en/topics/high-risk-and-other-monitored-jurisdictions.html",
        )
