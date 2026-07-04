"""EU Court of Justice adapter via the E-Justice Europa search portal.

Searches for cases before the Court of Justice of the European Union
(CJEU) and the General Court using the E-Justice ECLI search interface.
"""

from __future__ import annotations

import re
import urllib.parse
from typing import Optional

import httpx

from .base import CaseResult, LitigationResult, LitigationSource


class ECLISource(LitigationSource):
    """Search EU court cases via the E-Justice Europa ECLI portal."""

    @property
    def code(self) -> str:
        return "ecli_eu"

    @property
    def name(self) -> str:
        return "EU Court of Justice (ECLI)"

    async def query(self, name_en: str, name_he: Optional[str]) -> LitigationResult:
        cases: list[CaseResult] = []

        try:
            search_url = (
                "https://e-justice.europa.eu/ecli-search/search.do"
                f"?ecliSearch_searchCriteria_freeText={urllib.parse.quote(name_en)}"
                "&lang=en"
                "&numPerPage=10"
            )

            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    search_url,
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/120.0.0.0 Safari/537.36"
                        ),
                        "Accept": "text/html,application/xhtml+xml",
                    },
                )

                if resp.status_code == 200:
                    html = resp.text

                    # Look for case name patterns in the HTML.
                    # ECLI cases typically have patterns like "Case C-XXX/XX".
                    case_patterns = re.findall(
                        r"Case\s+([C-T]-\d+/\d+)[^<]*<[^>]*>\s*([^<]+)",
                        html,
                        re.IGNORECASE,
                    )

                    for case_num, case_title in case_patterns[:5]:
                        if name_en.lower() in case_title.lower():
                            cases.append(
                                CaseResult(
                                    case_name=f"Case {case_num}: {case_title.strip()}",
                                    case_number=case_num,
                                    court="Court of Justice of the European Union",
                                    jurisdiction="EU",
                                    case_type="EU Law",
                                    url="https://e-justice.europa.eu/ecli-search/search.do",
                                    snippet=case_title.strip()[:200],
                                )
                            )

                    if cases:
                        self.record_success()
                        return self._make_result(
                            "flagged",
                            cases=cases,
                            source_url="https://e-justice.europa.eu/",
                            cases_found=len(cases),
                        )

                    self.record_success()
                    return self._make_result(
                        "clear",
                        source_url="https://e-justice.europa.eu/",
                    )
                else:
                    self.record_failure()
                    return self._make_result(
                        "error",
                        source_url="https://e-justice.europa.eu/",
                    )

        except httpx.TimeoutException:
            self.record_failure()
            return self._make_result(
                "timeout",
                source_url="https://e-justice.europa.eu/",
            )
        except Exception:
            self.record_failure()
            return self._make_result(
                "error",
                source_url="https://e-justice.europa.eu/",
            )
