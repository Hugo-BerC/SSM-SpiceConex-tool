"""Safe, out-of-process updates for a Git checkout of SpiceConex."""
from __future__ import annotations

import json
import re
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlsplit

from packaging.version import InvalidVersion, Version

try:
    from app.config import APP_VERSION, GITHUB_BRANCH, GITHUB_REPOSITORY
    from app.installation import active_installation_problem, write_json_atomic
    from app.runtime import runtime_manifest, selected_python
except ModuleNotFoundError:
    # The coordinator is copied to a temporary directory before it updates the
    # checkout, where the package is deliberately unavailable.
    APP_VERSION = "0.0.0"
    GITHUB_BRANCH = "main"
    GITHUB_REPOSITORY = "Hugo-BerC/SSM-SpiceConex-tool"

    from installation import active_installation_problem, write_json_atomic
    from runtime import runtime_manifest, selected_python


@dataclass(frozen=True)
class UpdateTarget:
    version: Version
    tag: str
    sha: str


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    return subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=False, timeout=120, env=env)


def _repository_from_url(value: str) -> str | None:
    if value.startswith("git@github.com:"):
        path = value[len("git@github.com:"):]
    else:
        parts = urlsplit(value)
        if (parts.hostname != "github.com" or parts.scheme not in {"https", "ssh"}
                or parts.query or parts.fragment or parts.password or parts.port
                or (parts.scheme == "https" and parts.username)
                or (parts.scheme == "ssh" and parts.username != "git")):
            return None
        path = parts.path.lstrip("/")
    path = path.removesuffix(".git")
    return path.casefold() if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", path) else None


def _verify_repository(app_dir: Path, repository: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise RuntimeError("The configured GitHub repository is invalid.")
    top = _git(["rev-parse", "--show-toplevel"], app_dir)
    if top.returncode or Path(top.stdout.strip()).resolve() != app_dir.resolve():
        raise RuntimeError("The installation must be the root of the expected Git checkout.")
    remote = _git(["remote", "get-url", "origin"], app_dir)
    try:
        matches = remote.returncode == 0 and _repository_from_url(remote.stdout.strip()) == repository.casefold()
    except ValueError:
        matches = False
    if not matches:
        raise RuntimeError("The origin remote does not match the configured GitHub repository.")


def _commit(app_dir: Path, ref: str) -> str:
    result = _git(["rev-parse", "--verify", f"{ref}^{{commit}}"], app_dir)
    value = result.stdout.strip().lower()
    if result.returncode or not re.fullmatch(r"[0-9a-f]{40}", value):
        raise RuntimeError("Unable to resolve the expected Git commit.")
    return value


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
        if version is not None and re.fullmatch(r"v?[0-9][A-Za-z0-9.+-]*", tag) and re.fullmatch(r"[0-9a-fA-F]{40}", sha):
            targets.append(UpdateTarget(version, tag, sha))
    return max(targets, key=lambda target: target.version, default=None)


def check_for_update(app_dir: Path, repository: str = GITHUB_REPOSITORY, branch: str = GITHUB_BRANCH, installed_version: str = APP_VERSION) -> dict:
    """Return a UI-ready update status without altering local state."""
    installation_problem = active_installation_problem(app_dir)
    if installation_problem:
        return {
            "available": False,
            "installed_version": installed_version,
            "available_version": None,
            "can_apply": False,
            "reason": installation_problem,
        }
    if not shutil.which("git"):
        raise RuntimeError("Git is not installed. Run setup.sh to install it.")
    if not is_git_worktree(app_dir):
        raise RuntimeError("This installation is not a Git working tree. Clone it from GitHub to enable updates.")
    _verify_repository(app_dir, repository)
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
        result = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True, check=False, timeout=5)
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
    return selected_python(app_dir, local if local.exists() else Path(sys.executable))


_atomic_json = write_json_atomic

