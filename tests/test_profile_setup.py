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

    def test_generates_profiles_without_replacing_existing_aws_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config"
            credentials = root / "credentials"
            config.write_text("[profile personal]\nregion = us-east-1\n", encoding="utf-8")
            credentials.write_text("[personal]\naws_access_key_id = old-key\n", encoding="utf-8")
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
            self.assertEqual(1, len(result["backups"]))
            generated = config.read_text(encoding="utf-8")
            self.assertIn("[profile personal]", generated)
            self.assertIn("region = us-east-1", generated)
            self.assertIn("[sso-session spiceconex-", generated)
            self.assertIn("[profile Platform-Engineering]", generated)
            self.assertIn("sso_account_id = 222222222222", generated)
            self.assertNotIn("aws_secret_access_key", generated)
            self.assertEqual("[personal]\naws_access_key_id = old-key\n", credentials.read_text(encoding="utf-8"))

    def test_preserves_a_profile_with_the_same_name_as_an_account(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config"
            credentials = root / "credentials"
            config.write_text("[profile Security]\nregion = eu-central-1\n", encoding="utf-8")
            sts = Mock()
            sts.get_caller_identity.return_value = {"Account": "111111111111", "Arn": "arn:aws:iam::111111111111:user/admin"}
            organizations = Mock()
            organizations.get_paginator.return_value.paginate.return_value = [{"Accounts": [{"Id": "222222222222", "Name": "Security"}]}]
            session = Mock()
            session.client.side_effect = [sts, organizations]
            with patch("aws.profile_setup.boto3.Session", return_value=session), patch("aws.profile_setup.aws_paths", return_value=(config, credentials)):
                result = bootstrap_sso_profiles(self.request())

            self.assertEqual(["Security-222222222222"], result["profiles"])
            generated = config.read_text(encoding="utf-8")
            self.assertIn("[profile Security]\nregion = eu-central-1", generated)
            self.assertIn("[profile Security-222222222222]", generated)

    def test_temporary_credentials_require_a_session_token(self):
        request = ProfileSetupRequest(**{**self.request().__dict__, "access_key_id": "ASIAEXAMPLE"})
        with self.assertRaisesRegex(ValueError, "Session Token"):
            bootstrap_sso_profiles(request)
