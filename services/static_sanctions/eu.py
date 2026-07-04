"""EU Consolidated Sanctions List source adapter.

Downloads the EU Financial Sanctions File (FSF) XML list and screens
names against locally cached entries.

The EU sanctions XML is published by the European Commission and uses
a different schema than OFAC.  This adapter handles both the download
and the EU-specific XML parsing.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

from .base import SanctionsEntry, StaticSanctionsResult, StaticSanctionsSource

logger = __import__("logging").getLogger(__name__)

# Primary EU sanctions XML URL.
# The EU Commission publishes the consolidated sanctions list here.
_EU_SANCTIONS_URL: str = (
    "https://webgate.ec.europa.eu/fsd/fsf/public/files/xmlFullSanctionsList_1_1/content?token=nZZM-UPudE7Sg1qP8mH-0w"
)


class EUSanctionsXMLSource(StaticSanctionsSource):
    """EU Consolidated Financial Sanctions List (XML)."""

    @property
    def code(self) -> str:  # noqa: D102
        return "eu_xml"

    @property
    def name(self) -> str:  # noqa: D102
        return "EU Financial Sanctions (XML)"

    @property
    def xml_url(self) -> str:  # noqa: D102
        return _EU_SANCTIONS_URL

    @property
    def cache_file_name(self) -> str:  # noqa: D102
        return "eu_sanctions.xml"

    # -- Download & parse --------------------------------------------------

    async def download_and_parse(self, cache_dir: str) -> bool:
        """Download the EU sanctions XML file and parse entries into memory."""
        try:
            from .downloader import StaticSanctionsDownloader

            dl = StaticSanctionsDownloader(cache_dir)
            filepath = await dl.download(self.xml_url, self.cache_file_name)
            if filepath and filepath.exists():
                self._entries = self._parse_xml(str(filepath))
                self._loaded = True
                self.record_success()
                logger.info(
                    "EU sanctions loaded: %d entries from %s",
                    len(self._entries),
                    filepath,
                )
                return True
        except Exception as exc:
            logger.warning("EU sanctions download/parse failed: %s", exc)

        self.record_failure()
        return False

    def _parse_xml(self, filepath: str) -> List[SanctionsEntry]:
        """Parse EU sanctions XML into a list of ``SanctionsEntry``."""
        entries: List[SanctionsEntry] = []
        try:
            # EU XML may use namespaces; strip them for easier parsing
            tree = ET.parse(filepath)
            root = tree.getroot()

            # Register namespaces to handle both namespaced and non-namespaced queries
            ns = self._detect_namespace(root)

            # EU FSF format: <export> → <sanctionEntity> elements
            for entity in root.findall(".//sanctionEntity", ns):
                entry = self._parse_entity(entity, ns)
                if entry:
                    entries.append(entry)

            # Fallback: try without namespace if nothing found
            if not entries:
                for entity in root.findall(".//sanctionEntity"):
                    entry = self._parse_entity(entity, {})
                    if entry:
                        entries.append(entry)

        except Exception as exc:
            logger.warning("Failed to parse EU sanctions XML: %s", exc)
        return entries

    @staticmethod
    def _detect_namespace(root) -> Dict[str, str]:
        """Detect XML namespace from root tag for proper ElementTree queries."""
        ns: Dict[str, str] = {}
        tag = root.tag
        if tag.startswith("{"):
            uri = tag[1:].split("}")[0]
            ns["ns"] = uri
        return ns

    def _parse_entity(
        self, entity, ns: Dict[str, str]
    ) -> Optional[SanctionsEntry]:
        """Extract a single ``SanctionsEntry`` from a ``<sanctionEntity>`` element."""
        try:
            # Name: EU uses <nameAlias> elements with wholeName or firstName+lastName
            name_aliases = entity.findall(".//nameAlias", ns) or entity.findall(
                ".//nameAlias"
            )
            full_name = ""
            aliases: List[str] = []

            for name_alias in name_aliases:
                whole_name = name_alias.get("wholeName") or name_alias.get(
                    "aliasName"
                )
                if whole_name:
                    if not full_name:
                        full_name = whole_name.strip()
                    elif whole_name.strip() != full_name:
                        aliases.append(whole_name.strip())
                    continue

                first = name_alias.get("firstName") or ""
                last = name_alias.get("lastName") or ""
                combined = f"{first} {last}".strip()
                if combined:
                    if not full_name:
                        full_name = combined
                    elif combined != full_name:
                        aliases.append(combined)

            if not full_name:
                # Fallback: try <name> element directly
                name_elem = entity.find("ns:name", ns) if ns else entity.find("name")
                if name_elem is not None and name_elem.text:
                    full_name = name_elem.text.strip()

            if not full_name:
                return None

            # Entity type
            sdn_type = "entity"
            subject_type = entity.get("subjectType")
            if subject_type:
                sdn_type = subject_type.lower()
            elif "person" in (entity.get("acronym") or "").lower():
                sdn_type = "individual"

            # Addresses
            addresses: List[str] = []
            for addr in entity.findall(".//address", ns) or entity.findall(
                ".//address"
            ):
                addr_parts = []
                for attr in ("address", "city", "countryDescription", "country"):
                    val = addr.get(attr)
                    if val:
                        addr_parts.append(val)
                addr_text = ", ".join(addr_parts)
                if addr_text:
                    addresses.append(addr_text)

            # Dates (birth dates)
            dates: List[str] = []
            for bd in entity.findall(".//birthDate", ns) or entity.findall(
                ".//birthDate"
            ):
                birth = bd.get("birthDate") or bd.text
                if birth:
                    dates.append(birth)

            # Identifiers (passport, national ID)
            identifiers: List[str] = []
            for ident in entity.findall(".//identification", ns) or entity.findall(
                ".//identification"
            ):
                ident_number = ident.get("number") or ident.get("id")
                if ident_number:
                    identifiers.append(ident_number)

            # Nationality
            nationality: Optional[str] = None
            nat_elem = entity.find(".//nationality", ns) or entity.find(
                ".//nationality"
            )
            if nat_elem is not None:
                nationality = nat_elem.get("countryDescription") or nat_elem.text

            # Regulation / program
            program: Optional[str] = None
            reg_elem = entity.find(".//regulation", ns) or entity.find(".//regulation")
            if reg_elem is not None:
                program = reg_elem.get("program") or reg_elem.get("number")

            return SanctionsEntry(
                name=full_name,
                type=sdn_type,
                program=program,
                dates=dates,
                identifiers=identifiers,
                aliases=list(set(aliases)),
                addresses=addresses,
                nationality=nationality,
                source_list="EU Financial Sanctions",
            )
        except Exception as exc:
            logger.debug("Skipping malformed EU entity: %s", exc)
            return None

    # -- Query -------------------------------------------------------------

    async def query(
        self, name_en: str, name_he: Optional[str], cache_dir: str
    ) -> StaticSanctionsResult:
        """Screen *name_en* against the cached EU sanctions list."""
        if not self.is_available():
            return self._make_result(
                "error",
                source_url="https://www.sanctionsmap.eu/",
            )

        if not self._loaded:
            ok = await self.download_and_parse(cache_dir)
            if not ok:
                return self._make_result(
                    "not_downloaded",
                    source_url="https://www.sanctionsmap.eu/",
                )

        if not self._entries:
            return self._make_result(
                "not_downloaded",
                source_url="https://www.sanctionsmap.eu/",
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
                source_url="https://www.sanctionsmap.eu/",
                total_entries=len(self._entries),
                cache_age_hours=cache_age,
            )

        return self._make_result(
            "clear",
            source_url="https://www.sanctionsmap.eu/",
            total_entries=len(self._entries),
            cache_age_hours=cache_age,
        )
