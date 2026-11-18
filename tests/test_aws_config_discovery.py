from __future__ import annotations

import json
import unittest
from unittest.mock import Mock, patch

import boto3
from botocore.session import get_session
from botocore.stub import Stubber
from botocore.validate import validate_parameters

from services.discovery.aws_config import _resource_id, _enrich_configuration_items, _query_all_current_resources, discover_config_inventory
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
        model = get_session().get_service_model("config").operation_model("BatchGetResourceConfig")
        validate_parameters({"resourceKeys": resourceKeys}, model.input_shape)
        return {
            "baseConfigurationItems": [
                {
                    "resourceType": "AWS::EC2::VPC", "resourceId": "vpc-123", "resourceName": "core",
                    "awsRegion": "eu-west-1", "accountId": "123456789012", "configuration": "{}",
                },
                {
                    "resourceType": "AWS::EFS::FileSystem", "resourceId": "fs-123", "resourceName": "shared",
                    "awsRegion": "eu-west-1", "accountId": "123456789012", "configuration": json.dumps({"VpcId": "vpc-123"}),
                },
                {
                    "resourceType": "AWS::AppRunner::Service", "resourceId": "service-123", "resourceName": "public-api",
                    "awsRegion": "eu-west-1", "accountId": "123456789012", "configuration": "{}",
                },
            ]
        }

    def select_resource_config(self, **kwargs):
        assert "tags" in kwargs["Expression"] and "relationships" in kwargs["Expression"]
        return {"Results": [json.dumps(item) for item in [
            {"resourceType": "AWS::EC2::VPC", "resourceId": "vpc-123", "tags": [{"key": "Name", "value": "core"}]},
            {"resourceType": "AWS::EFS::FileSystem", "resourceId": "fs-123",
             "relationships": [{"resourceType": "AWS::EC2::VPC", "resourceId": "vpc-123",
                                "relationshipName": "is contained in"}]},
        ]]}


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
        self.assertEqual("core", graph.nodes["vpc-123"].tags["Name"])
        self.assertIn("config::AWS::AppRunner::Service::service-123", graph.nodes)
        self.assertTrue(any(edge.source == "fs-123" and edge.target == "vpc-123" for edge in graph.edges))

    def test_batch_contract_and_101_resource_boundary(self):
        client = boto3.client("config", region_name="eu-west-1", aws_access_key_id="testing",
                              aws_secret_access_key="testing")
        items = [{"resourceType": "AWS::EC2::VPC", "resourceId": f"vpc-{index}",
                  "resourceName": f"name-{index}"} for index in range(101)]
        with Stubber(client) as stub:
            for batch in (items[:100], items[100:]):
                keys = [{"resourceType": item["resourceType"], "resourceId": item["resourceId"]} for item in batch]
                stub.add_response("batch_get_resource_config", {
                    "baseConfigurationItems": [{**key, "configuration": "{}"} for key in keys],
                }, {"resourceKeys": keys})
            enriched = _enrich_configuration_items(client, items)
            stub.assert_no_pending_responses()
        self.assertEqual(101, len(enriched))
        self.assertTrue(all(item.get("configuration") == "{}" for item in enriched))

    def test_unprocessed_batch_keys_are_retried_without_repeating_completed_keys(self):
        client = Mock()
        first = {"resourceType": "AWS::EC2::VPC", "resourceId": "vpc-1"}
        second = {"resourceType": "AWS::EC2::VPC", "resourceId": "vpc-2"}
        client.batch_get_resource_config.side_effect = [
            {"baseConfigurationItems": [{**first, "configuration": "{}"}], "unprocessedResourceKeys": [second]},
            {"baseConfigurationItems": [{**second, "configuration": "{}"}]},
        ]
        with patch("services.discovery.aws_config.time.sleep"):
            enriched = _enrich_configuration_items(client, [first, second])
        self.assertEqual([second], client.batch_get_resource_config.call_args.kwargs["resourceKeys"])
        self.assertTrue(all(item.get("configuration") == "{}" for item in enriched))

    def test_query_denial_keeps_base_inventory_and_reports_missing_enrichment(self):
        client = ConfigClient()
        client.select_resource_config = Mock(side_effect=RuntimeError("AccessDenied"))
        session = Mock(); session.client.return_value = client
        graph = InfrastructureGraph("123456789012", "engineering", ["eu-west-1"])
        status = discover_config_inventory(session, graph, "eu-west-1", "123456789012", None)
        self.assertEqual(3, status.resources)
        self.assertEqual("vpc-123", graph.nodes["fs-123"].metadata["VpcId"])
        self.assertIn("AccessDenied", status.detail)

    def test_query_enrichment_follows_pagination_and_selects_complex_fields(self):
        client = Mock()
        client.select_resource_config.side_effect = [
            {"Results": [json.dumps({"resourceType": "AWS::EC2::VPC", "resourceId": "vpc-1", "tags": {"Name": "one"}})], "NextToken": "next-page"},
            {"Results": [json.dumps({"resourceType": "AWS::EC2::VPC", "resourceId": "vpc-2", "tags": {"Name": "two"}})]},
        ]
        rows = _query_all_current_resources(client)
        self.assertEqual(["vpc-1", "vpc-2"], [row["resourceId"] for row in rows])
        self.assertEqual("next-page", client.select_resource_config.call_args.kwargs["NextToken"])
        for field in ("configuration", "tags", "relationships"):
            self.assertIn(field, client.select_resource_config.call_args.kwargs["Expression"])

    def test_persistently_unprocessed_resources_are_bounded_and_reported(self):
        client = Mock()
        item = {"resourceType": "AWS::EC2::VPC", "resourceId": "vpc-1"}
        client.batch_get_resource_config.return_value = {"unprocessedResourceKeys": [item]}
        warnings = []
        with patch("services.discovery.aws_config.time.sleep"):
            result = _enrich_configuration_items(client, [item], warnings)
        self.assertEqual(3, client.batch_get_resource_config.call_count)
        self.assertEqual([item], result)
        self.assertTrue(warnings)
