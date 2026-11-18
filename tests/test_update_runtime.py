from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app import runtime


class RuntimeTests(unittest.TestCase):
    def test_missing_manifest_keeps_original_interpreter(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(runtime, "runtime_dir", return_value=Path(directory)):
            self.assertEqual(Path("original"), runtime.selected_python(Path(directory), Path("original")))

    def test_registered_environment_is_selected_before_ui_import(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(runtime, "runtime_dir", return_value=Path(directory)):
            root = Path(directory); python = root / "new-python"; python.write_text("interpreter", encoding="utf-8")
            manifest = runtime.runtime_manifest(root); manifest.parent.mkdir(parents=True)
            manifest.write_text(json.dumps({"app_dir": str(root), "python": str(python)}), encoding="utf-8")
            self.assertEqual(python, runtime.selected_python(root, Path("original")))
            with patch.object(runtime.os, "execv") as execute:
                runtime.use_active_runtime(root)
            self.assertEqual(str(python), execute.call_args.args[0])

    def test_missing_staged_interpreter_is_reported_instead_of_silent_fallback(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(runtime, "runtime_dir", return_value=Path(directory)):
            root = Path(directory); manifest = runtime.runtime_manifest(root); manifest.parent.mkdir(parents=True)
            manifest.write_text(json.dumps({"app_dir": str(root), "python": str(root / "missing")}), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "runtime manifest is invalid"):
                runtime.selected_python(root, Path("original"))

    def test_launch_is_blocked_while_update_transaction_is_active(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / ".spiceconex-update.lock").write_text("123", encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "update is in progress"):
                runtime.use_active_runtime(root)

    def test_distinct_venv_paths_are_not_collapsed_through_python_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); target = root / "new" / "bin" / "python"
            with patch.object(runtime, "selected_python", return_value=target), \
                 patch.object(runtime.sys, "executable", str(root / "old" / "bin" / "python")), \
                 patch.object(Path, "resolve", return_value=root / "base-python"), \
                 patch.object(runtime.os, "execv") as execute:
                runtime.use_active_runtime(root)
            execute.assert_called_once()
