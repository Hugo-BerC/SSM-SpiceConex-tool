"""Safely generate IAM Identity Center profiles from AWS Organizations.

The module deliberately uses Python/boto3 instead of a platform shell script so
the same workflow works from native Windows, macOS and Linux installations.
"""
from __future__ import annotations

import configparser
import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import boto3
from botocore.config import Config

from app.config import AWS_TIMEOUT_SECONDS


@dataclass(frozen=True)
class ProfileSetupRequest:
    region: str
    sso_start_url: str
    sso_region: str
    sso_role_name: str
    access_key_id: str
    secret_access_key: str
    session_token: str = ""
    output: str = "json"


def sanitize_profile_name(name: str, account_id: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.@+=,-]+", "-", (name or "").replace("/", "-").replace(" ", "-"))
    cleaned = re.sub(r"-+", "-", cleaned)
    return cleaned.strip("-") or account_id


def aws_paths() -> tuple[Path, Path]:
    """Honor AWS CLI path overrides, otherwise use the current user's ~/.aws."""
    aws_dir = Path.home() / ".aws"
    config_path = Path(os.environ.get("AWS_CONFIG_FILE", aws_dir / "config")).expanduser()
    credentials_path = Path(os.environ.get("AWS_SHARED_CREDENTIALS_FILE", aws_dir / "credentials")).expanduser()
    return config_path, credentials_path


def _client_config() -> Config:
    timeout = max(5, AWS_TIMEOUT_SECONDS)
    return Config(connect_timeout=min(5, timeout), read_timeout=timeout, retries={"mode": "standard", "max_attempts": 1})


def _validate_request(request: ProfileSetupRequest) -> None:
    required = {
        "AWS default region": request.region,
        "AWS SSO start URL": request.sso_start_url,
        "AWS SSO region": request.sso_region,
        "AWS SSO role name": request.sso_role_name,
        "AWS Access Key ID": request.access_key_id,
        "AWS Secret Access Key": request.secret_access_key,
    }
    missing = [label for label, value in required.items() if not value.strip()]
    if missing:
        raise ValueError("Required values are missing: " + ", ".join(missing))
    if request.access_key_id.strip().upper().startswith("ASIA") and not request.session_token.strip():
        raise ValueError("AWS Session Token is required when using temporary ASIA credentials.")


def _backup(path: Path) -> Path | None:
    if not path.exists():
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.name}.{stamp}")
    backup.write_bytes(path.read_bytes())
    return backup


def _write_private(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            # Windows ACLs govern access; replacement is still atomic there.
            pass
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise


def _read_config(path: Path) -> configparser.ConfigParser:
    """Read the AWS config without silently overwriting malformed content."""
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    if not path.exists():
        return parser
    try:
        with path.open("r", encoding="utf-8") as handle:
            parser.read_file(handle)
    except (OSError, configparser.Error) as exc:
        raise RuntimeError(
            f"Unable to safely read the existing AWS config at {path}. "
            "It was not changed. Repair the file and retry."
        ) from exc
    return parser


def _profile_section(profile: str) -> str:
    return "default" if profile == "default" else f"profile {profile}"


def _managed_profile_name(
    parser: configparser.ConfigParser,
    base_name: str,
    account_id: str,
    sso_start_url: str,
    sso_role_name: str,
) -> str:
    """Reuse our matching profile or choose a collision-free profile name.

    An existing, unrelated AWS profile is never overwritten merely because an
    Organizations account happens to have the same display name.
    """
    for section in parser.sections():
        values = parser[section]
        if (
            values.get("sso_account_id", "") == account_id
            and values.get("sso_role_name", "") == sso_role_name
            and values.get("sso_session", "").startswith("spiceconex-")
        ):
            return section.removeprefix("profile ")

    candidates = [base_name, f"{base_name}-{account_id}"]
    for candidate in candidates:
        if not parser.has_section(_profile_section(candidate)):
            return candidate
    suffix = 2
    while parser.has_section(_profile_section(f"{base_name}-{account_id}-{suffix}")):
        suffix += 1
    return f"{base_name}-{account_id}-{suffix}"


def _session_name(start_url: str) -> str:
    digest = hashlib.sha256(start_url.strip().encode("utf-8")).hexdigest()[:10]
    return f"spiceconex-{digest}"


def _serialize_config(parser: configparser.ConfigParser) -> str:
    from io import StringIO

    output = StringIO()
    parser.write(output)
    return output.getvalue()


def bootstrap_sso_profiles(request: ProfileSetupRequest) -> dict:
    """Generate IAM Identity Center profiles without replacing AWS CLI files.

    Bootstrap credentials exist only in memory while Organizations is queried.
    The existing config is merged, unrelated profiles are preserved and the
    shared credentials file is never read or written by this workflow.
    """
    _validate_request(request)
    session = boto3.Session(
        aws_access_key_id=request.access_key_id.strip(),
        aws_secret_access_key=request.secret_access_key.strip(),
        aws_session_token=request.session_token.strip() or None,
        region_name=request.region.strip(),
    )
    identity = session.client("sts", config=_client_config()).get_caller_identity()
    organizations = session.client("organizations", config=_client_config())
    accounts: list[dict] = []
    for page in organizations.get_paginator("list_accounts").paginate():
        accounts.extend(page.get("Accounts", []))

    if not accounts:
        raise RuntimeError("AWS Organizations returned no accounts.")

    profiles: list[tuple[str, str]] = []
    config_path, _credentials_path = aws_paths()
    parser = _read_config(config_path)
    session_name = _session_name(request.sso_start_url)
    session_section = f"sso-session {session_name}"
    if not parser.has_section(session_section):
        parser.add_section(session_section)
    parser.set(session_section, "sso_start_url", request.sso_start_url.strip())
    parser.set(session_section, "sso_region", request.sso_region.strip())

    for account in accounts:
        account_id = str(account.get("Id", "")).strip()
        if not account_id:
            continue
        base = sanitize_profile_name(str(account.get("Name", "")), account_id)
        profile = _managed_profile_name(
            parser,
            base,
            account_id,
            request.sso_start_url,
            request.sso_role_name,
        )
        section = _profile_section(profile)
        if not parser.has_section(section):
            parser.add_section(section)
        parser.set(section, "sso_session", session_name)
        parser.set(section, "sso_account_id", account_id)
        parser.set(section, "sso_role_name", request.sso_role_name.strip())
        parser.set(section, "region", request.region.strip())
        parser.set(section, "output", request.output.strip() or "json")
        profiles.append((profile, account_id))
    if not profiles:
        raise RuntimeError("AWS Organizations returned accounts without account IDs.")

    backup = _backup(config_path)
    _write_private(config_path, _serialize_config(parser))
    return {
        "account_id": identity.get("Account", ""),
        "arn": identity.get("Arn", ""),
        "profiles": [profile for profile, _account_id in profiles],
        "config_path": str(config_path),
        "backups": [str(backup)] if backup else [],
    }
