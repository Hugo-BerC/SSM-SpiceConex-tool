"""Run unittest offline with Qt and logs isolated from the workstation."""
from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pattern", default="test_*.py")
    args = parser.parse_args()
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["AWS_EC2_METADATA_DISABLED"] = "true"
    with tempfile.TemporaryDirectory(prefix="spiceconex-tests-") as directory:
        with patch.dict(os.environ, {"SSM_SPICECONEX_LOG_DIR": directory}), \
             patch.object(socket.socket, "connect", side_effect=AssertionError("Network disabled in tests")):
            import pyside6_poc.main as ui
            from PySide6.QtCore import QObject, Signal

            class OfflineIcons(QObject):
                changed = Signal()
                def path(self, _resource_type): return None
                def request(self, _resource_type): pass

            with patch.object(ui, "AwsIconProvider", OfflineIcons):
                result = unittest.TextTestRunner(verbosity=1).run(
                    unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern=args.pattern))
            from utils.logging import LOGGER_NAME
            logger = logging.getLogger(LOGGER_NAME)
            for handler in list(logger.handlers):
                handler.close(); logger.removeHandler(handler)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
