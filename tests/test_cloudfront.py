from __future__ import annotations

from datetime import datetime, timezone
import unittest
from unittest.mock import MagicMock, patch

from services.cloudfront import CLOUDFRONT_COLUMNS, discover_distributions


class CloudFrontDiscoveryTests(unittest.TestCase):
    def test_distributions_are_flattened_for_inventory_filters(self):
        session = MagicMock()
        client = MagicMock()
        paginator = MagicMock()
        paginator.paginate.return_value = [
            {
                "DistributionList": {
                    "Items": [
                        {
                            "Id": "EABC123",
                            "ARN": "arn:aws:cloudfront::123456789012:distribution/EABC123",
                            "DomainName": "d111111abcdef8.cloudfront.net",
                            "Status": "Deployed",
                            "Enabled": True,
                            "Aliases": {"Items": ["app.example.com"]},
                            "Origins": {"Items": [
                                {"Id": "api", "DomainName": "api.example.com"},
                                {"Id": "static", "DomainName": "bucket.s3.amazonaws.com"},
                            ]},
                            "DefaultCacheBehavior": {
                                "TargetOriginId": "api",
                                "ViewerProtocolPolicy": "redirect-to-https",
                            },
                            "CacheBehaviors": {"Quantity": 3},
                            "HttpVersion": "http2and3",
                            "WebACLId": "arn:aws:wafv2:eu-west-1:123456789012:global/webacl/edge/abc",
                            "IsIPV6Enabled": True,
                            "PriceClass": "PriceClass_100",
                            "LastModifiedTime": datetime(2026, 10, 22, 9, 45, tzinfo=timezone.utc),
                        }
                    ]
                }
            }
        ]
        client.get_paginator.return_value = paginator
        session.client.return_value = client

        with patch("services.cloudfront.boto3.Session", return_value=session):
            result = discover_distributions("edge", "eu-west-1")

        self.assertEqual("edge", result["profile"])
        self.assertEqual(CLOUDFRONT_COLUMNS, [
            "Distribution ID", "Domain Name", "Aliases", "Status", "Enabled", "Origins",
            "Default Origin", "Behaviors", "Viewer Protocol", "HTTP Version", "WAF", "IPv6",
            "Price Class", "Last Modified",
        ])
        row = result["distributions"][0]
        self.assertEqual("EABC123", row["DistributionId"])
        self.assertEqual("app.example.com", row["Aliases"])
        self.assertEqual("2", row["Origins"])
        self.assertEqual("api · api.example.com", row["DefaultOrigin"])
        self.assertEqual("redirect-to-https", row["ViewerProtocol"])
        self.assertEqual("Enabled", row["WAF"])
        self.assertTrue(row["IPv6"])
        self.assertIn("2026-10-22T09:45:00", row["LastModified"])

    def test_missing_context_is_rejected_before_creating_session(self):
        with self.assertRaisesRegex(ValueError, "profile"):
            discover_distributions("", "eu-west-1")
        with self.assertRaisesRegex(ValueError, "region"):
            discover_distributions("edge", "")


if __name__ == "__main__":
    unittest.main()
