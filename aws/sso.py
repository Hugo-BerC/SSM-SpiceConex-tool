from __future__ import annotations

import re
import shutil
import subprocess
import webbrowser
from urllib.parse import quote

from app.config import AWS_TIMEOUT_SECONDS
from aws.environment import aws_environment
from utils.logging import get_logger

logger = get_logger("aws.sso")

_URL_RE = re.compile(r"https?://[^\s<>()\[\]{}\"']+", re.IGNORECASE)
_CODE_RE = re.compile(r"\b([A-Z0-9]{4}-[A-Z0-9]{4})\b", re.IGNORECASE)


def _clean_url(value: str) -> str:
    return value.rstrip(".,;:!?")


def _complete_device_url(lines: list[str]) -> str | None:
    """Prefer AWS CLI's complete URL; otherwise append its device code to the device URL."""
    urls: list[str] = []
    code: str | None = None
    for line in lines:
        for match in _URL_RE.findall(line):
            url = _clean_url(match)
            if "user_code=" in url.lower():
                return url
            if "/device" in url.lower():
                urls.append(url)
        if "user code" in line.lower() or "code:" in line.lower() or "code to continue" in line.lower():
            match = _CODE_RE.search(line)
            if match:
                code = match.group(1).upper()
    if urls and code:
        # AWS IAM Identity Center expects user_code in the URL fragment route,
        # e.g. https://d-xxxx.awsapps.com/start/#/device?user_code=ABCD-EFGH.
        return f"{urls[0]}?user_code={quote(code)}"
    return urls[0] if urls else None


def login_profile(profile: str) -> str | None:
    """Refresh SSO credentials and open the device URL including its one-time code.

    We deliberately use ``--no-browser`` and open the complete URL ourselves. Some
    Linux desktop/browser handlers open only the base ``#/device`` page when AWS CLI
    launches the browser, leaving the code field empty. Do not open the base URL as
    soon as it appears; wait until the code-bearing URL can be built.
    """
    profile = profile.strip()
    if not profile:
        raise ValueError("No AWS profile was supplied for SSO login.")
    aws_path = shutil.which("aws") or shutil.which("aws.exe")
    if not aws_path:
        raise RuntimeError("AWS CLI not found in PATH. SSO login requires AWS CLI v2.")

    args = [aws_path, "sso", "login", "--profile", profile, "--no-browser"]
    logger.info("Starting AWS SSO login | profile=%r", profile)
    process = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=aws_environment(),
    )
    output: list[str] = []
    opened_url: str | None = None
    if process.stdout:
        for raw_line in process.stdout:
            line = raw_line.rstrip()
            output.append(line)
            logger.debug("AWS SSO CLI: %s", line)
            candidate = _complete_device_url(output)
            if candidate and candidate != opened_url:
                if "user_code=" in candidate.lower():
                    opened_url = candidate
                    logger.info("Opening AWS SSO authorization URL with device code | profile=%r", profile)
                    if not webbrowser.open(candidate, new=2):
                        logger.warning("Browser handler did not confirm opening the AWS SSO URL")
                    break
    # Continue draining stdout after opening the browser so the CLI can finish its
    # polling flow. The first loop breaks only after the full URL is found.
    if process.stdout:
        for raw_line in process.stdout:
            output.append(raw_line.rstrip())
    try:
        return_code = process.wait(timeout=max(AWS_TIMEOUT_SECONDS, 900))
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        raise RuntimeError("AWS SSO login timed out after 15 minutes. Run 'aws sso login --profile <profile>' manually and retry.")

    if return_code != 0:
        details = "\n".join(output[-16:]).strip()
        if "InvalidGrantException" in details or "invalid_grant" in details.lower():
            raise RuntimeError(
                "AWS SSO authorization expired or was rejected (InvalidGrantException). "
                "Close the old authorization tab, retry Refresh, and complete the newly opened URL promptly.\n\n"
                + details
            )
        raise RuntimeError(details or f"aws sso login failed with exit code {return_code}")
    logger.info("AWS SSO login succeeded | profile=%r", profile)
    return opened_url
