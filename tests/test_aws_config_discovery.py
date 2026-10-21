from __future__ import annotations

import json
import unittest

from services.discovery.aws_config import _resource_id, discover_config_inventory
from services.discovery.models import InfrastructureGraph


class ConfigClient:
    def describe_configuration_recorders(self):
        return {"ConfigurationRecorders": [{"name": "default"}]}

    def describe_configuration_recorder_status(self):
        return {"ConfigurationRecordersStatus": [{"name": "default", "recording": True}]}

    def get_discovered_resource_counts(self, **_kwargs):
        return {
            "resourceCounts": [
                {"resourceType": "AWS::EC2::VPC", "count": 1},
                {"resourceType": "AWS::EFS::FileSystem", "count": 1},
                {"resourceType": "AWS::AppRunner::Service", "count": 1},
            ]
        }

    def list_discovered_resources(self, resourceType, **_kwargs):
        identifiers = {
            "AWS::EC2::VPC": {"resourceId": "vpc-123", "resourceName": "core"},
            "AWS::EFS::FileSystem": {"resourceId": "fs-123", "resourceName": "shared"},
            "AWS::AppRunner::Service": {"resourceId": "service-123", "resourceName": "public-api"},
        }
        return {"resourceIdentifiers": [identifiers[resourceType]]}

    def batch_get_resource_config(self, resourceKeys):
        return {
            "baseConfigurationItems": [
                {
                    "resourceType": "AWS::EC2::VPC", "resourceId": "vpc-123", "resourceName": "core",
                    "awsRegion": "eu-west-1", "accountId": "123456789012", "configuration": "{}", "tags": {"Name": "core"},
                },
                {
                    "resourceType": "AWS::EFS::FileSystem", "resourceId": "fs-123", "resourceName": "shared",
                    "awsRegion": "eu-west-1", "accountId": "123456789012", "configuration": json.dumps({"VpcId": "vpc-123"}),
                    "relationships": [{"resourceType": "AWS::EC2::VPC", "resourceId": "vpc-123", "relationshipName": "is contained in"}],
                },
                {
                    "resourceType": "AWS::AppRunner::Service", "resourceId": "service-123", "resourceName": "public-api",
                    "awsRegion": "eu-west-1", "accountId": "123456789012", "configuration": "{}",
                },
            ]
        }


class ConfigSession:
    def client(self, name, **_kwargs):
        if name != "config":
            raise AssertionError(name)
        return ConfigClient()


class AwsConfigDiscoveryTests(unittest.TestCase):
    def test_api_gateway_config_arn_uses_the_same_identity_as_live_discovery(self):
        item = {
            "resourceType": "AWS::ApiGateway::RestApi",
            "resourceId": "config-specific-identifier",
            "arn": "arn:aws:apigateway:eu-south-2::/restapis/abc123",
        }
        self.assertEqual("abc123", _resource_id(item, {}))

    def test_active_recorder_contributes_known_unknown_and_related_resources(self):
        graph = InfrastructureGraph("123456789012", "engineering", ["eu-west-1"])

        status = discover_config_inventory(ConfigSession(), graph, "eu-west-1", "123456789012", None)

        self.assertTrue(status.available)
        self.assertTrue(status.active)
        self.assertEqual(3, status.resources)
        self.assertEqual("EFS", graph.nodes["fs-123"].resource_type)
        self.assertEqual("vpc-123", graph.nodes["fs-123"].metadata["VpcId"])
        self.assertIn("config::AWS::AppRunner::Service::service-123", graph.nodes)
        self.assertTrue(any(edge.source == "fs-123" and edge.target == "vpc-123" for edge in graph.edges))
