"""Config file loader — reads config.json for API keys and settings.

This module provides a fallback mechanism: it reads config.json from the
backend directory and exposes the values. Environment variables always
override config.json values for security.

Usage:
    from core.config_file import config_file
    api_key = config_file.get("opensanctions_api_key", "")
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

# Default config file location (same directory as backend)
DEFAULT_CONFIG_PATH = Path(__file__).parent.parent / "config.json"


def _load_config_file(path: Path) -> Dict[str, Any]:
    """Read and parse config.json. Returns empty dict on error."""
    try:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except (json.JSONDecodeError, OSError, IOError):
        pass
    return {}


class ConfigFile:
    """Simple key-value config reader backed by config.json.

    Values are read once at import time. Use ``reload()`` to refresh.
    Environment variables always take precedence over config.json.
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = path or DEFAULT_CONFIG_PATH
        self._data: Dict[str, Any] = _load_config_file(self._path)

    def get(self, key: str, default: Any = None) -> Any:
        """Get a config value.

        Priority:
        1. Environment variable (uppercase, e.g. OPENSANCTIONS_API_KEY)
        2. config.json key (lowercase, e.g. opensanctions_api_key)
        3. Default value
        """
        # 1. Check environment variable (uppercase with underscores)
        env_key = key.upper()
        env_val = os.environ.get(env_key)
        if env_val is not None and env_val != "":
            # Convert boolean strings
            if env_val.lower() in ("true", "1", "yes"):
                return True
            if env_val.lower() in ("false", "0", "no"):
                return False
            return env_val

        # 2. Check config.json
        if key in self._data and self._data[key] != "":
            return self._data[key]

        # 3. Return default
        return default

    def get_bool(self, key: str, default: bool = False) -> bool:
        """Get a boolean config value."""
        val = self.get(key, default)
        if isinstance(val, bool):
            return val
        if isinstance(val, str):
            return val.lower() in ("true", "1", "yes", "on")
        return bool(val)

    def get_int(self, key: str, default: int = 0) -> int:
        """Get an integer config value."""
        val = self.get(key, default)
        try:
            return int(val)
        except (ValueError, TypeError):
            return default

    def get_str(self, key: str, default: str = "") -> str:
        """Get a string config value."""
        val = self.get(key, default)
        if val is None:
            return default
        return str(val)

    def reload(self) -> None:
        """Reload config from disk."""
        self._data = _load_config_file(self._path)

    @property
    def path(self) -> Path:
        """Path to the config file."""
        return self._path

    @property
    def is_loaded(self) -> bool:
        """True if config.json was found and loaded."""
        return bool(self._data)


# Singleton instance
config_file = ConfigFile()
