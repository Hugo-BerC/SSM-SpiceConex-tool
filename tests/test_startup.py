from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication
import pyside6_poc.main as ui
from pyside6_poc import startup


class StartupTests(unittest.TestCase):
    def test_missing_logo_still_creates_shows_and_runs_window(self):
        application = Mock(); application.exec.return_value = 0
        factory = Mock()
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(startup, "QApplication") as qt, patch.object(startup, "reset_logging_session"):
            qt.instance.return_value = application
            code = startup.run(factory, "", Path(directory) / "absent-logo.png", Path(directory), argv=["--demo"])
        self.assertEqual(0, code)
        factory.assert_called_once_with(True); factory.return_value.show.assert_called_once()
        application.exec.assert_called_once(); application.setWindowIcon.assert_not_called()

    def test_real_demo_window_can_be_built_without_optional_logos(self):
        application = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(ui, "LOGO_SIDEBAR", Path(directory) / "absent.png"), \
             patch.object(ui, "LOGO_MAIN", Path(directory) / "absent-main.png"):
            window = ui.MainWindow(demo=True)
            try:
                window.show(); application.processEvents()
                self.assertTrue(window.isVisible())
                self.assertGreater(window.stack.count(), 0)
                self.assertIsNotNone(window.performance)
            finally:
                window.close(); window.deleteLater(); application.processEvents()

    def test_legacy_import_names_still_point_to_extracted_components(self):
        from pyside6_poc.performance_page import PerformancePage
        from pyside6_poc.base_page import BasePage
        self.assertIs(ui.PerformancePage, PerformancePage)
        self.assertIs(ui.BasePage, BasePage)