def _prepare_environment(app_dir: Path, sha: str, version_dir: Path) -> Path:
    archive = version_dir / "source.zip"
    archived = _git(["archive", "--format=zip", f"--output={archive}", sha], app_dir)
    if archived.returncode:
        raise RuntimeError("Unable to stage the verified source revision.")
    source = version_dir / "source"
    source.mkdir()
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.namelist():
            if not (source / member).resolve().is_relative_to(source.resolve()):
                raise RuntimeError("The update archive contains an unsafe path.")
        bundle.extractall(source)
    if not (source / "app" / "runtime.py").is_file() or "use_active_runtime" not in (
            source / "ssm_spiceconex.py").read_text(encoding="utf-8"):
        raise RuntimeError("This release does not support isolated runtime updates. Use setup to install it.")
    environment = version_dir / "venv"
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    env = dict(os.environ, AWS_EC2_METADATA_DISABLED="true", QT_QPA_PLATFORM="offscreen")
    for name in ("PYTHONPATH", "PYTHONHOME", "PYTHONUSERBASE", "PIP_TARGET", "PIP_PREFIX", "PIP_ROOT", "PIP_USER"):
        env.pop(name, None)
    # Prevent user/system pip settings from redirecting writes outside the new
    # venv. Index/proxy/certificate environment variables remain available.
    env.update(PIP_CONFIG_FILE=os.devnull, PIP_REQUIRE_VIRTUALENV="true")
    commands = [
        ([str(_venv_python(app_dir)), "-m", "venv", str(environment)], "creating the staged Python environment"),
        ([str(python), "-m", "pip", "install", "--no-user", "-r", str(source / "requirements.txt"),
          "--disable-pip-version-check", "--no-input"], "dependency preparation"),
        ([str(python), "-m", "pip", "check"], "dependency compatibility validation"),
        ([str(python), "-I", "-c", "import sys; sys.path.insert(0,sys.argv[1]); import pyside6_poc.main", str(source)],
         "application import validation"),
    ]
    for command, phase in commands:
        result = subprocess.run(command, cwd=source, text=True, capture_output=True, check=False, timeout=900, env=env)
        if result.returncode:
            raise RuntimeError(f"Update failed during {phase}; the active Python environment was not changed.")
    return python


class UpdateRecoveryError(RuntimeError):
    """Retain both environments and the recovery journal instead of forcing Git."""


