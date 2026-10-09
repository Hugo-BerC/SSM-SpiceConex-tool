from __future__ import annotations

import unittest

from app.config import AWS_REGIONS, DEFAULT_REGION


class ConfigurationTests(unittest.TestCase):
    def test_terminal_region_catalog_is_the_approved_european_scope(self):
        self.assertEqual(("eu-west-1", "eu-south-2", "eu-central-1"), AWS_REGIONS)
        self.assertEqual("eu-west-1", DEFAULT_REGION)
