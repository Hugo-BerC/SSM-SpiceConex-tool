from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import installation
from app import runtime


class InstallationTests(unittest.TestCase):
    def test_active_installation_is_read_from_the_runtime_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_dir = root / "active"
            app_dir.mkdir()
            runtime = root / "runtime"
            runtime.mkdir()
            (runtime / "active-installation.json").write_text(
                json.dumps({"app_dir": str(app_dir)}), encoding="utf-8"
            )
            with patch.object(installation, "runtime_dir", return_value=runtime):
                self.assertEqual(app_dir.resolve(), installation.active_installation_dir())
                self.assertIsNone(installation.active_installation_problem(app_dir))

    def test_non_active_checkout_is_explained(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            active = root / "active"
            stale = root / "stale"
            active.mkdir()
            stale.mkdir()
            runtime = root / "runtime"
            runtime.mkdir()
            (runtime / "active-installation.json").write_text(
                json.dumps({"app_dir": str(active)}), encoding="utf-8"
            )
            with patch.object(installation, "runtime_dir", return_value=runtime):
                message = installation.active_installation_problem(stale)
        self.assertIn(str(active.resolve()), message)
        self.assertIn("not the active installation", message)

    def test_setup_registers_platform_path_and_replaces_updater_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); app_dir = root / "source"; app_dir.mkdir()
            (app_dir / "ssm_spiceconex.py").write_text("", encoding="utf-8")
            python = root / "venv" / "python"; python.parent.mkdir(); python.write_text("", encoding="utf-8")
            launcher = root / "launcher"; launcher.write_text("", encoding="utf-8")
            with patch.object(installation, "runtime_dir", return_value=root / "runtime"), \
                 patch.object(runtime, "runtime_dir", return_value=root / "runtime"):
                selection = runtime.runtime_manifest(app_dir)
                installation.write_json_atomic(selection, {"python": "old-updater-interpreter"})
                installation.register_installation(app_dir, python, launcher)
                self.assertEqual(app_dir.resolve(), installation.active_installation_dir())
                self.assertEqual(python, runtime.selected_python(app_dir, Path("fallback")))

    def test_windows_bom_manifest_is_understood(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "active-installation.json"
            manifest.write_text(json.dumps({"app_dir": str(root)}), encoding="utf-8-sig")
            with patch.object(installation, "active_installation_file", return_value=manifest):
                self.assertEqual(root.resolve(), installation.active_installation_dir())

    def test_failed_registration_restores_previous_runtime_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_dir = root / "source"
            app_dir.mkdir()
            (app_dir / "ssm_spiceconex.py").touch()
            python, launcher = root / "python", root / "launcher"
            python.touch()
            launcher.touch()
            with patch.object(installation, "runtime_dir", return_value=root / "runtime"), \
                 patch.object(runtime, "runtime_dir", return_value=root / "runtime"):
                selection = runtime.runtime_manifest(app_dir)
                previous = {"schema": 1, "python": "previous-interpreter"}
                installation.write_json_atomic(selection, previous)
                original_write = installation.write_json_atomic

                def fail_registration(path, payload):
                    if path == installation.active_installation_file():
                        raise PermissionError("registration denied")
                    original_write(path, payload)

                with patch.object(installation, "write_json_atomic", side_effect=fail_registration):
                    with self.assertRaises(PermissionError):
                        installation.register_installation(app_dir, python, launcher)
                self.assertEqual(previous, json.loads(selection.read_text(encoding="utf-8")))
                self.assertFalse(installation.active_installation_file().exists())

    def test_setup_registration_refuses_an_update_recovery_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".spiceconex-update.lock").touch()
            with patch.object(installation, "write_json_atomic") as write:
                with self.assertRaisesRegex(RuntimeError, "update/recovery lock"):
                    installation.register_installation(root, root / "python", root / "launcher")
                write.assert_not_called()

    def test_runtime_paths_follow_platform_conventions(self):
        with patch.object(installation.Path, "home", return_value=Path("/user")):
            for system, env, expected in (
                ("Windows", {"LOCALAPPDATA": "/local"}, "/local/SSM-SpiceConex"),
                ("Darwin", {}, "/user/Library/Application Support/SSM-SpiceConex"),
                ("Linux", {"XDG_CONFIG_HOME": "/xdg"}, "/xdg/SSM-SpiceConex"),
            ):
                with self.subTest(system=system), patch.object(installation.platform, "system", return_value=system), \
                     patch.dict(installation.os.environ, env, clear=True):
                    self.assertEqual(Path(expected), installation.runtime_dir())

    def test_unsupported_python_is_rejected_before_ui_import(self):
        with patch.object(installation.sys, "version_info", (3, 9)):
            with self.assertRaisesRegex(RuntimeError, "Python 3.10"):
                installation.check_python_runtime()
        with patch.object(installation.struct, "calcsize", return_value=4):
            with self.assertRaisesRegex(RuntimeError, "64-bit"):
                installation.check_python_runtime()
