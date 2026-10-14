from __future__ import annotations

import unittest
from unittest.mock import patch

from pyside6_poc.main import _discover_certificates_for_profiles


class CertificateMultiAccountTests(unittest.TestCase):
    def test_all_profiles_are_consolidated_and_a_failure_is_isolated(self):
        def discover(profile, region):
            self.assertEqual("eu-west-1", region)
            if profile == "unavailable":
                raise RuntimeError("Access denied")
            return {
                "certificates": [{"Account": profile, "Domain": f"{profile}.example"}],
                "balancers": [{"Account": profile, "LoadBalancer": f"{profile}-lb"}],
            }

        with patch("pyside6_poc.main.discover_certificate_inventory", side_effect=discover), \
             patch("pyside6_poc.main._run_with_sso", side_effect=lambda _profile, operation: operation()):
            result = _discover_certificates_for_profiles(
                ["engineering", "unavailable", "engineering", "security"],
                "eu-west-1",
            )

        self.assertEqual(["engineering", "unavailable", "security"], result["profiles"])
        self.assertEqual(2, len(result["inventories"]))
        self.assertEqual(2, len(result["certificates"]))
        self.assertEqual(2, len(result["balancers"]))
        self.assertEqual("unavailable", result["failures"][0]["profile"])

    def test_no_profiles_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "No AWS profiles"):
            _discover_certificates_for_profiles([], "eu-west-1")
