"""OFAC SDN XML source adapter.

Downloads the OFAC Specially Designated Nationals (SDN) list from the
US Treasury and screens names against locally cached entries.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import SanctionsEntry, StaticSanctionsResult, StaticSanctionsSource

logger = __import__("logging").getLogger(__name__)


class OFACXMLSource(StaticSanctionsSource):
    """OFAC SDN list downloaded as XML from the US Treasury website."""

    @property
    def code(self) -> str:  # noqa: D102
        return "ofac_xml"

    @property
    def name(self) -> str:  # noqa: D102
        return "OFAC SDN (XML)"

    @property
    def xml_url(self) -> str:  # noqa: D102
        return "https://www.treasury.gov/ofac/downloads/sdn.xml"

    @property
    def cache_file_name(self) -> str:  # noqa: D102
        return "ofac_sdn.xml"

    # -- Download & parse --------------------------------------------------

    async def download_and_parse(self, cache_dir: str) -> bool:
        """Download the OFAC SDN XML file and parse entries into memory."""
        try:
            from .downloader import StaticSanctionsDownloader

            dl = StaticSanctionsDownloader(cache_dir)
            filepath = await dl.download(self.xml_url, self.cache_file_name)
            if filepath and filepath.exists():
                self._entries = self._parse_xml(str(filepath))
                self._loaded = True
                self.record_success()
                logger.info(
                    "OFAC SDN loaded: %d entries from %s", len(self._entries), filepath
                )
                return True
        except Exception as exc:
            logger.warning("OFAC download/parse failed: %s", exc)

        self.record_failure()
        return False

    def _parse_xml(self, filepath: str) -> List[SanctionsEntry]:
        """Parse OFAC SDN XML into a list of ``SanctionsEntry``."""
        entries: List[SanctionsEntry] = []
        try:
            tree = ET.parse(filepath)
            root = tree.getroot()
            for sdn_entry in root.findall(".//sdnEntry"):
                entry = self._parse_sdn_entry(sdn_entry)
                if entry:
                    entries.append(entry)
        except Exception as exc:
            logger.warning("Failed to parse OFAC XML: %s", exc)
        return entries

    def _parse_sdn_entry(self, sdn_entry) -> Optional[SanctionsEntry]:
        """Extract a single ``SanctionsEntry`` from an ``<sdnEntry>`` element."""
        try:
            first_name = self._get_text(sdn_entry, "firstName")
            last_name = self._get_text(sdn_entry, "lastName")
            sdn_type = self._get_text(sdn_entry, "sdnType")
            full_name = f"{first_name} {last_name}".strip()

            if not full_name:
                return None

            # Programs
            programs = [
                p.text
                for p in sdn_entry.findall(".//programList/program")
                if p.text
            ]

            # Addresses
            addresses: List[str] = []
            for addr in sdn_entry.findall(".//addressList/address"):
                addr_parts = []
                for tag in ("address1", "address2", "city", "stateOrProvince", "country"):
                    val = addr.find(tag)
                    if val is not None and val.text:
                        addr_parts.append(val.text)
                if addr_parts:
                    addresses.append(", ".join(addr_parts))

            # Aliases (AKA)
            aliases: List[str] = []
            for aka in sdn_entry.findall(".//akaList/aka"):
                aka_first = self._get_text(aka, "firstName")
                aka_last = self._get_text(aka, "lastName")
                aka_name = f"{aka_first} {aka_last}".strip()
                if aka_name:
                    aliases.append(aka_name)

            # Dates of birth
            dates = [
                d.text
                for d in sdn_entry.findall(
                    ".//dateOfBirthList/dateOfBirthItem/dateOfBirth"
                )
                if d.text
            ]

            # Identifiers (passport, national ID, etc.)
            identifiers = [
                i.text
                for i in sdn_entry.findall(".//idList/id/IDNumber")
                if i.text
            ]

            # Nationality
            nationality_elem = sdn_entry.find(".//nationalityList/nationality")
            nationality = (
                nationality_elem.text
                if nationality_elem is not None and nationality_elem.text
                else None
            )

            return SanctionsEntry(
                name=full_name,
                type=sdn_type.lower() if sdn_type else "unknown",
                program=programs[0] if programs else None,
                dates=dates,
                identifiers=identifiers,
                aliases=aliases,
                addresses=addresses,
                nationality=nationality,
                source_list="OFAC SDN",
            )
        except Exception as exc:
            logger.debug("Skipping malformed OFAC entry: %s", exc)
            return None

    @staticmethod
    def _get_text(parent, tag: str) -> str:
        """Safely extract text from a child element."""
        elem = parent.find(tag)
        return elem.text if elem is not None and elem.text else ""

    # -- Query -------------------------------------------------------------

    async def query(
        self, name_en: str, name_he: Optional[str], cache_dir: str
    ) -> StaticSanctionsResult:
        """Screen *name_en* against the cached OFAC SDN list."""
        if not self.is_available():
            return self._make_result(
                "error",
                source_url="https://sanctionssearch.ofac.treas.gov/",
            )

        if not self._loaded:
            ok = await self.download_and_parse(cache_dir)
            if not ok:
                return self._make_result(
                    "not_downloaded",
                    source_url="https://sanctionssearch.ofac.treas.gov/",
                )

        if not self._entries:
            return self._make_result(
                "not_downloaded",
                source_url="https://sanctionssearch.ofac.treas.gov/",
            )

        # Calculate cache age
        from .downloader import StaticSanctionsDownloader

        dl = StaticSanctionsDownloader(cache_dir)
        cache_age = dl.get_cache_age_hours(self.cache_file_name)

        name_lower = name_en.lower().strip()
        matches: List[Dict[str, Any]] = []

        for entry in self._entries:
            entry_name_lower = entry.name.lower()

            # Direct name match
            if name_lower in entry_name_lower or entry_name_lower in name_lower:
                matches.append(
                    {
                        "entry": entry.model_dump(),
                        "match_score": 1.0,
                        "matched_on": "name",
                    }
                )
                continue

            # Check aliases
            for alias in entry.aliases:
                alias_lower = alias.lower()
                if name_lower in alias_lower or alias_lower in name_lower:
                    matches.append(
                        {
                            "entry": entry.model_dump(),
                            "match_score": 0.9,
                            "matched_on": "alias",
                        }
                    )
                    break

        if matches:
            return self._make_result(
                "flagged",
                matches=matches,
                source_url="https://sanctionssearch.ofac.treas.gov/",
                total_entries=len(self._entries),
                cache_age_hours=cache_age,
            )

        return self._make_result(
            "clear",
            source_url="https://sanctionssearch.ofac.treas.gov/",
            total_entries=len(self._entries),
            cache_age_hours=cache_age,
        )
