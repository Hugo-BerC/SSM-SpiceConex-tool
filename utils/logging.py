from __future__ import annotations

import logging
import os
import tempfile
import uuid
from functools import lru_cache
from logging.handlers import RotatingFileHandler
from pathlib import Path

from utils.paths import log_dir
from utils.redaction import redact_sensitive

LOGGER_NAME = "ssm_spiceconex"
LOG_FILENAME = "ssm-spiceconex.log"


@lru_cache(maxsize=1)
def _fallback_log_dir() -> Path:
    return Path(tempfile.mkdtemp(prefix="spiceconex-logs-"))


def _resolve_log_dir() -> Path:
    override = os.environ.get("SSM_SPICECONEX_LOG_DIR", "").strip()
    if override:
        path = Path(override).expanduser()
        try:
            path.mkdir(parents=True, exist_ok=True)
            return path
        except OSError:
            pass
    try:
        return log_dir()
    except (OSError, PermissionError):
        try:
            return _fallback_log_dir()
        except OSError:
            return Path(tempfile.gettempdir()) / "SSM-SpiceConex" / "logs"


class RedactingFormatter(logging.Formatter):
    def format(self, record):
        # Redact the fully formatted traceback too, not just record.msg/args.
        return redact_sensitive(super().format(record))


def configure_logging() -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    if logger.handlers:
        return logger

    directory = _resolve_log_dir()
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        handler = RotatingFileHandler(
            directory / LOG_FILENAME, maxBytes=2 * 1024 * 1024,
            backupCount=5, encoding="utf-8", delay=False,
        )
    except OSError:
        handler = logging.StreamHandler()
    handler.setFormatter(RedactingFormatter("%(asctime)s | %(levelname)-8s | %(name)s | pid=%(process)d | %(threadName)s | %(message)s"))
    logger.addHandler(handler)
    logger.info("Logging initialized | log_file=%s", directory / LOG_FILENAME)
    return logger


def reset_logging_session() -> logging.Logger:
    """Mark a new session while retaining bounded history for diagnostics."""
    logger = logging.getLogger(LOGGER_NAME)
    for handler in list(logger.handlers):
        try:
            handler.flush()
            handler.close()
        except (OSError, ValueError):
            pass
        logger.removeHandler(handler)

    logger = configure_logging()
    logger.info("Application session started | session=%s", uuid.uuid4().hex[:12])
    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    configure_logging()
    return logging.getLogger(LOGGER_NAME if not name else f"{LOGGER_NAME}.{name}")


def log_path() -> Path:
    return _resolve_log_dir() / LOG_FILENAME


def log_directory() -> Path:
    return _resolve_log_dir()
