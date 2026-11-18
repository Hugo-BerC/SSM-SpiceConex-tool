from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from botocore.exceptions import (
    ClientError, CredentialRetrievalError, EndpointConnectionError, NoCredentialsError,
    SSOTokenLoadError, TokenRetrievalError, UnauthorizedSSOTokenError,
)

from aws.auth import is_session_unavailable, sso_login_profile
import pyside6_poc.main as ui


class AuthenticationTests(unittest.TestCase):
    def test_sdk_authentication_errors_are_recognized(self):
        errors = [NoCredentialsError(), UnauthorizedSSOTokenError(),
                  SSOTokenLoadError(error_msg="Token does not exist"),
                  TokenRetrievalError(provider="sso", error_msg="Token has expired and refresh failed"),
                  ClientError({"Error": {"Code": "ExpiredToken", "Message": "expired"}}, "DescribeInstances"),
                  ClientError({"Error": {"Code": "UnauthorizedException", "Message": "expired"}}, "GetRoleCredentials")]
        for error in errors:
            with self.subTest(error=type(error).__name__):
                self.assertTrue(is_session_unavailable(error))

    def test_permissions_network_and_configuration_errors_do_not_trigger_login(self):
        errors = [ClientError({"Error": {"Code": "AccessDenied", "Message": "SSO session expired"}}, "DescribeInstances"),
                  ClientError({"Error": {"Code": "ThrottlingException", "Message": "retry"}}, "DescribeInstances"),
                  EndpointConnectionError(endpoint_url="https://example.test"),
                  CredentialRetrievalError(provider="sso", error_msg="AccessDeniedException: forbidden"),
                  TokenRetrievalError(provider="sso", error_msg="Could not connect to the endpoint URL"),
                  RuntimeError("SSO session configuration is invalid"),
                  RuntimeError("Access denied to refresh an SSO session")]
        for error in errors:
            with self.subTest(error=str(error)):
                self.assertFalse(is_session_unavailable(error))

    def test_profile_detection_supports_modern_legacy_and_role_chains(self):
        profiles = {"modern": {"sso_session": "corp"}, "legacy": {"sso_start_url": "https://example.test/start"},
                    "role": {"role_arn": "arn:aws:iam::123456789012:role/Example", "source_profile": "modern"},
                    "static": {"region": "eu-west-1"}, "cycle-a": {"source_profile": "cycle-b"},
                    "cycle-b": {"source_profile": "cycle-a"}}
        with patch("aws.auth.load_config", return_value={"profiles": profiles}), \
             patch.dict(os.environ, {"AWS_CONFIG_FILE": "custom-config"}):
            self.assertEqual("modern", sso_login_profile("modern"))
            self.assertEqual("legacy", sso_login_profile("legacy"))
            self.assertEqual("modern", sso_login_profile("role"))
            self.assertIsNone(sso_login_profile("static"))
            self.assertIsNone(sso_login_profile("missing"))
            self.assertIsNone(sso_login_profile("cycle-a"))

    def test_profile_resolution_uses_sdk_config_format_and_path_override(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config"
            config.write_text("[sso-session corp]\nsso_start_url = https://example.test/start\nsso_region = eu-west-1\n"
                              "[profile login-source]\nsso_session = corp\n"
                              "[profile selected-role]\nsource_profile = login-source\nrole_arn = arn:aws:iam::123456789012:role/Example\n",
                              encoding="utf-8")
            with patch.dict(os.environ, {"AWS_CONFIG_FILE": str(config), "AWS_SHARED_CREDENTIALS_FILE": str(Path(directory) / "absent-credentials")}):
                self.assertEqual("login-source", sso_login_profile("selected-role"))

    def test_recovery_refreshes_sso_source_once_then_retries_selected_operation(self):
        expired = ClientError({"Error": {"Code": "ExpiredToken", "Message": "expired"}}, "DescribeInstances")
        operation = Mock(side_effect=[expired, "result"])
        with patch.object(ui, "sso_login_profile", return_value="sso-source"), \
             patch.object(ui, "login_profile") as login, patch.object(ui, "clear_session_cache") as clear:
            self.assertEqual("result", ui._run_with_sso("selected-role", operation))
        login.assert_called_once_with("sso-source")
        clear.assert_called_once()
        self.assertEqual(2, operation.call_count)

    def test_non_sso_profiles_and_permission_errors_never_start_login(self):
        for error, source in [(NoCredentialsError(), None),
                              (ClientError({"Error": {"Code": "AccessDenied", "Message": "denied"}}, "SendCommand"), "sso")]:
            with self.subTest(error=type(error).__name__), \
                 patch.object(ui, "sso_login_profile", return_value=source), patch.object(ui, "login_profile") as login:
                with self.assertRaises(type(error)):
                    ui._run_with_sso("selected", Mock(side_effect=error))
                login.assert_not_called()

    def test_persistent_authentication_failure_does_not_loop(self):
        operation = Mock(side_effect=NoCredentialsError())
        with patch.object(ui, "sso_login_profile", return_value="sso"), patch.object(ui, "login_profile") as login, \
             patch.object(ui, "clear_session_cache"):
            with self.assertRaises(NoCredentialsError):
                ui._run_with_sso("sso", operation)
        login.assert_called_once()
        self.assertEqual(2, operation.call_count)

    def test_rejected_login_does_not_retry_the_original_operation(self):
        operation = Mock(side_effect=NoCredentialsError())
        with patch.object(ui, "sso_login_profile", return_value="sso"), \
             patch.object(ui, "login_profile", side_effect=RuntimeError("Authorization denied")), \
             patch.object(ui, "clear_session_cache") as clear:
            with self.assertRaisesRegex(RuntimeError, "Authorization denied"):
                ui._run_with_sso("sso", operation)
        operation.assert_called_once()
        clear.assert_not_called()

    def test_permission_error_summary_does_not_claim_that_credentials_expired(self):
        error = ClientError({"Error": {"Code": "AccessDenied", "Message": "SSO session expired"}}, "DescribeInstances")
        summary, _details = ui._exception_summary("reading EC2", error)
        self.assertIn("denied", summary)

    def test_inventory_auth_errors_escape_but_permission_fallback_is_preserved(self):
        from aws.ec2 import _ssm_information_by_instance
        from services.discovery.engine import _safe_tags, _discover_global
        from services.discovery.aws_config import _enrich_configuration_items, discover_config_inventory
        from services.discovery.models import InfrastructureGraph
        expired = ClientError({"Error": {"Code": "ExpiredToken", "Message": "expired"}}, "DescribeResources")
        with self.assertRaises(ClientError):
            _safe_tags(Mock(side_effect=expired))
        self.assertEqual({}, _safe_tags(Mock(side_effect=ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "denied"}}, "DescribeTags"))))
        with patch("aws.ec2.get_ssm_client", side_effect=expired), self.assertRaises(ClientError):
            _ssm_information_by_instance("sso", "eu-west-1")
        client = Mock(); client.batch_get_resource_config.side_effect = expired
        with self.assertRaises(ClientError):
            _enrich_configuration_items(client, [{"resourceType": "AWS::EC2::VPC", "resourceId": "vpc-test"}])
        session = Mock(); session.client.side_effect = expired
        graph = InfrastructureGraph("123456789012", "sso", ["eu-west-1"])
        with self.assertRaises(ClientError):
            discover_config_inventory(session, graph, "eu-west-1", "123456789012", None)
        session.client.side_effect = None
        session.client.return_value.list_buckets.side_effect = expired
        with self.assertRaises(ClientError):
            _discover_global(session, graph, "123456789012", "application")

    def test_acm_detail_auth_failure_is_not_silently_skipped(self):
        from services.certificates import discover_certificate_inventory
        session = Mock(); sts = Mock(); acm = Mock()
        sts.get_caller_identity.return_value = {"Account": "123456789012"}
        session.client.side_effect = lambda service, **_kwargs: sts if service == "sts" else acm
        acm.get_paginator.return_value.paginate.return_value = [{"CertificateSummaryList": [{"CertificateArn": "arn-test"}]}]
        acm.describe_certificate.side_effect = ClientError(
            {"Error": {"Code": "ExpiredToken", "Message": "expired"}}, "DescribeCertificate")
        with patch("services.certificates.boto3.Session", return_value=session), self.assertRaises(ClientError):
            discover_certificate_inventory("sso", "eu-west-1")
