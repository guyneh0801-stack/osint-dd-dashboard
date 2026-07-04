"""Google News RSS adapter.

Queries Google News via its public RSS endpoint for articles that mention
the screened subject.  The RSS feed is free, requires no authentication,
and is more resistant to bot detection than the HTML search page.

The adapter uses targeted search queries with the subject's name and
filters results to include only articles whose titles contain the name.
A small set of negative keywords is used to flag potentially adverse
articles.  Rate limiting (2-second sleep between requests) keeps the
adapter respectful of Google's infrastructure.
"""

from __future__ import annotations

import asyncio
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

import httpx

from .base import MediaResult, MediaSource

logger = __import__("logging").getLogger(__name__)

# ---------------------------------------------------------------------------
# Google News RSS configuration
# ---------------------------------------------------------------------------

_GOOGLE_NEWS_RSS_URL: str = "https://news.google.com/rss/search"
_GOOGLE_NEWS_TIMEOUT: float = 15.0
_RATE_LIMIT_DELAY: float = 2.0  # seconds between consecutive requests

# Keywords that suggest an article may be adverse / negative
_NEGATIVE_KEYWORDS: List[str] = [
    "sanctions",
    "fraud",
    "lawsuit",
    "investigation",
    "crime",
    "convicted",
    "charged",
    "guilty",
    "money laundering",
    "terrorist",
    "corruption",
    "bribery",
    "embezzlement",
]

# Realistic browser User-Agent to avoid bot detection
_BROWSER_USER_AGENT: str = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


# ---------------------------------------------------------------------------
# Google News RSS source
# ---------------------------------------------------------------------------


class GoogleNewsRSSSource(MediaSource):
    """Google News RSS – free, no-auth news search with rate limiting."""

    @property
    def code(self) -> str:  # noqa: D102
        return "google_news_rss"

    @property
    def name(self) -> str:  # noqa: D102
        return "Google News RSS"

    async def query(self, name_en: str, name_he: Optional[str]) -> MediaResult:  # noqa: D102
        all_articles: List[Dict[str, Any]] = []

        # Build search queries – one per name variant
        queries: List[str] = [f'"{name_en}"']
        if name_he:
            queries.append(f'"{name_he}"')

        for idx, q in enumerate(queries):
            try:
                articles = await self._fetch_rss(q, name_en, name_he)
                all_articles.extend(articles)
            except Exception as exc:
                # Log but continue – don't fail the whole screening because
                # one query (or the entire RSS fetch) errored out.
                logger.warning(
                    "Google News RSS query #%d failed for name='%s': %s",
                    idx + 1,
                    name_en,
                    exc,
                )

            # Rate limiting: sleep between requests (but not after the last)
            if len(queries) > 1 and idx < len(queries) - 1:
                await asyncio.sleep(_RATE_LIMIT_DELAY)

        if all_articles:
            self.record_success()
            return self._make_result(
                status="flagged",
                articles=all_articles,
                source_url="https://news.google.com/",
                articles_found=len(all_articles),
            )

        self.record_success()
        return self._make_result(
            status="clear",
            source_url="https://news.google.com/",
        )

    # -- RSS fetch helpers --------------------------------------------------

    async def _fetch_rss(
        self,
        query: str,
        name_en: str,
        name_he: Optional[str],
    ) -> List[Dict[str, Any]]:
        """Execute a single RSS fetch and return parsed, filtered articles."""
        rss_url = (
            f"{_GOOGLE_NEWS_RSS_URL}"
            f"?q={urllib.parse.quote(query)}"
            f"&hl=en-US&gl=US&ceid=US:en"
        )

        async with httpx.AsyncClient(timeout=_GOOGLE_NEWS_TIMEOUT) as client:
            resp = await client.get(
                rss_url,
                headers={
                    "User-Agent": _BROWSER_USER_AGENT,
                    "Accept": (
                        "application/rss+xml,application/xml;q=0.9,*/*;q=0.8"
                    ),
                },
            )

        if resp.status_code != 200:
            logger.warning(
                "Google News RSS returned HTTP %d for query='%s'",
                resp.status_code,
                query,
            )
            return []

        return self._parse_rss_items(resp.content, name_en, name_he)

    @staticmethod
    def _parse_rss_items(
        content: bytes,
        name_en: str,
        name_he: Optional[str],
    ) -> List[Dict[str, Any]]:
        """Parse RSS XML content and extract articles mentioning the subject."""
        articles: List[Dict[str, Any]] = []
        name_en_lower = name_en.lower()
        name_he_lower = (name_he or "").lower()

        try:
            root = ET.fromstring(content)
        except ET.ParseError as exc:
            logger.warning("Google News RSS XML parse error: %s", exc)
            return []

        # RSS 2.0: <rss><channel><item>...</item></channel></rss>
        items = root.findall(".//item")

        for item in items[:10]:  # Cap at 10 articles per query
            title_elem = item.find("title")
            link_elem = item.find("link")
            pub_date_elem = item.find("pubDate")
            source_elem = item.find("source")

            title = title_elem.text if title_elem is not None else ""
            link = link_elem.text if link_elem is not None else ""
            pub_date = pub_date_elem.text if pub_date_elem is not None else ""
            source_name = source_elem.text if source_elem is not None else "Google News"

            # Only include if the title contains the subject's name
            title_lower = title.lower()
            if name_en_lower not in title_lower and (
                not name_he_lower or name_he_lower not in title_lower
            ):
                continue

            # Check for negative keywords in the title
            has_negative = any(kw in title_lower for kw in _NEGATIVE_KEYWORDS)

            articles.append(
                {
                    "title": title,
                    "url": link,
                    "date": pub_date,
                    "source": source_name,
                    "snippet": title[:200],
                    "has_negative_keywords": has_negative,
                }
            )

        return articles
