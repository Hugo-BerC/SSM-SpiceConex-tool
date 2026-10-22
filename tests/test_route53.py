from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from services.route53 import (
    ROUTE53_RECORD_COLUMNS,
    discover_hosted_zone_records,
    discover_private_hosted_zones,
)


class Route53DiscoveryTests(unittest.TestCase):
    def _session_with_pages(self, pages_by_operation):
        session = MagicMock()
        client = MagicMock()
        session.client.return_value = client

        def paginator(operation):
            paginator = MagicMock()
            paginator.paginate.return_value = pages_by_operation[operation]
            return paginator

        client.get_paginator.side_effect = paginator
        return session

    def test_only_private_hosted_zones_are_returned(self):
        session = self._session_with_pages(
            {
                "list_hosted_zones": [
                    {
                        "HostedZones": [
                            {
                                "Id": "/hostedzone/ZPRIVATE",
                                "Name": "internal.example.",
                                "ResourceRecordSetCount": 8,
                                "Config": {"PrivateZone": True, "Comment": "platform"},
                            },
                            {
                                "Id": "/hostedzone/ZPUBLIC",
                                "Name": "example.com.",
                                "ResourceRecordSetCount": 12,
                                "Config": {"PrivateZone": False},
                            },
                        ]
                    }
                ]
            }
        )
        with patch("services.route53.boto3.Session", return_value=session):
            result = discover_private_hosted_zones("engineering", "eu-west-1")

        self.assertEqual("engineering", result["profile"])
        self.assertEqual(["ZPRIVATE"], [zone["Id"] for zone in result["zones"]])
        self.assertEqual("internal.example", result["zones"][0]["Name"])
        self.assertEqual("platform", result["zones"][0]["Comment"])

    def test_record_sets_are_flattened_for_filterable_table(self):
        session = self._session_with_pages(
            {
                "list_resource_record_sets": [
                    {
                        "ResourceRecordSets": [
                            {
                                "Name": "api.internal.example.",
                                "Type": "A",
                                "TTL": 60,
                                "ResourceRecords": [{"Value": "10.0.1.15"}],
                            },
                            {
                                "Name": "service.internal.example.",
                                "Type": "A",
                                "SetIdentifier": "primary",
                                "Failover": "PRIMARY",
                                "HealthCheckId": "health-check-1",
                                "AliasTarget": {
                                    "DNSName": "internal-alb-1.eu-west-1.elb.amazonaws.com.",
                                    "EvaluateTargetHealth": True,
                                },
                            },
                        ]
                    }
                ]
            }
        )
        with patch("services.route53.boto3.Session", return_value=session):
            result = discover_hosted_zone_records("engineering", "eu-west-1", "/hostedzone/ZPRIVATE")

        self.assertEqual(ROUTE53_RECORD_COLUMNS, [
            "Name", "Type", "TTL", "Value / Target", "Routing", "Identifier", "Health Check",
        ])
        self.assertEqual(2, len(result["records"]))
        api = result["records"][0]
        alias = result["records"][1]
        self.assertEqual("api.internal.example", api["Name"])
        self.assertEqual("10.0.1.15", api["ValueTarget"])
        self.assertEqual("Simple", api["Routing"])
        self.assertEqual("", alias["TTL"])
        self.assertIn("internal-alb-1", alias["ValueTarget"])
        self.assertIn("evaluate target health", alias["ValueTarget"])
        self.assertEqual("Failover: PRIMARY", alias["Routing"])
        self.assertEqual("health-check-1", alias["HealthCheck"])

    def test_invalid_context_is_rejected_before_creating_session(self):
        with self.assertRaisesRegex(ValueError, "profile"):
            discover_private_hosted_zones("", "eu-west-1")
        with self.assertRaisesRegex(ValueError, "hosted zone"):
            discover_hosted_zone_records("engineering", "eu-west-1", "")


if __name__ == "__main__":
    unittest.main()
