"""Authentication error classification and read-only SSO profile resolution."""
from __future__ import annotations

import os
from pathlib import Path

from botocore.configloader import load_config
from botocore.exceptions import (
    BotoCoreError, ClientError, CredentialRetrievalError, NoCredentialsError,
    SSOTokenLoadError, TokenRetrievalError, UnauthorizedSSOTokenError,
)


def is_session_unavailable(exc: Exception) -> bool:
    """Distinguish refreshable authentication failures from permissions/network errors."""
    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code", "")
        return code in {"ExpiredToken", "ExpiredTokenException", "InvalidClientTokenId", "UnrecognizedClientException"} or (
            code == "UnauthorizedException" and exc.operation_name == "GetRoleCredentials"
        )
    if isinstance(exc, (NoCredentialsError, SSOTokenLoadError, UnauthorizedSSOTokenError)):
        return True
    if isinstance(exc, BotoCoreError) and not isinstance(exc, (TokenRetrievalError, CredentialRetrievalError)):
        return False
    text = str(exc).lower()
    if any(marker in text for marker in (
        "accessdenied", "access denied", "not authorized", "forbidden", "could not connect",
        "connection error", "endpointconnection", "connect timeout", "read timeout", "proxyconnection",
    )):
        return False
    return any(marker in text for marker in (
        "expiredtoken", "expired token", "unauthorizedsso", "token has expired",
        "unable to locate credentials", "no credentials", "credential should be refreshed",
        "invalidclienttokenid", "unrecognizedclientexception", "security token included in the request is invalid",
        "security token is invalid", "sso session has expired", "sso session associated with this profile has expired",
        "error loading sso token", "unable to refresh credentials", "refresh failed",
    ))


def sso_login_profile(profile: str) -> str | None:
    """Resolve an SSO profile or its source without invoking credential providers.

    Only the AWS config is read; the credentials file and SSO cache are not
    needed to decide whether an interactive SSO login is appropriate.
    """
    path = Path(os.environ.get("AWS_CONFIG_FILE", Path.home() / ".aws" / "config")).expanduser()
    try:
        profiles = load_config(str(path)).get("profiles", {})
    except (BotoCoreError, OSError):
        return None
    visited = set()
    while profile and profile not in visited:
        visited.add(profile)
        config = profiles.get(profile, {})
        if config.get("sso_session") or config.get("sso_start_url"):
            return profile
        profile = config.get("source_profile", "")
    return None
