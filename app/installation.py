"""Resolve the single active SpiceConex installation on a workstation."""
from __future__ import annotations

import json
import os
import platform
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
        payload = json.loads(active_installation_file().read_text(encoding="utf-8"))
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
