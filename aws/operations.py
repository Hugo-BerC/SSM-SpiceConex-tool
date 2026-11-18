"""Coordinate one-time SSO recovery independently from the desktop UI."""
from __future__ import annotations

from aws.auth import is_session_unavailable, sso_login_profile
from aws.session import clear_session_cache
from aws.sso import login_profile
from utils.logging import get_logger

logger = get_logger("aws.operations")


def run_with_sso(profile, operation, *, login=None, resolve=None, clear=None):
    try:
        return operation()
    except Exception as exc:
        if not is_session_unavailable(exc):
            raise
        target = (resolve or sso_login_profile)(profile)
        if target is None:
            raise
        logger.warning("AWS session unavailable | profile=%r | starting SSO login", profile)
        (login or login_profile)(target)
        (clear or clear_session_cache)()
        return operation()
