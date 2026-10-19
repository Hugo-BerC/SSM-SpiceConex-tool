from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import installation


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
