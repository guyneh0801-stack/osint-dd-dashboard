"""Structured JSON logging for the OSINT DD Dashboard.

All log records are emitted as JSON Lines to ``stdout`` (for container
orchestrator collection) and to rotating files under ``logs/`` for
local debugging.  The format includes timestamp, level, message,
logger name, and any extra context fields.

Usage::

    from core.logger import get_logger

    logger = get_logger(__name__)
    logger.info("Screening started", extra={"job_id": job_id, "name": name})
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, Optional

from core.config import settings

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Maximum size for a single log file (10 MB)
MAX_BYTES = 10 * 1024 * 1024
# Number of backup files to keep
BACKUP_COUNT = 5

# ---------------------------------------------------------------------------
# JSON Formatter
# ---------------------------------------------------------------------------


class JSONFormatter(logging.Formatter):
    """Emit log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        obj: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Include exception info if present
        if record.exc_info and record.exc_info[1]:
            obj["exception"] = self.formatException(record.exc_info)
        # Include any extra fields
        for key, value in record.__dict__.items():
            if key not in (
                "name",
                "msg",
                "args",
                "levelname",
                "levelno",
                "pathname",
                "filename",
                "module",
                "exc_info",
                "exc_text",
                "stack_info",
                "lineno",
                "funcName",
                "created",
                "msecs",
                "relativeCreated",
                "thread",
                "threadName",
                "processName",
                "process",
                "asctime",
                "message",
            ):
                obj[key] = value
        return json.dumps(obj, default=str)


# ---------------------------------------------------------------------------
# Handler setup
# ---------------------------------------------------------------------------


def _setup_handlers(logger: logging.Logger, logs_dir: Path) -> None:
    """Attach stdout and file handlers to *logger*."""
    logger.setLevel(logging.DEBUG)
    logger.handlers = []  # Clear existing

    # Console handler — plain text for human readability
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter(DEFAULT_LOG_FORMAT, datefmt=DATE_FORMAT))
    logger.addHandler(console)

    # File handler — JSON Lines for machine parsing
    logs_dir.mkdir(parents=True, exist_ok=True)
    file_path = logs_dir / "app.jsonl"
    file_handler = RotatingFileHandler(
        file_path,
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(JSONFormatter())
    logger.addHandler(file_handler)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_logger(name: str) -> logging.Logger:
    """Return a configured logger for *name*.

    The root logger is configured on first call.  Subsequent calls
    return child loggers that inherit the same handlers.
    """
    root = logging.getLogger("osint_dd")
    if not root.handlers:
        _setup_handlers(root, settings.LOGS_DIR)
    return logging.getLogger(name)
