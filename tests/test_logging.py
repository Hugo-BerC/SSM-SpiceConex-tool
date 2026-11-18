from __future__ import annotations

import io
import logging
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from utils import logging as app_logs
from utils.redaction import redact_sensitive


class RedactionTests(unittest.TestCase):
    def test_credentials_urls_headers_and_cli_parameters_are_redacted(self):
        cases = [
            ('aws_access_key_id=AKIAABCDEFGHIJKLMNOP', 'AKIAABCDEFGHIJKLMNOP'),
            ('{"SecretAccessKey":"synthetic-secret"}', 'synthetic-secret'),
            ("{'SessionToken': 'synthetic-session'}", 'synthetic-session'),
            ('Authorization: Bearer synthetic-bearer', 'synthetic-bearer'),
            ('--password "synthetic password"', 'synthetic password'),
            ('https://example.test/authorize?code_challenge=synthetic-challenge&state=synthetic-state', 'synthetic-state'),
            ('https://user:synthetic-password@example.test/path', 'synthetic-password'),
            ('https://example.test/path?X-Amz-Signature=synthetic-signature', 'synthetic-signature'),
            ('password="prefix\\"synthetic-tail"', 'synthetic-tail'),
        ]
        for text, secret in cases:
            with self.subTest(text=text):
                output = redact_sensitive(text)
                self.assertNotIn(secret, output)
                self.assertEqual(output, redact_sensitive(output))

    def test_formatted_arguments_and_tracebacks_keep_diagnostics_without_tokens(self):
        output = io.StringIO()
        logger = logging.Logger("redaction-test")
        handler = logging.StreamHandler(output); handler.setFormatter(app_logs.RedactingFormatter("%(message)s"))
        logger.addHandler(handler)
        logger.info("operation=%s secret_access_key=%s", "DescribeInstances", "synthetic-secret")
        try:
            raise RuntimeError("access_token=synthetic-token")
        except RuntimeError:
            logger.exception("Discovery failed | task=123")
        text = output.getvalue()
        self.assertIn("DescribeInstances", text); self.assertIn("RuntimeError", text); self.assertIn("task=123", text)
        self.assertNotIn("synthetic-secret", text); self.assertNotIn("synthetic-token", text)


class LogLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.logger = logging.getLogger(app_logs.LOGGER_NAME)
        self.handlers = list(self.logger.handlers); self.logger.handlers = []

    def tearDown(self):
        for handler in list(self.logger.handlers):
            handler.close(); self.logger.removeHandler(handler)
        self.logger.handlers = self.handlers

    def test_startup_preserves_existing_log_history(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / app_logs.LOG_FILENAME
            path.write_text("previous session diagnostic\n", encoding="utf-8")
            with patch.object(app_logs, "_resolve_log_dir", return_value=Path(directory)):
                logger = app_logs.reset_logging_session(); logger.info("new session diagnostic")
                for handler in logger.handlers: handler.flush()
            text = path.read_text(encoding="utf-8")
            self.assertIn("previous session diagnostic", text); self.assertIn("new session diagnostic", text)
            self.assertIn("Application session started", text)
            for handler in self.logger.handlers: handler.close()

    def test_unwritable_log_file_falls_back_to_redacted_stderr(self):
        output = io.StringIO()
        with patch.object(app_logs, "_resolve_log_dir", return_value=Path("unwritable")), \
             patch.object(app_logs, "RotatingFileHandler", side_effect=PermissionError("denied")), \
             patch("sys.stderr", output):
            logger = app_logs.configure_logging(); logger.error("password=synthetic-password")
        self.assertNotIn("synthetic-password", output.getvalue())
        self.assertIn("[REDACTED]", output.getvalue())
