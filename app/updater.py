"""Safe, out-of-process updates for a Git checkout of SpiceConex."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from packaging.version import InvalidVersion, Version

try:
    from app.config import APP_VERSION, GITHUB_BRANCH, GITHUB_REPOSITORY
except ModuleNotFoundError:
    # The coordinator is copied to a temporary directory before it updates the
    # checkout, where the package is deliberately unavailable.
    APP_VERSION = "0.0.0"
    GITHUB_BRANCH = "main"
    GITHUB_REPOSITORY = ""


@dataclass(frozen=True)
class UpdateTarget:
    version: Version
    tag: str
    sha: str


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=False)


def _error(result: subprocess.CompletedProcess[str], fallback: str) -> RuntimeError:
    return RuntimeError(result.stderr.strip() or result.stdout.strip() or fallback)


def _normalise_tag(tag: str) -> Version | None:
    try:
        return Version(tag.strip().removeprefix("v"))
    except InvalidVersion:
        return None


def is_git_worktree(app_dir: Path) -> bool:
    return _git(["rev-parse", "--is-inside-work-tree"], app_dir).returncode == 0


def _working_tree_problem(app_dir: Path, branch: str) -> str | None:
    status = _git(["status", "--porcelain"], app_dir)
    if status.returncode:
        return "Unable to inspect the local Git working tree."
    if status.stdout.strip():
        return "Local changes were detected; updates never overwrite them automatically."
    current = _git(["branch", "--show-current"], app_dir)
    if current.returncode or current.stdout.strip() != branch:
        return f"Updates require the checked-out '{branch}' branch."
    return None


def _github_tags(repository: str) -> list[dict]:
    url = f"https://api.github.com/repos/{repository}/tags?per_page=100"
    request = Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "SSM-SpiceConex"})
    try:
        with urlopen(request, timeout=10) as response:
            payload = json.load(response)
    except HTTPError as exc:
        if exc.code == 404:
            return []
        raise RuntimeError(f"GitHub could not provide version tags (HTTP {exc.code}).") from exc
    except URLError as exc:
        raise RuntimeError("Could not reach GitHub to check for updates.") from exc
    if not isinstance(payload, list):
        raise RuntimeError("GitHub returned an unexpected version response.")
    return payload


def latest_published_target(repository: str) -> UpdateTarget | None:
    targets: list[UpdateTarget] = []
    for item in _github_tags(repository):
        tag = str(item.get("name", ""))
        version = _normalise_tag(tag)
        sha = str((item.get("commit") or {}).get("sha", ""))
        if version is not None and sha:
            targets.append(UpdateTarget(version, tag, sha))
    return max(targets, key=lambda target: target.version, default=None)


def check_for_update(app_dir: Path, repository: str = GITHUB_REPOSITORY, branch: str = GITHUB_BRANCH, installed_version: str = APP_VERSION) -> dict:
    """Return a UI-ready update status without altering local state."""
    if not shutil.which("git"):
        raise RuntimeError("Git is not installed. Run setup.sh to install it.")
    if not is_git_worktree(app_dir):
        raise RuntimeError("This installation is not a Git working tree. Clone it from GitHub to enable updates.")
    remote = _git(["remote", "get-url", "origin"], app_dir)
    if remote.returncode or not remote.stdout.strip():
        raise RuntimeError("The local Git repository has no 'origin' remote configured.")
    target = latest_published_target(repository)
    if target is None:
        return {"available": False, "installed_version": installed_version, "available_version": None,
                "reason": "No published Git tag exists yet; this development build cannot be upgraded automatically."}
    try:
        installed = Version(installed_version.removeprefix("v"))
    except InvalidVersion as exc:
        raise RuntimeError(f"Installed application version is invalid: {installed_version}") from exc
    problem = _working_tree_problem(app_dir, branch)
    return {"available": target.version > installed, "installed_version": installed_version,
            "available_version": str(target.version), "tag": target.tag, "remote_sha": target.sha,
            "can_apply": problem is None, "reason": problem or ""}


def _process_alive(pid: int) -> bool:
    if os.name == "nt":
        result = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True, check=False)
        return str(pid) in result.stdout
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _acquire_lock(app_dir: Path) -> Path:
    lock = app_dir / ".spiceconex-update.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise RuntimeError("Another SpiceConex update is already in progress.") from exc
    os.write(fd, str(os.getpid()).encode())
    os.close(fd)
    return lock


def _venv_python(app_dir: Path) -> Path:
    local = app_dir / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return local if local.exists() else Path(sys.executable)


def apply_update(app_dir: Path, branch: str = GITHUB_BRANCH, tag: str | None = None, pid: int | None = None) -> None:
    """Prepare a published tag's requirements, then fast-forward to that tag."""
    lock = _acquire_lock(app_dir)
    requirements_file: Path | None = None
    try:
        if pid:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and _process_alive(pid):
                time.sleep(0.25)
            if _process_alive(pid):
                raise RuntimeError("SpiceConex did not close within 30 seconds; update cancelled.")
        problem = _working_tree_problem(app_dir, branch)
        if problem:
            raise RuntimeError(problem)
        if not tag:
            raise RuntimeError("No published version tag was supplied for the update.")
        fetched = _git(["fetch", "--tags", "origin", branch], app_dir)
        if fetched.returncode:
            raise _error(fetched, "Git fetch failed.")
        remote_ref = f"origin/{branch}"
        tag_ref = f"refs/tags/{tag}"
        reachable = _git(["merge-base", "--is-ancestor", tag_ref, remote_ref], app_dir)
        if reachable.returncode:
            raise RuntimeError("The published version tag is not reachable from the configured update branch.")
        remote_requirements = _git(["show", f"{tag_ref}:requirements.txt"], app_dir)
        if remote_requirements.returncode:
            raise _error(remote_requirements, "The update does not contain requirements.txt.")
        handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", prefix="spiceconex-requirements-", suffix=".txt", delete=False)
        requirements_file = Path(handle.name)
        handle.write(remote_requirements.stdout)
        handle.close()
        install = subprocess.run([str(_venv_python(app_dir)), "-m", "pip", "install", "-r", str(requirements_file), "--disable-pip-version-check", "--no-input"], cwd=app_dir, text=True, capture_output=True, check=False)
        if install.returncode:
            raise _error(install, "Python dependency preparation failed; source files were not changed.")
        pulled = _git(["merge", "--ff-only", tag_ref], app_dir)
        if pulled.returncode:
            raise _error(pulled, "Git fast-forward update failed; source files were not changed.")
    finally:
        if requirements_file:
            requirements_file.unlink(missing_ok=True)
        lock.unlink(missing_ok=True)


def launch_update_process(app_dir: Path, pid: int, tag: str) -> subprocess.Popen:
    """Copy the coordinator outside the checkout, then start it detached."""
    staging = Path(tempfile.mkdtemp(prefix="spiceconex-updater-"))
    coordinator = staging / "updater.py"
    shutil.copy2(Path(__file__), coordinator)
    return subprocess.Popen([str(_venv_python(app_dir)), str(coordinator), "--apply", str(app_dir), GITHUB_BRANCH, tag, str(pid)], cwd=staging, close_fds=True)


def _restart(app_dir: Path) -> None:
    subprocess.Popen([str(_venv_python(app_dir)), str(app_dir / "ssm_spiceconex.py")], cwd=app_dir, close_fds=True)


def _main() -> int:
    if len(sys.argv) < 6 or sys.argv[1] != "--apply":
        return 2
    app_dir, branch, tag, pid = Path(sys.argv[2]).resolve(), sys.argv[3], sys.argv[4], int(sys.argv[5])
    try:
        apply_update(app_dir, branch, tag, pid)
        _restart(app_dir)
    except Exception as exc:
        (app_dir / "update-error.log").write_text(f"SpiceConex update failed: {exc}\n", encoding="utf-8")
        _restart(app_dir)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
