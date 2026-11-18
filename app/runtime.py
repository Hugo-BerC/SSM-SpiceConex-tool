"""Select an update's validated Python environment before importing the UI."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys

try:
    from app.installation import runtime_dir
except ModuleNotFoundError:
    from installation import runtime_dir  # detached updater copy


def runtime_manifest(app_dir: Path) -> Path:
    identity = os.path.normcase(str(app_dir.resolve())).encode("utf-8")
    return runtime_dir() / "runtimes" / hashlib.sha256(identity).hexdigest()[:20] / "active.json"


def selected_python(app_dir: Path, fallback: Path) -> Path:
    manifest = runtime_manifest(app_dir)
    if not manifest.exists():
        return fallback
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        if Path(data["app_dir"]).resolve() != app_dir.resolve():
            raise ValueError("Installation mismatch")
        python = Path(data["python"])
        if not python.is_absolute() or not python.is_file():
            raise ValueError("Python environment is unavailable")
        return python
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RuntimeError(f"The update runtime manifest is invalid: {manifest}. Restore the source/interpreter pair using its recovery journal.") from exc


def use_active_runtime(app_dir: Path) -> None:
    if (app_dir / ".spiceconex-update.lock").exists():
        raise SystemExit("SpiceConex update is in progress. Wait for completion; after an interrupted update, review its recovery journal.")
    target = selected_python(app_dir, Path(sys.executable))
    # Unix venv executables can be symlinks to the same base Python while
    # selecting different site-packages. Compare invocation paths, not realpaths.
    if os.path.normcase(os.path.abspath(target)) != os.path.normcase(os.path.abspath(sys.executable)):
        os.execv(str(target), [str(target), *sys.argv])
