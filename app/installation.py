"""Resolve the single active SpiceConex installation on a workstation."""
from __future__ import annotations

import json
import os
import platform
import struct
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def runtime_dir() -> Path:
    """Return the per-user location shared by installer and updater metadata."""
    if platform.system() == "Windows":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    elif platform.system() == "Darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / "SSM-SpiceConex"


def active_installation_file() -> Path:
    return runtime_dir() / "active-installation.json"


def active_installation_dir() -> Path | None:
    """Return the registered installation directory, ignoring stale metadata."""
    try:
        payload = json.loads(active_installation_file().read_text(encoding="utf-8-sig"))
        raw_directory = payload.get("app_dir", "")
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(raw_directory, str) or not raw_directory.strip():
        return None
    directory = Path(raw_directory).expanduser().resolve()
    return directory if directory.is_dir() else None


def active_installation_problem(app_dir: Path) -> str | None:
    """Explain why a non-canonical checkout must not update itself."""
    active = active_installation_dir()
    current = app_dir.expanduser().resolve()
    if active is None or active == current:
        return None
    return (
        "This SpiceConex copy is not the active installation. "
        f"The active installation is {active}. Start SpiceConex using the installed command, "
        "or run setup from this checkout to make it the active installation."
    )


def write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".spiceconex-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def check_python_runtime() -> None:
    if sys.version_info < (3, 10) or struct.calcsize("P") != 8:
        raise RuntimeError("SpiceConex requires 64-bit Python 3.10 or newer.")


def register_installation(app_dir: Path, python: Path, launcher: Path) -> None:
    """Commit setup metadata only after dependency checks and launcher creation."""
    from app.runtime import runtime_manifest
    app_dir = app_dir.resolve()
    # Preserve the invocation path of a Unix venv python symlink.
    python, launcher = (Path(os.path.abspath(path)) for path in (python, launcher))
    if (app_dir / ".spiceconex-update.lock").exists():
        raise RuntimeError("An update/recovery lock exists. Review its journal before running setup.")
    if not (app_dir / "ssm_spiceconex.py").is_file() or not python.is_file() or not launcher.is_file():
        raise RuntimeError("Setup did not produce a complete installation and launcher.")
    # Re-running setup must replace a previous updater runtime selection too.
    selection = runtime_manifest(app_dir)
    previous = json.loads(selection.read_text(encoding="utf-8-sig")) if selection.exists() else None
    write_json_atomic(selection, {"schema": 1, "app_dir": str(app_dir), "python": str(python), "source": "setup"})
    try:
        write_json_atomic(active_installation_file(), {
            "schema": 1, "app_dir": str(app_dir), "python": str(python), "launcher": str(launcher),
            "installed_at_utc": datetime.now(timezone.utc).isoformat(),
        })
    except OSError:
        if previous is None:
            selection.unlink(missing_ok=True)
        else:
            write_json_atomic(selection, previous)
        raise


def _main():
    import argparse
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", nargs=1, metavar="APP_DIR")
    group.add_argument("--register", nargs=3, metavar=("APP_DIR", "PYTHON", "LAUNCHER"))
    args = parser.parse_args()
    check_python_runtime()
    if args.check:
        import importlib
        for module in ("boto3", "botocore", "numpy", "pandas", "PIL", "PySide6", "packaging", "pyside6_poc.main"):
            importlib.import_module(module)
    elif args.register:
        register_installation(*(Path(path) for path in args.register))
    else:
        parser.error("Use --check APP_DIR or --register APP_DIR PYTHON LAUNCHER")


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    _main()
