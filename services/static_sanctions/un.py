"""UN Consolidated Sanctions List source adapter.

Downloads the UN Security Council consolidated sanctions list and
screens names against locally cached entries.

The UN publishes their consolidated list at:
https://scsanctions.un.org/resources/xml/en/consolidated.xml
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

from .base import SanctionsEntry, StaticSanctionsResult, StaticSanctionsSource

logger = __import__("logging").getLogger(__name__)

# UN Consolidated Sanctions List XML URL
_UN_SANCTIONS_URL: str = (
    "https://scsanctions.un.org/resources/xml/en/consolidated.xml"
)


class UNConsolidatedXMLSource(StaticSanctionsSource):
    """UN Security Council Consolidated Sanctions List (XML)."""

    @property
    def code(self) -> str:  # noqa: D102
        return "un_xml"

    @property
    def name(self) -> str:  # noqa: D102
        return "UN Consolidated List (XML)"

    @property
    def xml_url(self) -> str:  # noqa: D102
        return _UN_SANCTIONS_URL

    @property
    def cache_file_name(self) -> str:  # noqa: D102
        return "un_consolidated.xml"

    # -- Download & parse --------------------------------------------------

    async def download_and_parse(self, cache_dir: str) -> bool:
        """Download the UN consolidated XML file and parse entries into memory."""
        try:
            from .downloader import StaticSanctionsDownloader

            dl = StaticSanctionsDownloader(cache_dir)
            filepath = await dl.download(self.xml_url, self.cache_file_name)
            if filepath and filepath.exists():
                self._entries = self._parse_xml(str(filepath))
                self._loaded = True
                self.record_success()
                logger.info(
                    "UN consolidated list loaded: %d entries from %s",
                    len(self._entries),
                    filepath,
                )
                return True
        except Exception as exc:
            logger.warning("UN consolidated list download/parse failed: %s", exc)

        self.record_failure()
        return False

    def _parse_xml(self, filepath: str) -> List[SanctionsEntry]:
        """Parse UN consolidated sanctions XML into a list of ``SanctionsEntry``."""
        entries: List[SanctionsEntry] = []
        try:
            tree = ET.parse(filepath)
            root = tree.getroot()

            # The UN XML has <CONSOLIDATED_LIST> as root with child
            # <INDIVIDUALS>, <ENTITIES>, <VESSELS>, <AIRCRAFT>
            for section in ("INDIVIDUALS", "ENTITIES", "VESSELS", "AIRCRAFT"):
                section_elem = root.find(f".//{section}")
                if section_elem is None:
                    continue

                entry_type = section.lower()[:-1]  # "individual", "entity", "vessel", "aircraft"
                for item in section_elem:
                    entry = self._parse_un_entry(item, entry_type)
                    if entry:
                        entries.append(entry)

        except Exception as exc:
            logger.warning("Failed to parse UN consolidated XML: %s", exc)
        return entries

    def _parse_un_entry(self, elem, entry_type: str) -> Optional[SanctionsEntry]:
        """Extract a single ``SanctionsEntry`` from a UN list element."""
        try:
            # Name handling differs by section type
            if entry_type == "individual":
                first_name = self._get_text(elem, "FIRST_NAME")
                second_name = self._get_text(elem, "SECOND_NAME")
                third_name = self._get_text(elem, "THIRD_NAME")
                fourth_name = self._get_text(elem, "FOURTH_NAME")
                name_parts = [p for p in (first_name, second_name, third_name, fourth_name) if p]
                full_name = " ".join(name_parts).strip()
            elif entry_type == "entity":
                full_name = self._get_text(elem, "FIRST_NAME")
            elif entry_type in ("vessel", "aircraft"):
                full_name = self._get_text(elem, "NAME")
            else:
                full_name = self._get_text(elem, "FIRST_NAME")

            if not full_name:
                return None

            # Aliases (INDIVIDUAL_ALIAS or ENTITY_ALIAS)
            aliases: List[str] = []
            alias_tag = (
                "INDIVIDUAL_ALIAS"
                if entry_type == "individual"
                else f"{entry_type.upper()}_ALIAS"
            )
            for alias in elem.findall(f".//{alias_tag}"):
                alias_name = alias.get("ALIAS_NAME") or self._get_text(alias, "ALIAS_NAME")
                if alias_name:
                    aliases.append(alias_name)

            # Addresses
            addresses: List[str] = []
            addr_tag = (
                "INDIVIDUAL_ADDRESS"
                if entry_type == "individual"
                else f"{entry_type.upper()}_ADDRESS"
            )
            for addr in elem.findall(f".//{addr_tag}"):
                addr_parts = []
                for field in ("STREET", "CITY", "COUNTRY"):
                    val = addr.get(field) or self._get_text(addr, field)
                    if val:
                        addr_parts.append(val)
                if addr_parts:
                    addresses.append(", ".join(addr_parts))

            # Dates of birth
            dates: List[str] = []
            dob_tag = (
                "INDIVIDUAL_DATE_OF_BIRTH"
                if entry_type == "individual"
                else None
            )
            if dob_tag:
                for dob in elem.findall(f".//{dob_tag}"):
                    dob_val = dob.get("DATE") or self._get_text(dob, "DATE") or dob.get("YEAR")
                    if dob_val:
                        dates.append(str(dob_val))

            # Nationality
            nationality: Optional[str] = None
            nat_tag = (
                "INDIVIDUAL_NATIONALITY"
                if entry_type == "individual"
                else None
            )
            if nat_tag:
                for nat in elem.findall(f".//{nat_tag}"):
                    nat_val = nat.get("COUNTRY") or self._get_text(nat, "COUNTRY")
                    if nat_val:
                        nationality = nat_val
                        break

            # Identifiers (passport, national ID, etc.)
            identifiers: List[str] = []
            doc_tag = (
                "INDIVIDUAL_DOCUMENT"
                if entry_type == "individual"
                else None
            )
            if doc_tag:
                for doc in elem.findall(f".//{doc_tag}"):
                    doc_num = doc.get("NUMBER") or self._get_text(doc, "NUMBER")
                    if doc_num:
                        identifiers.append(doc_num)

            # Programme / committee
            program: Optional[str] = None
            if entry_type == "individual":
                program = self._get_text(elem, "DESIGNATION")
            elif entry_type == "entity":
                program = self._get_text(elem, "UN_LIST_TYPE")

            return SanctionsEntry(
                name=full_name,
                type=entry_type,
                program=program if program else None,
                dates=dates,
                identifiers=identifiers,
                aliases=aliases,
                addresses=addresses,
                nationality=nationality,
                source_list="UN Consolidated List",
            )
        except Exception as exc:
            logger.debug("Skipping malformed UN entry: %s", exc)
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
        """Screen *name_en* against the cached UN consolidated list."""
        if not self.is_available():
            return self._make_result(
                "error",
                source_url="https://scsanctions.un.org/",
            )

        if not self._loaded:
            ok = await self.download_and_parse(cache_dir)
            if not ok:
                return self._make_result(
                    "not_downloaded",
                    source_url="https://scsanctions.un.org/",
                )

        if not self._entries:
            return self._make_result(
                "not_downloaded",
                source_url="https://scsanctions.un.org/",
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
                source_url="https://scsanctions.un.org/",
                total_entries=len(self._entries),
                cache_age_hours=cache_age,
            )

        return self._make_result(
            "clear",
            source_url="https://scsanctions.un.org/",
            total_entries=len(self._entries),
            cache_age_hours=cache_age,
        )
