"""Safely generate IAM Identity Center profiles from AWS Organizations.

The module deliberately uses Python/boto3 instead of a platform shell script so
the same workflow works from native Windows, macOS and Linux installations.
"""
from __future__ import annotations

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


def bootstrap_sso_profiles(request: ProfileSetupRequest) -> dict:
    """Validate supplied credentials and replace AWS CLI config with SSO profiles.

    Both pre-existing files are timestamped before replacement, matching the
    supplied bootstrap script while avoiding shell and jq dependencies.
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

    used_names: set[str] = set()
    profiles: list[tuple[str, str]] = []
    for account in accounts:
        account_id = str(account.get("Id", "")).strip()
        if not account_id:
            continue
        base = sanitize_profile_name(str(account.get("Name", "")), account_id)
        profile = base
        if profile in used_names:
            profile = f"{base}-{account_id}"
        used_names.add(profile)
        profiles.append((profile, account_id))
    if not profiles:
        raise RuntimeError("AWS Organizations returned accounts without account IDs.")

    config_lines = ["[default]", f"region = {request.region.strip()}", f"output = {request.output.strip() or 'json'}", ""]
    for profile, account_id in profiles:
        config_lines.extend([
            f"[profile {profile}]",
            f"sso_start_url = {request.sso_start_url.strip()}",
            f"sso_account_id = {account_id}",
            f"sso_region = {request.sso_region.strip()}",
            f"sso_role_name = {request.sso_role_name.strip()}",
            f"region = {request.region.strip()}",
            f"output = {request.output.strip() or 'json'}",
            "",
        ])
    credentials_lines = ["[default]", f"aws_access_key_id = {request.access_key_id.strip()}", f"aws_secret_access_key = {request.secret_access_key.strip()}"]
    if request.session_token.strip():
        credentials_lines.append(f"aws_session_token = {request.session_token.strip()}")
    credentials_lines.append("")

    config_path, credentials_path = aws_paths()
    backups = [backup for backup in (_backup(config_path), _backup(credentials_path)) if backup]
    _write_private(config_path, "\n".join(config_lines))
    _write_private(credentials_path, "\n".join(credentials_lines))
    return {
        "account_id": identity.get("Account", ""),
        "arn": identity.get("Arn", ""),
        "profiles": [profile for profile, _account_id in profiles],
        "config_path": str(config_path),
        "credentials_path": str(credentials_path),
        "backups": [str(path) for path in backups],
    }
