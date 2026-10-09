from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import Request, urlopen

from app.config import GITHUB_BRANCH, GITHUB_REPOSITORY, APP_VERSION


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=False)


def is_git_worktree(app_dir: Path) -> bool:
    return (app_dir / ".git").exists() or _git(["rev-parse", "--is-inside-work-tree"], app_dir).returncode == 0


def current_commit(app_dir: Path) -> str:
    result = _git(["rev-parse", "HEAD"], app_dir)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Unable to determine current Git commit.")
    return result.stdout.strip()


def remote_url(app_dir: Path) -> str:
    result = _git(["remote", "get-url", "origin"], app_dir)
    return result.stdout.strip() if result.returncode == 0 else ""


def github_api_commit() -> dict:
    if not GITHUB_REPOSITORY or GITHUB_REPOSITORY.startswith("YOUR_"):
        raise RuntimeError("GitHub repository is not configured yet. Set GITHUB_REPOSITORY in app/config.py before enabling updates.")
    url = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/commits/{GITHUB_BRANCH}"
    request = Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "SSM-SpiceConex"})
    with urlopen(request, timeout=10) as response:
        return json.load(response)


def check_for_update(app_dir: Path) -> dict:
    if not shutil.which("git"):
        raise RuntimeError("Git is not installed. Run the SpiceConex setup script again to install it.")
    if not is_git_worktree(app_dir):
        raise RuntimeError("This SpiceConex installation is not a Git working tree. Install SpiceConex from a Git clone to enable in-app updates.")

    remote = remote_url(app_dir)
    if not remote:
        raise RuntimeError("The local Git repository has no 'origin' remote configured.")

    commit = github_api_commit()
    remote_sha = commit.get("sha", "")
    local_sha = current_commit(app_dir)
    return {
        "available": bool(remote_sha and remote_sha != local_sha),
        "local_sha": local_sha,
        "remote_sha": remote_sha,
        "remote_url": remote,
        "message": (commit.get("commit") or {}).get("message", "").splitlines()[0],
        "author": ((commit.get("commit") or {}).get("author") or {}).get("name", ""),
        "version": APP_VERSION,
    }


def _process_alive(pid: int) -> bool:
    if os.name == "nt":
        result = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True)
        return str(pid) in result.stdout
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def apply_update(app_dir: Path, pid: int | None = None) -> None:
    if pid:
        deadline = time.time() + 30
        while time.time() < deadline and _process_alive(pid):
            time.sleep(0.5)

    fetch = _git(["fetch", "origin", GITHUB_BRANCH], app_dir)
    if fetch.returncode != 0:
        raise RuntimeError(fetch.stderr.strip() or "Git fetch failed.")

    status = _git(["status", "--porcelain"], app_dir)
    if status.returncode != 0:
        raise RuntimeError(status.stderr.strip() or "Unable to inspect Git working tree.")
    if status.stdout.strip():
        raise RuntimeError("Local changes were detected. SpiceConex will not overwrite them automatically.")

    pull = _git(["pull", "--ff-only", "origin", GITHUB_BRANCH], app_dir)
    if pull.returncode != 0:
        raise RuntimeError(pull.stderr.strip() or "Git fast-forward update failed.")

    venv = app_dir / ".venv"
    if os.name == "nt":
        venv = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "SSM-SpiceConex" / "venv"
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.exists():
        python = Path(sys.executable)
    install = subprocess.run([str(python), "-m", "pip", "install", "-r", str(app_dir / "requirements.txt"), "--disable-pip-version-check", "--no-input"], cwd=app_dir, text=True, capture_output=True)
    if install.returncode != 0:
        raise RuntimeError(install.stderr.strip() or "Python dependency update failed.")


def _main() -> int:
    if len(sys.argv) < 3 or sys.argv[1] != "--apply":
        return 2
    app_dir = Path(sys.argv[2]).resolve()
    pid = int(sys.argv[3]) if len(sys.argv) > 3 else None
    try:
        apply_update(app_dir, pid)
    except Exception as exc:
        log = app_dir / "update-error.log"
        log.write_text(f"SpiceConex update failed: {exc}\n", encoding="utf-8")
        return 1
    launcher = app_dir / ("spiceconex.cmd" if os.name == "nt" else "spiceconex")
    if launcher.exists():
        subprocess.Popen([str(launcher)], cwd=app_dir)
    else:
        subprocess.Popen([sys.executable, str(app_dir / "ssm_spiceconex.py")], cwd=app_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
