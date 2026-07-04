"""Application settings using pydantic-settings.

All configuration is loaded from environment variables with sensible
defaults for local development. Sensitive values (e.g. ADMIN_PASSWORD)
must be provided via env vars and are never hard-coded.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application configuration.

    Values are read from environment variables automatically.
    Example: ``export ADMIN_PASSWORD=secret`` sets ``settings.admin_password``.
    """

    # Application metadata
    APP_NAME: str = "OSINT DD Dashboard API"
    """Human-readable application name."""

    API_PREFIX: str = "/api"
    """URL prefix for all REST endpoints."""

    # CORS
    CORS_ORIGINS: List[str] = ["http://localhost:5173"]
    """Allowed origins for Cross-Origin Resource Sharing."""

    # Directories
    REPORTS_DIR: Path = Path("reports")
    """Directory where generated reports are stored."""

    LOGS_DIR: Path = Path("logs")
    """Directory where application logs are written."""

    # Subprocess
    DD_SCREENER_PATH: Path = Path("dd_screener.py")
    """Path to the screening script executed for each job."""

    SCREENING_TIMEOUT: int = 300
    """Maximum seconds a screening subprocess may run before termination."""

    MAX_CONCURRENT_JOBS: int = 5
    """Maximum screening jobs that may run concurrently."""

    # Jurisdiction screening
    ENABLED_JURISDICTIONS: List[str] = [
        "us_ofac",
        "un",
        "uk_hmt",
        "eu",
        "il",
        "ca_sema",
        "au_dfat",
        "fatf_grey",
    ]
    """Jurisdiction codes that are active for sanctions screening."""

    JURISDICTION_CONCURRENCY: int = 5
    """Maximum concurrent outbound queries to jurisdiction data sources."""

    # WebSocket
    WS_BUFFER_SIZE: int = 1000
    """Maximum log lines buffered per job for late-joining WebSocket clients."""

    # Security
    ADMIN_PASSWORD: str = Field(default="changeme", repr=False)
    """Password for admin endpoints. **Must** be overridden in production.
    Can also be set via config.json key 'admin_password'."""

    # Module toggles (can also be set via config.json)
    DD_ENABLE_JURISDICTION: bool = Field(default=True)
    DD_ENABLE_ADVERSE_MEDIA: bool = Field(default=True)
    DD_ENABLE_LITIGATION: bool = Field(default=True)
    DD_ENABLE_STATIC_SANCTIONS: bool = Field(default=True)

    # Database
    DATABASE_URL: Optional[str] = Field(
        default=None,
        description="If set, use PostgreSQL (e.g. postgresql+asyncpg://user:pass@host/db). "
        "If None, use SQLite.",
    )

    DATABASE_PATH: Path = Field(
        default=Path("data/osint_dd.db"),
        description="Filesystem path for the SQLite database (used when DATABASE_URL is not set).",
    )

    class Config:
        env_prefix = ""
        case_sensitive = True

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Override with config.json values (env vars always win via pydantic-settings)
        try:
            from core.config_file import config_file
            if config_file.is_loaded:
                # Admin password from config.json
                cfg_pwd = config_file.get_str("admin_password", "")
                if cfg_pwd and self.ADMIN_PASSWORD == "changeme":
                    self.ADMIN_PASSWORD = cfg_pwd
                # Module toggles from config.json
                if not _env_set("DD_ENABLE_JURISDICTION"):
                    self.DD_ENABLE_JURISDICTION = config_file.get_bool("enable_jurisdiction", True)
                if not _env_set("DD_ENABLE_ADVERSE_MEDIA"):
                    self.DD_ENABLE_ADVERSE_MEDIA = config_file.get_bool("enable_adverse_media", True)
                if not _env_set("DD_ENABLE_LITIGATION"):
                    self.DD_ENABLE_LITIGATION = config_file.get_bool("enable_litigation", True)
                if not _env_set("DD_ENABLE_STATIC_SANCTIONS"):
                    self.DD_ENABLE_STATIC_SANCTIONS = config_file.get_bool("enable_static_sanctions", True)
        except Exception:
            pass


def _env_set(name: str) -> bool:
    """Check if an environment variable is explicitly set (non-empty)."""
    import os
    val = os.environ.get(name, "")
    return val != ""


# Singleton settings instance used across the application.
settings = Settings()
