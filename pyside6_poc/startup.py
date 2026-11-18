"""Application startup independent of optional visual assets."""
from __future__ import annotations

import argparse
from pathlib import Path

from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from pyside6_poc.dialogs import show_dialog
from utils.logging import get_logger, log_path, reset_logging_session
from utils.redaction import redact_sensitive


def run(window_factory, style: str, logo: Path, app_dir: Path, *, argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--demo", action="store_true")
    args = parser.parse_args(argv)
    reset_logging_session()
    logger = get_logger("startup")
    logger.info("Application starting | log_file=%s", log_path())
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion"); app.setStyleSheet(style); app.setFont(QFont("Segoe UI", 10))
    if logo.exists():
        app.setWindowIcon(QIcon(str(logo)))
    else:
        logger.info("Optional logo is unavailable; continuing startup")
    window = window_factory(args.demo)
    window.show()
    error_file = app_dir / "update-error.log"
    if error_file.exists():
        try:
            details = redact_sensitive(error_file.read_text(encoding="utf-8", errors="replace").strip())
            error_file.unlink()
        except OSError:
            logger.warning("Unable to read/remove update diagnostics")
        else:
            show_dialog(window, QMessageBox.Icon.Warning, "SpiceConex update failed",
                        "SpiceConex restarted after an update failure. Review the details before retrying.", details)
    return app.exec()
