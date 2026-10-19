from __future__ import annotations

import os
import tempfile
import unittest
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
