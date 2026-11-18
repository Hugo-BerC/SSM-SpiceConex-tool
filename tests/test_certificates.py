from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import boto3
from botocore.stub import Stubber
from botocore.exceptions import ClientError, EndpointConnectionError

from services.certificates import CERTIFICATE_KEY_TYPES, CERTIFICATE_STATUSES, discover_certificate_inventory


class CertificateInventoryTests(unittest.TestCase):
    def test_rsa_and_ecdsa_algorithms_are_requested_across_pages(self):
        clients = {service: boto3.client(service, region_name="eu-west-1",
                                         aws_access_key_id="testing", aws_secret_access_key="testing")
                   for service in ("sts", "acm", "elbv2")}
        session = Mock()
        session.client.side_effect = lambda service, **_kwargs: clients[service]
        request = {"CertificateStatuses": list(CERTIFICATE_STATUSES),
                   "Includes": {"keyTypes": list(CERTIFICATE_KEY_TYPES)}}
        arns = [f"arn:aws:acm:eu-west-1:123456789012:certificate/{index}"
                for index in range(len(CERTIFICATE_KEY_TYPES))]
        with Stubber(clients["sts"]) as sts, Stubber(clients["acm"]) as acm, \
             Stubber(clients["elbv2"]) as elb, \
             patch("services.certificates.boto3.Session", return_value=session):
            sts.add_response("get_caller_identity", {"Account": "123456789012"}, {})
            for offset, indexes in enumerate((range(3), range(3, len(arns)))):
                response = {"CertificateSummaryList": [{"CertificateArn": arns[i],
                                                         "DomainName": f"cert-{i}.example.test"} for i in indexes]}
                params = dict(request)
                if offset == 0:
                    response["NextToken"] = "next-page"
                else:
                    params["NextToken"] = "next-page"
                acm.add_response("list_certificates", response, params)
                for i in indexes:
                    acm.add_response("describe_certificate", {"Certificate": {
                        "CertificateArn": arns[i], "DomainName": f"cert-{i}.example.test",
                        "Status": "ISSUED", "Type": "AMAZON_ISSUED", "KeyAlgorithm": CERTIFICATE_KEY_TYPES[i],
                    }}, {"CertificateArn": arns[i]})
            elb.add_response("describe_load_balancers", {"LoadBalancers": []}, {})
            result = discover_certificate_inventory("engineering", "eu-west-1")
            sts.assert_no_pending_responses(); acm.assert_no_pending_responses(); elb.assert_no_pending_responses()
        self.assertEqual(len(CERTIFICATE_KEY_TYPES), len(result["certificates"]))
        self.assertEqual(set(arns), {row["CertificateArn"] for row in result["certificates"]})

    def test_partial_service_errors_keep_certificates_and_report_categories(self):
        for error, category in (
            (ClientError({"Error": {"Code": "AccessDenied", "Message": "denied"}}, "DescribeLoadBalancers"), "permission"),
            (EndpointConnectionError(endpoint_url="https://example.invalid"), "network"),
        ):
            with self.subTest(category=category):
                clients = {service: Mock() for service in ("sts", "acm", "elbv2")}
                clients["sts"].get_caller_identity.return_value = {"Account": "123456789012"}
                clients["acm"].get_paginator.return_value.paginate.return_value = [{"CertificateSummaryList": [{"CertificateArn": "arn-test"}]}]
                clients["acm"].describe_certificate.return_value = {"Certificate": {"CertificateArn": "arn-test", "DomainName": "example.test"}}
                clients["elbv2"].get_paginator.return_value.paginate.side_effect = error
                session = Mock(); session.client.side_effect = lambda service, **_kw: clients[service]
                with patch("services.certificates.get_boto3_session", return_value=session):
                    result = discover_certificate_inventory("test", "eu-west-1")
                self.assertEqual(1, len(result["certificates"]))
                self.assertFalse(result["complete"])
                self.assertIn(category, result["warnings"][0])
