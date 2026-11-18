from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from botocore.exceptions import ClientError, EndpointConnectionError, ReadTimeoutError

from aws.errors import classify_error
from aws.session import client_config, get_client, get_ssm_client


class ClientPolicyTests(unittest.TestCase):
    def test_policy_has_explicit_total_attempts_and_bounded_timeouts(self):
        policy = client_config()
        self.assertEqual({"mode": "standard", "total_max_attempts": 3}, policy.retries)
        self.assertEqual(5, policy.connect_timeout)
        self.assertGreaterEqual(policy.read_timeout, 5)
        self.assertTrue(policy.tcp_keepalive)

    def test_all_inventory_config_helpers_share_the_policy(self):
        from services.certificates import _client_config as certificates
        from services.route53 import _client_config as route53
        from services.cloudfront import _client_config as cloudfront
        from services.discovery.engine import _config as discovery
        from aws.profile_setup import _client_config as profiles
        for helper in (certificates, route53, cloudfront, discovery, profiles):
            self.assertIs(client_config, helper)

    def test_existing_session_is_used_with_shared_config(self):
        session = Mock()
        get_client("cloudwatch", region="eu-west-1", session=session)
        options = session.client.call_args.kwargs
        self.assertEqual("eu-west-1", options["region_name"])
        self.assertEqual(3, options["config"].retries["total_max_attempts"])

    def test_ssm_transport_does_not_retry_ambiguous_commands(self):
        with patch("aws.session.get_boto3_session") as factory:
            get_ssm_client("test", "eu-west-1")
        self.assertEqual(1, factory.return_value.client.call_args.kwargs["config"].retries["total_max_attempts"])

    def test_error_categories_are_shared_and_not_inferred_from_misleading_api_messages(self):
        cases = [("ThrottlingException", "throttling"), ("AccessDenied", "permission"),
                 ("ExpiredToken", "authentication"), ("ValidationException", "aws")]
        for code, expected in cases:
            with self.subTest(code=code):
                error = ClientError({"Error": {"Code": code, "Message": "expired token access denied"}}, "DescribeInstances")
                self.assertEqual(expected, classify_error(error))
        self.assertEqual("network", classify_error(EndpointConnectionError(endpoint_url="https://example.invalid")))
        self.assertEqual("network", classify_error(ReadTimeoutError(endpoint_url="https://example.invalid")))

    def test_missing_tags_and_denied_tag_reads_are_distinguished(self):
        from services.discovery.engine import _safe_tags
        warnings = []
        for code in ("NoSuchTagSet", "AccessDenied"):
            error = ClientError({"Error": {"Code": code, "Message": "missing"}}, "GetBucketTagging")
            self.assertEqual({}, _safe_tags(Mock(side_effect=error), warnings))
        self.assertEqual(1, len(warnings))
        self.assertIn("permission", warnings[0])
