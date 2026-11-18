from __future__ import annotations

import re
import os
import math
import queue
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from urllib.parse import parse_qs, quote, urlsplit, urlunsplit

from aws.environment import aws_environment
from utils.logging import get_logger

logger = get_logger("aws.sso")

_URL_RE = re.compile(r"https?://[^\s<>()\[\]{}\"']+", re.IGNORECASE)
_CODE_RE = re.compile(r"\b([A-Z0-9]{4}-[A-Z0-9]{4})\b", re.IGNORECASE)
SSO_TIMEOUT_SECONDS = 900
_POLL_SECONDS = 0.25
_CLEANUP_SECONDS = 2


def _clean_url(value: str) -> str:
    return value.rstrip(".,;:!?")


def _urls(line: str):
    for match in _URL_RE.findall(line):
        value = _clean_url(match)
        try:
            parts = urlsplit(value)
            if parts.scheme == "https" and parts.hostname and not parts.username and not parts.password:
                yield value
        except ValueError:
            continue


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
        for url in _urls(line):
            if "user_code=" in url.lower():
                return url
            if expect_autofill_url:
                autofill_urls.append(url)
                expect_autofill_url = False
                continue
            parts = urlsplit(url)
            if "/device" in (parts.path + parts.fragment).lower() or (parts.hostname or "").startswith("device.sso."):
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
        parts = urlsplit(device_urls[0])
        if "/device" in parts.fragment.lower():
            separator = "&" if "?" in parts.fragment else "?"
            parts = parts._replace(fragment=parts.fragment + separator + "user_code=" + quote(code))
        else:
            parts = parts._replace(query=parts.query + ("&" if parts.query else "") + "user_code=" + quote(code))
        return urlunsplit(parts)
    return None


def _authorization_url(lines: list[str]) -> str | None:
    device_url = _complete_device_url(lines)
    if device_url:
        return device_url
    for line in lines:
        for url in _urls(line):
            parts = urlsplit(url)
            params = parse_qs(parts.query)
            if (parts.path.rstrip("/").endswith("/authorize") and params.get("response_type") == ["code"]
                    and all(params.get(key) for key in ("client_id", "redirect_uri", "code_challenge"))):
                return url
    return None


def _output_text(value) -> str:
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or ""


