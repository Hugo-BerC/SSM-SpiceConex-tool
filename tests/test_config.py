from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app.config import AWS_REGIONS, DEFAULT_REGION, _aws_timeout


class ConfigurationTests(unittest.TestCase):
    def test_terminal_region_catalog_is_the_approved_european_scope(self):
        self.assertEqual(("eu-west-1", "eu-south-2", "eu-central-1"), AWS_REGIONS)
        self.assertEqual("eu-west-1", DEFAULT_REGION)

    def test_invalid_timeout_uses_safe_default_without_disclosing_the_value(self):
        for value in ("invalid-private-value", "0", "-1"):
            with self.subTest(value=value), patch.dict(os.environ, {"SSM_SPICECONEX_AWS_TIMEOUT": value}):
                with self.assertWarns(RuntimeWarning) as warning:
                    self.assertEqual(20, _aws_timeout())
                self.assertEqual("Invalid SSM_SPICECONEX_AWS_TIMEOUT; using 20 seconds.", str(warning.warning))

    def test_valid_timeout_is_preserved(self):
        with patch.dict(os.environ, {"SSM_SPICECONEX_AWS_TIMEOUT": "45"}):
            self.assertEqual(45, _aws_timeout())
