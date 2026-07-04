"""CourtListener API adapter for US federal court records.

CourtListener provides free access to US court opinions and dockets.
The free tier works without an API token but has lower rate limits;
provide a ``COURTLISTENER_TOKEN`` environment variable for higher limits.
"""

from __future__ import annotations

import os
import urllib.parse
from typing import Optional

import httpx

from .base import CaseResult, LitigationResult, LitigationSource


class CourtListenerSource(LitigationSource):
    """Search US court opinions via the CourtListener REST API."""

    @property
    def code(self) -> str:
        return "courtlistener"

    @property
    def name(self) -> str:
        return "CourtListener (US Courts)"

    async def query(self, name_en: str, name_he: Optional[str]) -> LitigationResult:
        # Read token from environment or config.json
        token = os.environ.get("COURTLISTENER_TOKEN", "")
        if not token:
            try:
                from core.config_file import config_file
                token = config_file.get_str("courtlistener_token", "")
            except Exception:
                pass
        headers: dict[str, str] = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Token {token}"

        cases: list[CaseResult] = []

        try:
            # Search for opinions mentioning the name
            search_url = (
                "https://www.courtlistener.com/api/rest/v3/search/"
                f"?q={urllib.parse.quote(name_en)}"
                "&type=o"
                "&court__in=all"
                "&order_by=score+desc"
            )

            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.get(search_url, headers=headers)

                if resp.status_code == 200:
                    data = resp.json()
                    for result in data.get("results", [])[:10]:
                        case_name = result.get("caseName", "")
                        # Only include if the name appears in the case
                        if name_en.lower() in case_name.lower():
                            cases.append(
                                CaseResult(
                                    case_name=case_name,
                                    case_number=result.get("docketNumber", "")
                                    or None,
                                    court=result.get("court", "") or None,
                                    date_decided=result.get("dateFiled", "") or None,
                                    status="decided",
                                    url=(
                                        f"https://www.courtlistener.com"
                                        f"{result.get('absolute_url', '')}"
                                    ),
                                    jurisdiction="US",
                                    case_type=result.get("type", "") or None,
                                    snippet=(result.get("snippet", "") or "")[:300],
                                )
                            )

                    if cases:
                        self.record_success()
                        return self._make_result(
                            "flagged",
                            cases=cases,
                            source_url="https://www.courtlistener.com/",
                            cases_found=len(cases),
                        )

                    self.record_success()
                    return self._make_result(
                        "clear",
                        source_url="https://www.courtlistener.com/",
                    )

                elif resp.status_code in (401, 403):
                    # Token issue or rate limited
                    self.record_failure()
                    return self._make_result(
                        "error",
                        source_url="https://www.courtlistener.com/",
                    )
                else:
                    self.record_failure()
                    return self._make_result(
                        "error",
                        source_url="https://www.courtlistener.com/",
                    )

        except httpx.TimeoutException:
            self.record_failure()
            return self._make_result(
                "timeout",
                source_url="https://www.courtlistener.com/",
            )
        except Exception:
            self.record_failure()
            return self._make_result(
                "error",
                source_url="https://www.courtlistener.com/",
            )
