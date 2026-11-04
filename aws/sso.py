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
    """Return the code-bearing authorization URL emitted by AWS CLI.

    AWS CLI 2.33 introduced ``signin.aws/platform/authorize`` output where the
    code and the alternative autofill URL are printed on separate lines.  Keep
    support for the older ``#/device`` flow while deliberately waiting for the
    autofill URL in the newer flow instead of opening its first, code-less URL.
    """
    device_urls: list[str] = []
    autofill_urls: list[str] = []
    code: str | None = None
    expect_code = False
    expect_autofill_url = False
    for line in lines:
        lower = line.lower()
        if "then enter the code" in lower or "code to continue" in lower:
            expect_code = True
        if "alternatively" in lower or "autofill the code" in lower:
            expect_autofill_url = True
        for match in _URL_RE.findall(line):
            url = _clean_url(match)
            if "user_code=" in url.lower():
                return url
            if expect_autofill_url:
                autofill_urls.append(url)
                expect_autofill_url = False
                continue
            if "/device" in url.lower():
                device_urls.append(url)
        if expect_code:
            match = _CODE_RE.fullmatch(line.strip())
            if match:
                code = match.group(1).upper()
                expect_code = False
        if "user code" in lower or "code:" in lower or "code to continue" in lower:
            match = _CODE_RE.search(line)
            if match:
                code = match.group(1).upper()
    if autofill_urls:
        return autofill_urls[-1]
    if device_urls and code:
        # AWS IAM Identity Center expects user_code in the URL fragment route,
        # e.g. https://d-xxxx.awsapps.com/start/#/device?user_code=ABCD-EFGH.
        return f"{device_urls[0]}?user_code={quote(code)}"
    return device_urls[0] if device_urls else None


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
    # Do not iterate over stdout here: that iteration itself blocks until the
    # AWS CLI exits, making the wait(timeout=...) below ineffective. communicate
    # drains the remaining output while enforcing the operator-facing deadline.
    try:
        remaining_output, _ = process.communicate(timeout=max(AWS_TIMEOUT_SECONDS, 900))
        output.extend(line for line in remaining_output.splitlines() if line)
        return_code = process.returncode
    except subprocess.TimeoutExpired:
        process.kill()
        remaining_output, _ = process.communicate()
        output.extend(line for line in remaining_output.splitlines() if line)
        raise RuntimeError(
            "AWS SSO authorization was not completed within 15 minutes. "
            "Complete the browser authorization opened by SpiceConex, then retry; "
            "or run 'aws sso login --profile <profile>' manually."
        )

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
