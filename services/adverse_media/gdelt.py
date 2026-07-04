"""GDELT Project Doc API adapter.

Queries the GDELT Global Database ``doc`` endpoint for articles that
mention the screened subject.  The GDELT API is free, requires no
authentication, and returns up to 10 recent articles in JSON format.

The adapter filters returned articles so only those whose titles contain
the subject's name (English or Hebrew) are included in the result.
"""

from __future__ import annotations

import urllib.parse
from typing import Any, Dict, List, Optional

import httpx

from .base import MediaResult, MediaSource

logger = __import__("logging").getLogger(__name__)

# ---------------------------------------------------------------------------
# GDELT API configuration
# ---------------------------------------------------------------------------

_GDELT_DOC_URL: str = "https://api.gdeltproject.org/api/v2/doc/doc"
_GDELT_TIMEOUT: float = 15.0


# ---------------------------------------------------------------------------
# GDELT source
# ---------------------------------------------------------------------------


class GDELTSource(MediaSource):
    """GDELT Global Database – free, no-auth news article search."""

    @property
    def code(self) -> str:  # noqa: D102
        return "gdelt"

    @property
    def name(self) -> str:  # noqa: D102
        return "GDELT Global Database"

    async def query(self, name_en: str, name_he: Optional[str]) -> MediaResult:  # noqa: D102
        query = name_en
        if name_he:
            query = f"{name_en} OR {name_he}"

        url = (
            f"{_GDELT_DOC_URL}"
            f"?query={urllib.parse.quote(query)}"
            f"&mode=ArtList"
            f"&maxrecords=10"
            f"&sort=DateDesc"
            f"&format=json"
        )

        try:
            async with httpx.AsyncClient(timeout=_GDELT_TIMEOUT) as client:
                resp = await client.get(
                    url,
                    headers={
                        "Accept": "application/json",
                        "User-Agent": "OSINT-DD-Dashboard/1.0",
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    articles = self._extract_articles(data, name_en, name_he)

                    if articles:
                        self.record_success()
                        return self._make_result(
                            status="flagged",
                            articles=articles,
                            source_url="https://www.gdeltproject.org/",
                            articles_found=len(articles),
                        )

                    self.record_success()
                    return self._make_result(
                        status="clear",
                        source_url="https://www.gdeltproject.org/",
                    )

                logger.warning(
                    "GDELT returned HTTP %d for name='%s'",
                    resp.status_code,
                    name_en,
                )
                self.record_failure()
                return self._make_result(
                    status="error",
                    source_url="https://www.gdeltproject.org/",
                )

        except httpx.HTTPError as exc:
            logger.warning("GDELT HTTP error: %s", exc)
            self.record_failure()
            return self._make_result(
                status="error",
                source_url="https://www.gdeltproject.org/",
            )
        except Exception as exc:
            logger.warning("GDELT unexpected error: %s", exc)
            self.record_failure()
            return self._make_result(
                status="error",
                source_url="https://www.gdeltproject.org/",
            )

    # -- Helpers ------------------------------------------------------------

    @staticmethod
    def _extract_articles(
        data: Dict[str, Any],
        name_en: str,
        name_he: Optional[str],
    ) -> List[Dict[str, str]]:
        """Parse GDELT JSON response and filter articles by subject name.

        Only articles whose titles contain the subject's English or Hebrew
        name are included.
        """
        articles: List[Dict[str, str]] = []
        name_en_lower = name_en.lower()
        name_he_lower = (name_he or "").lower()

        for art in data.get("articles", []):
            title = art.get("title", "")
            title_lower = title.lower()

            # Filter: title must mention the subject
            if name_en_lower not in title_lower and (
                not name_he_lower or name_he_lower not in title_lower
            ):
                continue

            articles.append(
                {
                    "title": title,
                    "url": art.get("url", ""),
                    "date": art.get("seendate", ""),
                    "source": art.get("domain", "GDELT"),
                    "snippet": title[:200],
                }
            )

        return articles
