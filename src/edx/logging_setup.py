"""Structured JSON logging via structlog with rotation to ``logs/pipeline.log``."""

from __future__ import annotations

import contextlib
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Final, cast

import structlog

DEFAULT_LOG_DIR: Final[Path] = Path("logs")
DEFAULT_LOG_FILE: Final[str] = "pipeline.log"
LOG_LEVEL_ENV: Final[str] = "EDX_LOG_LEVEL"
LOG_FILE_ENV: Final[str] = "EDX_LOG_FILE"
MAX_BYTES: Final[int] = 10 * 1024 * 1024
BACKUP_COUNT: Final[int] = 5

_configured: bool = False


def _resolve_level() -> int:
    raw = os.environ.get(LOG_LEVEL_ENV, "INFO").upper()
    return logging.getLevelNamesMapping().get(raw, logging.INFO)


def _resolve_log_path(log_dir: Path | None) -> Path:
    """Resolution order:

    1. ``EDX_LOG_FILE`` env var — explicit absolute or relative path to the
       log file (used by ``edx evolve tick`` to redirect a child pipeline's
       output into ``evolution/runs/{tick_id}/pipeline.log``).
    2. ``log_dir`` keyword argument — directory in which the default
       ``pipeline.log`` is created.
    3. Default ``logs/pipeline.log`` next to the project root.
    """
    env_path = os.environ.get(LOG_FILE_ENV)
    if env_path:
        return Path(env_path)
    base = log_dir if log_dir is not None else DEFAULT_LOG_DIR
    return base / DEFAULT_LOG_FILE


def configure(log_dir: Path | None = None) -> None:
    """Configure structlog + stdlib logging.

    Idempotent: repeated calls reset handlers so tests can rebind the log directory.
    """
    global _configured

    log_path = _resolve_log_path(log_dir)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    level = _resolve_level()

    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        with contextlib.suppress(Exception):
            handler.close()
    root.setLevel(level)

    formatter = logging.Formatter("%(message)s")

    stream_handler = logging.StreamHandler(stream=sys.stdout)
    stream_handler.setLevel(level)
    stream_handler.setFormatter(formatter)
    root.addHandler(stream_handler)

    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    _configured = True


def get_logger(name: str | None = None) -> Any:
    """Return a structlog BoundLogger; typed as ``Any`` because structlog has no stubs."""
    if not _configured:
        configure()
    logger = structlog.get_logger(name) if name else structlog.get_logger()
    return cast(Any, logger)
