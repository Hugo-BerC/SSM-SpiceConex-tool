"""Shared AWS error categories for recovery, UI messages and partial reports."""
from __future__ import annotations

from botocore.exceptions import (
    ClientError, ConnectionClosedError, ConnectTimeoutError, EndpointConnectionError,
    HTTPClientError, ProxyConnectionError, ReadTimeoutError, SSLError,
)

from aws.auth import is_session_unavailable


def classify_error(exc: Exception) -> str:
    if is_session_unavailable(exc):
        return "authentication"
    if isinstance(exc, ClientError):
        error = exc.response.get("Error", {})
        code = str(error.get("Code", "")).lower()
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if "throttl" in code or code in {"requestlimitexceeded", "toomanyrequestsexception", "slowdown"} or status == 429:
            return "throttling"
        if "accessdenied" in code or code in {"unauthorizedoperation", "forbidden", "authorizationerror"} or status == 403:
            return "permission"
        if status and status >= 500:
            return "service"
        return "aws"
    if isinstance(exc, (EndpointConnectionError, ConnectTimeoutError, ReadTimeoutError,
                        ConnectionClosedError, ProxyConnectionError, SSLError, HTTPClientError)):
        return "network"
    text = str(exc).lower()
    if any(word in text for word in ("accessdenied", "access denied", "not authorized", "forbidden", "unauthorizedoperation")):
        return "permission"
    if any(word in text for word in ("throttl", "too many requests", "requestlimitexceeded")):
        return "throttling"
    if any(word in text for word in ("could not connect", "could not be reached", "endpoint connection",
                                     "connection timeout", "connect timeout", "read timeout", "proxyconnection")):
        return "network"
    return "other"


def error_code(exc: Exception) -> str:
    if isinstance(exc, ClientError):
        return str(exc.response.get("Error", {}).get("Code", "AWS error"))
    return type(exc).__name__
