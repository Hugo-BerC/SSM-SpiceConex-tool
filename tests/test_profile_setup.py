from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from aws.profile_setup import ProfileSetupRequest, bootstrap_sso_profiles, sanitize_profile_name


class ProfileSetupTests(unittest.TestCase):
    def request(self):
        return ProfileSetupRequest(
            region="eu-west-1",
            sso_start_url="https://example.awsapps.com/start",
            sso_region="eu-west-1",
            sso_role_name="AWSAdministratorAccess",
            access_key_id="AKIAEXAMPLE",
            secret_access_key="secret",
        )

    def test_profile_names_are_safe_for_aws_config(self):
        self.assertEqual("Platform-Engineering", sanitize_profile_name("Platform / Engineering", "123"))
        self.assertEqual("123", sanitize_profile_name("///", "123"))

    def test_generates_profiles_and_backs_up_existing_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config"
            credentials = root / "credentials"
            config.write_text("old config", encoding="utf-8")
            credentials.write_text("old credentials", encoding="utf-8")
            sts = Mock()
            sts.get_caller_identity.return_value = {"Account": "111111111111", "Arn": "arn:aws:iam::111111111111:user/admin"}
            organizations = Mock()
            organizations.get_paginator.return_value.paginate.return_value = [{"Accounts": [
                {"Id": "111111111111", "Name": "Platform / Engineering"},
                {"Id": "222222222222", "Name": "Security"},
            ]}]
            session = Mock()
            session.client.side_effect = [sts, organizations]
            with patch("aws.profile_setup.boto3.Session", return_value=session), patch("aws.profile_setup.aws_paths", return_value=(config, credentials)):
                result = bootstrap_sso_profiles(self.request())

            self.assertEqual(["Platform-Engineering", "Security"], result["profiles"])
            self.assertEqual(2, len(result["backups"]))
            self.assertIn("[profile Platform-Engineering]", config.read_text(encoding="utf-8"))
            self.assertIn("sso_account_id = 222222222222", config.read_text(encoding="utf-8"))
            self.assertIn("aws_secret_access_key = secret", credentials.read_text(encoding="utf-8"))

    def test_temporary_credentials_require_a_session_token(self):
        request = ProfileSetupRequest(**{**self.request().__dict__, "access_key_id": "ASIAEXAMPLE"})
        with self.assertRaisesRegex(ValueError, "Session Token"):
            bootstrap_sso_profiles(request)