def _stop_process(process) -> None:
    """Terminate only the owned CLI process, with a bounded drain/reap."""
    try:
        process.kill()
    except OSError:
        pass
    try:
        process.communicate(timeout=_CLEANUP_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        logger.warning("AWS SSO CLI cleanup did not finish within its deadline")


def _login_error(output: str, return_code: int) -> RuntimeError:
    # Never attach raw CLI output: it can contain URLs, one-time codes or tokens
    # and exceptions are recorded by the UI's worker logger.
    text = output.lower()
    if "invalidgrantexception" in text or "invalid_grant" in text:
        return RuntimeError("AWS SSO authorization expired or was rejected (InvalidGrantException). "
                            "Close the old authorization tab, retry, and complete the new authorization promptly.")
    if "expiredtoken" in text:
        return RuntimeError("AWS SSO authorization expired (ExpiredToken). Start a new login and complete the new authorization promptly.")
    if any(marker in text for marker in ("accessdenied", "access denied", "authorization_declined", "access_denied")):
        return RuntimeError("AWS SSO authorization was denied or cancelled. Check the selected SSO profile "
                            "and complete a new authorization if access is permitted.")
    if any(marker in text for marker in ("could not connect", "endpointconnection", "connectionerror", "connection error",
                                        "proxyconnection", "ssl validation failed", "connect timeout", "read timeout")):
        return RuntimeError("AWS SSO endpoints could not be reached. Check network, proxy and certificate settings before retrying.")
    if "unknown options" in text and "--use-device-code" in text:
        return RuntimeError("This AWS CLI does not support explicit --use-device-code. Update AWS CLI v2 "
                            "or unset SSM_SPICECONEX_SSO_DEVICE_CODE to use its default authorization flow.")
    return RuntimeError(f"AWS SSO login failed with exit code {return_code}. "
                        "Check the profile with 'aws configure sso' and run 'aws sso login --profile <profile>' manually for diagnostics.")


def login_profile(profile: str, *, use_device_code: bool | None = None,
                  timeout_seconds: float = SSO_TIMEOUT_SECONDS) -> str | None:
    """Refresh SSO through the CLI, bounding startup, output and authorization waits.

    Windows retains CLI-managed browser opening. Unix opens only a complete PKCE
    or device authorization URL; no raw output or authorization data is logged.
    """
    profile = profile.strip()
    if not profile:
        raise ValueError("No AWS profile was supplied for SSO login.")
    timeout_seconds = float(timeout_seconds)
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("SSO authorization timeout must be a positive finite number of seconds.")
    if use_device_code is None:
        use_device_code = os.environ.get("SSM_SPICECONEX_SSO_DEVICE_CODE", "").strip().lower() in {"1", "true", "yes"}
    aws_path = shutil.which("aws") or shutil.which("aws.exe")
    if not aws_path:
        raise RuntimeError("AWS CLI not found in PATH. SSO login requires AWS CLI v2.")

    # On Windows, AWS CLI can retain its device-flow output when stdout is a
    # pipe.  Waiting for that text means SpiceConex never reaches the URL and
    # the operator sees no browser. Delegate browser opening to AWS CLI there.
    # Other platforms retain the explicit no-browser flow because it lets us
    # open the complete code-bearing URL ourselves.
    native_browser = sys.platform == "win32"
    args = [aws_path, "sso", "login", "--profile", profile]
    if use_device_code:
        args.append("--use-device-code")
    if not native_browser:
        args.append("--no-browser")
    logger.info("Starting AWS SSO login | profile=%r", profile)
    deadline = time.monotonic() + timeout_seconds
    env = aws_environment()
    env.update(AWS_CLI_AUTO_PROMPT="off", PYTHONUNBUFFERED="1", PYTHONUTF8="1")
    process = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=env,
    )
    output = ""
    opened_url: str | None = None
    completed = False
    browser_results = queue.Queue(maxsize=1)
    browser_cancelled = threading.Event()

    def open_browser(url):
        if browser_cancelled.is_set():
            return
        try:
            opened = bool(webbrowser.open(url, new=2))
        except Exception:
            opened = False
        browser_results.put(opened)

    if native_browser:
        logger.info("Delegating AWS SSO browser launch to AWS CLI on Windows | profile=%r", profile)
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError(f"AWS SSO authorization was not completed within {timeout_seconds:g} seconds. "
                                   "Close the old authorization tab and retry, or run 'aws sso login --profile <profile>' manually.")
            try:
                browser_opened = browser_results.get_nowait()
            except queue.Empty:
                browser_opened = None
            if browser_opened is False and process.poll() != 0:
                raise RuntimeError("Could not open a browser for AWS SSO authorization. "
                                   "Run 'aws sso login --profile <profile>' manually in a browser-enabled environment.")
            try:
                stdout, _ = process.communicate(timeout=remaining if native_browser else min(_POLL_SECONDS, remaining))
                output = _output_text(stdout)
                completed = True
                break
            except subprocess.TimeoutExpired as exc:
                if exc.output is not None:
                    output = _output_text(exc.output)
            if not native_browser and opened_url is None:
                # A timeout may split a URL in the middle. Parse complete lines
                # only; communicate retries retain cumulative output.
                lines = output.splitlines(keepends=True)
                candidate = _authorization_url([line.rstrip() for line in lines if line.endswith(("\n", "\r"))])
                if candidate:
                    opened_url = candidate
                    logger.info("Opening AWS SSO authorization page | profile=%r", profile)
                    threading.Thread(target=open_browser, args=(candidate,), daemon=True,
                                     name="spiceconex-sso-browser").start()
    finally:
        browser_cancelled.set()
        if not completed:
            _stop_process(process)

    if process.returncode != 0:
        raise _login_error(output, process.returncode) from None
    logger.info("AWS SSO login succeeded | profile=%r", profile)
    return opened_url
