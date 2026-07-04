"""Background downloader that keeps XML sanctions lists cached locally."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx

logger = __import__("logging").getLogger(__name__)

# Default cache directory (override via SANCTIONS_CACHE_DIR env var)
DEFAULT_CACHE_DIR: str = os.environ.get(
    "SANCTIONS_CACHE_DIR", "/mnt/agents/output/backend/data/sanctions_cache"
)

# Cache freshness threshold in hours
CACHE_MAX_AGE_HOURS: float = 24.0


class StaticSanctionsDownloader:
    """Downloads and refreshes static sanctions XML files.

    Maintains a local cache directory and only re-downloads files when
    the cached copy is older than 24 hours (or when *force=True*).

    Usage::

        dl = StaticSanctionsDownloader("/tmp/sanctions_cache")
        path = await dl.download(
            "https://example.com/sanctions.xml", "sanctions.xml"
        )
    """

    def __init__(self, cache_dir: str | None = None) -> None:
        self.cache_dir = Path(cache_dir or DEFAULT_CACHE_DIR)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    async def download(
        self, url: str, filename: str, force: bool = False
    ) -> Optional[Path]:
        """Download *url* to *filename* in the cache directory.

        Skips the download when a cached file exists and is younger than
        24 hours (unless *force* is ``True``).

        Returns:
            ``Path`` to the cached file, or ``None`` when the download
            failed and no cached file exists.
        """
        filepath = self.cache_dir / filename

        # Check if cache is fresh (< 24 hours)
        if not force and filepath.exists():
            age_hours = (
                datetime.now(timezone.utc).timestamp() - filepath.stat().st_mtime
            ) / 3600
            if age_hours < CACHE_MAX_AGE_HOURS:
                logger.debug("Cache hit for %s (age=%.1fh)", filename, age_hours)
                return filepath

        # Download
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.get(
                    url,
                    headers={"User-Agent": "OSINT-DD-Dashboard/1.0"},
                    follow_redirects=True,
                )
                if resp.status_code == 200:
                    filepath.write_bytes(resp.content)
                    logger.info(
                        "Downloaded %s (%d bytes)", filename, len(resp.content)
                    )
                    return filepath
                else:
                    logger.warning(
                        "Download of %s returned HTTP %d", url, resp.status_code
                    )
        except httpx.TimeoutException:
            logger.warning("Download timeout for %s", url)
        except Exception as exc:
            logger.warning("Download failed for %s: %s", url, exc)

        # Return existing file if download failed
        if filepath.exists():
            age_hours = (
                datetime.now(timezone.utc).timestamp() - filepath.stat().st_mtime
            ) / 3600
            logger.info("Using stale cache for %s (age=%.1fh)", filename, age_hours)
            return filepath

        return None

    def get_cache_path(self, filename: str) -> Path:
        """Return the full path to *filename* in the cache directory."""
        return self.cache_dir / filename

    def get_cache_age_hours(self, filename: str) -> Optional[float]:
        """Return the age of the cached file in hours, or ``None`` if not cached."""
        filepath = self.cache_dir / filename
        if not filepath.exists():
            return None
        return (
            datetime.now(timezone.utc).timestamp() - filepath.stat().st_mtime
        ) / 3600
