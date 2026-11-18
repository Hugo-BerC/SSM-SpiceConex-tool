from __future__ import annotations

import os
import tempfile
import unittest
import subprocess
from pathlib import Path
from unittest.mock import patch

from aws.profiles import discover_profiles, read_aws_config


class AwsProfileDiscoveryTests(unittest.TestCase):
    def test_config_reader_honours_aws_config_file_override(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "team-config"
            config.write_text("[profile engineering]\nregion = eu-west-1\n", encoding="utf-8")
            with patch.dict(os.environ, {"AWS_CONFIG_FILE": str(config)}, clear=False):
                path, parser, error = read_aws_config()

        self.assertIsNone(error)
        self.assertEqual(str(config), path)
        self.assertTrue(parser.has_section("profile engineering"))

    @patch("aws.profiles.run_aws_cli")
    def test_discovery_merges_file_profiles_and_cli_profiles(self, run_cli):
        run_cli.return_value.returncode = 0
        run_cli.return_value.stdout = "cli-profile\n"
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config"
            credentials = Path(directory) / "credentials"
            config.write_text("[profile config-profile]\nregion = eu-west-1\n", encoding="utf-8")
            credentials.write_text("[credentials-profile]\n", encoding="utf-8")
            with patch.dict(os.environ, {"AWS_CONFIG_FILE": str(config), "AWS_SHARED_CREDENTIALS_FILE": str(credentials)}, clear=False):
                profiles = discover_profiles()

        self.assertEqual(["cli-profile", "config-profile", "credentials-profile"], profiles)

    def test_sso_sessions_and_service_sections_are_not_profiles(self):
        with tempfile.TemporaryDirectory() as directory:
            config, credentials = Path(directory) / "config", Path(directory) / "credentials"
            config.write_text("[default]\nregion=eu-west-1\n[sso-session corp]\nsso_region=eu-west-1\n"
                              "[services endpoints]\n[plugins]\n[profile real]\n[profile ]\n", encoding="utf-8")
            credentials.write_text("[credentials-only]\n[profile literal-name]\n", encoding="utf-8")
            with patch.dict(os.environ, {"AWS_CONFIG_FILE": str(config), "AWS_SHARED_CREDENTIALS_FILE": str(credentials)}), \
                 patch("aws.profiles.run_aws_cli", side_effect=RuntimeError("CLI missing")):
                self.assertEqual(["credentials-only", "default", "profile literal-name", "real"], discover_profiles())

    def test_utf8_percent_values_and_cli_timeout_do_not_hide_profiles(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config"
            path.write_text("[profile equipo-ñ]\nsso_start_url=https://example.test/start?value=%20\n", encoding="utf-8-sig")
            with patch.dict(os.environ, {"AWS_CONFIG_FILE": str(path), "AWS_SHARED_CREDENTIALS_FILE": str(path.parent / "absent")}), \
                 patch("aws.profiles.run_aws_cli", side_effect=subprocess.TimeoutExpired("aws", 1)):
                self.assertEqual(["equipo-ñ"], discover_profiles())
