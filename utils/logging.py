from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from utils.paths import log_dir

LOGGER_NAME = "ssm_spiceconex"
LOG_FILENAME = "ssm-spiceconex.log"


def _fallback_log_dir() -> Path:
    path = Path.cwd() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _resolve_log_dir() -> Path:
    override = os.environ.get("SSM_SPICECONEX_LOG_DIR", "").strip()
    if override:
        path = Path(override).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        return path
    try:
        return log_dir()
    except (OSError, PermissionError):
        return _fallback_log_dir()


def configure_logging() -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    if logger.handlers:
        return logger

    directory = _resolve_log_dir()
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = RotatingFileHandler(
        directory / LOG_FILENAME,
        maxBytes=2 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
        delay=False,
    )
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"))
    logger.addHandler(handler)
    logger.info("Logging initialized | log_file=%s", directory / LOG_FILENAME)
    return logger


def reset_logging_session() -> logging.Logger:
    """Begin a new application-session log without keeping stale entries.

    Module imports initialise logging before the Qt entry point runs, so the
    reset deliberately lives here and is called once from ``main``.  Closing
    handlers first is essential on Windows, where an open handler prevents the
    active file from being replaced or deleted.
    """
    logger = logging.getLogger(LOGGER_NAME)
    for handler in list(logger.handlers):
        handler.flush()
        handler.close()
        logger.removeHandler(handler)

    directory = _resolve_log_dir()
    try:
        for previous_log in directory.glob(f"{LOG_FILENAME}*"):
            if previous_log.is_file():
                previous_log.unlink()
    except OSError:
        # Logging must never prevent SpiceConex from starting.  The fresh
        # active log will still be created below when the directory allows it.
        pass
    return configure_logging()


def get_logger(name: str | None = None) -> logging.Logger:
    configure_logging()
    return logging.getLogger(LOGGER_NAME if not name else f"{LOGGER_NAME}.{name}")


def log_path() -> Path:
    return _resolve_log_dir() / LOG_FILENAME


def log_directory() -> Path:
    return _resolve_log_dir()