def apply_update(app_dir: Path, branch: str = GITHUB_BRANCH, tag: str | None = None, pid: int | None = None,
                 *, expected_sha: str | None = None, repository: str = GITHUB_REPOSITORY) -> None:
    """Validate ancestry/SHA, stage dependencies, then commit source and runtime."""
    app_dir = app_dir.resolve()
    lock = _acquire_lock(app_dir)
    version_dir = None
    original = None
    target = None
    activated = False
    previous = None
    manifest = runtime_manifest(app_dir)
    journal = None
    record = {}
    retain_lock = False
    try:
        if pid:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and _process_alive(pid):
                time.sleep(0.25)
            if _process_alive(pid):
                raise RuntimeError("SpiceConex did not close within 30 seconds; update cancelled.")
        problem = active_installation_problem(app_dir) or _working_tree_problem(app_dir, branch)
        if problem:
            raise RuntimeError(problem)
        _verify_repository(app_dir, repository)
        if not tag or not re.fullmatch(r"v?[0-9][A-Za-z0-9.+-]*", tag) or _normalise_tag(tag) is None:
            raise RuntimeError("No valid published version tag was supplied for the update.")
        if not expected_sha or not re.fullmatch(r"[0-9a-fA-F]{40}", expected_sha):
            raise RuntimeError("The update requires the full SHA approved during the version check.")
        ref_check = _git(["check-ref-format", f"refs/heads/{branch}"], app_dir)
        if ref_check.returncode:
            raise RuntimeError("The configured update branch is invalid.")
        original = _commit(app_dir, "HEAD")
        tag_ref = f"refs/tags/{tag}"
        fetched = _git(["fetch", "--no-tags", "origin",
                        f"+refs/heads/{branch}:refs/remotes/origin/{branch}", f"{tag_ref}:{tag_ref}"], app_dir)
        if fetched.returncode:
            raise RuntimeError("Git fetch failed; the active installation was not changed.")
        target = _commit(app_dir, tag_ref)
        if target != expected_sha.lower():
            raise RuntimeError("The fetched tag SHA differs from the approved GitHub SHA. Update cancelled.")
        for ancestor, descendant, error in (
            (target, f"refs/remotes/origin/{branch}", "The release is not reachable from the configured update branch."),
            (original, target, "The release is not a fast-forward from the installed revision."),
        ):
            if _git(["merge-base", "--is-ancestor", ancestor, descendant], app_dir).returncode:
                raise RuntimeError(error)
        if target == original:
            return
        old_python = _venv_python(app_dir)
        previous = json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else None
        versions = manifest.parent / "versions"
        versions.mkdir(parents=True, exist_ok=True)
        version_dir = versions / uuid.uuid4().hex
        version_dir.mkdir()
        journal = version_dir / "transaction.json"
        record = {"app_dir": str(app_dir), "previous_sha": original, "target_sha": target,
                  "previous_python": str(old_python), "phase": "preparing"}
        _atomic_json(journal, record)
        python = _prepare_environment(app_dir, target, version_dir)
        record.update(phase="prepared", python=str(python)); _atomic_json(journal, record)
        # Preparation can take minutes. Never merge over concurrent edits or a
        # branch/HEAD that changed while dependencies were being installed.
        problem = _working_tree_problem(app_dir, branch)
        if problem or _commit(app_dir, "HEAD") != original:
            raise RuntimeError(problem or "HEAD changed during dependency preparation; update cancelled.")
        merged = _git(["merge", "--ff-only", target], app_dir)
        if merged.returncode or _commit(app_dir, "HEAD") != target:
            raise RuntimeError("Git fast-forward failed; preparing recovery to the previous revision.")
        record["phase"] = "source_updated"; _atomic_json(journal, record)
        _atomic_json(manifest, {"schema": 1, "app_dir": str(app_dir), "python": str(python),
                               "sha": target, "previous_sha": original, "previous_python": str(old_python)})
        activated = True
        record["phase"] = "committed"; _atomic_json(journal, record)
    except Exception as exc:
        try:
            if activated:
                if previous is None:
                    manifest.unlink(missing_ok=True)
                else:
                    _atomic_json(manifest, previous)
            if original and target and _commit(app_dir, "HEAD") != original:
                if _commit(app_dir, "HEAD") != target:
                    raise RuntimeError("HEAD changed outside the update transaction")
                # --keep refuses to overwrite intervening local changes.
                restored = _git(["reset", "--keep", original], app_dir)
                if restored.returncode:
                    raise RuntimeError("Git refused to restore over local changes")
            if journal:
                record["phase"] = "rolled_back"; _atomic_json(journal, record)
        except Exception as recovery:
            retain_lock = True
            if journal:
                record["phase"] = "recovery_required"
                try:
                    _atomic_json(journal, record)
                except OSError:
                    pass
            raise UpdateRecoveryError(f"Update recovery requires review. Both environments were retained. Journal: {journal}") from recovery
        raise exc
    finally:
        if not retain_lock:
            lock.unlink(missing_ok=True)


def launch_update_process(app_dir: Path, pid: int, tag: str, expected_sha: str | None = None) -> subprocess.Popen:
    """Copy the coordinator outside the checkout, then start it detached."""
    if not expected_sha or not re.fullmatch(r"[0-9a-fA-F]{40}", expected_sha):
        raise RuntimeError("No verified update SHA was supplied.")
    staging = Path(tempfile.mkdtemp(prefix="spiceconex-updater-"))
    coordinator = staging / "updater.py"
    shutil.copy2(Path(__file__), coordinator)
    for name in ("runtime.py", "installation.py"):
        shutil.copy2(Path(__file__).with_name(name), staging / name)
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    return subprocess.Popen([str(_venv_python(app_dir)), str(coordinator), "--apply", str(app_dir),
                             GITHUB_BRANCH, tag, str(pid), GITHUB_REPOSITORY, expected_sha],
                            cwd=staging, close_fds=True, **options)


def _restart(app_dir: Path) -> None:
    subprocess.Popen([str(_venv_python(app_dir)), str(app_dir / "ssm_spiceconex.py")], cwd=app_dir, close_fds=True)


def _main() -> int:
    if len(sys.argv) != 8 or sys.argv[1] != "--apply":
        return 2
    app_dir, branch, tag, pid = Path(sys.argv[2]).resolve(), sys.argv[3], sys.argv[4], int(sys.argv[5])
    try:
        apply_update(app_dir, branch, tag, pid, repository=sys.argv[6], expected_sha=sys.argv[7])
        _restart(app_dir)
    except Exception as exc:
        (app_dir / "update-error.log").write_text(f"SpiceConex update failed: {exc}\n", encoding="utf-8")
        if not isinstance(exc, UpdateRecoveryError):
            _restart(app_dir)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
