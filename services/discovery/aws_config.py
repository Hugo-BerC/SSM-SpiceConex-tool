"""AWS Config inventory source for architecture discovery.

AWS Config is intentionally used as a broad, optional source of truth.  It
gives the application a way to represent resource types for which we do not
yet have a service-specific collector, while the service APIs in ``engine``
continue to enrich live topology details such as listeners, targets and mount
targets.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from .graph import add_relation
from .models import InfrastructureGraph, ResourceNode


@dataclass(frozen=True)
class ConfigSnapshotStatus:
    available: bool
    active: bool
    resources: int = 0
    detail: str = ""


_TYPE_MAP = {
    "AWS::EC2::VPC": "VPC",
    "AWS::EC2::Subnet": "Subnet",
    "AWS::EC2::RouteTable": "RouteTable",
    "AWS::EC2::SecurityGroup": "SecurityGroup",
    "AWS::EC2::NetworkInterface": "NetworkInterface",
    "AWS::EC2::InternetGateway": "InternetGateway",
    "AWS::EC2::NatGateway": "NATGateway",
    "AWS::EC2::VPCEndpoint": "VPCEndpoint",
    "AWS::EC2::TransitGateway": "TransitGateway",
    "AWS::EC2::Instance": "EC2",
    "AWS::EC2::Volume": "EBS",
    "AWS::ElasticLoadBalancingV2::LoadBalancer": "LoadBalancer",
    "AWS::ElasticLoadBalancingV2::Listener": "Listener",
    "AWS::ElasticLoadBalancingV2::TargetGroup": "TargetGroup",
    "AWS::AutoScaling::AutoScalingGroup": "AutoScalingGroup",
    "AWS::Lambda::Function": "Lambda",
    "AWS::RDS::DBInstance": "RDS",
    "AWS::RDS::DBCluster": "RDSCluster",
    "AWS::EFS::FileSystem": "EFS",
    "AWS::EFS::MountTarget": "EFSMountTarget",
    "AWS::ECS::Cluster": "ECSCluster",
    "AWS::ECS::Service": "ECS",
    "AWS::EKS::Cluster": "EKS",
    "AWS::EKS::Nodegroup": "EKSNodeGroup",
    "AWS::ElastiCache::CacheCluster": "ElastiCache",
    "AWS::DynamoDB::Table": "DynamoDB",
    "AWS::FSx::FileSystem": "FSx",
    "AWS::S3::Bucket": "S3",
    "AWS::Route53::HostedZone": "Route53HostedZone",
    "AWS::CloudFront::Distribution": "CloudFront",
    "AWS::WAFv2::WebACL": "WAF",
    "AWS::ApiGateway::RestApi": "APIGateway",
    "AWS::ApiGatewayV2::Api": "APIGatewayV2",
    "AWS::SQS::Queue": "SQS",
    "AWS::SNS::Topic": "SNS",
    "AWS::Events::Rule": "EventBridgeRule",
    "AWS::StepFunctions::StateMachine": "StepFunctions",
    "AWS::OpenSearch::Domain": "OpenSearch",
    "AWS::Redshift::Cluster": "Redshift",
    "AWS::MSK::Cluster": "MSK",
    "AWS::SecretsManager::Secret": "Secret",
}


def _tags(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(key): str(item) for key, item in value.items() if key is not None and item is not None}
    result: dict[str, str] = {}
    for item in value or []:
        if isinstance(item, dict) and item.get("key") is not None and item.get("value") is not None:
            result[str(item["key"])] = str(item["value"])
    return result


def _configuration(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _value(configuration: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if configuration.get(key) not in (None, ""):
            return configuration[key]
    for nested_key in ("resourcesVpcConfig", "vpcConfig", "networkConfiguration"):
        nested = configuration.get(nested_key)
        if isinstance(nested, dict):
            for key in keys:
                if nested.get(key) not in (None, ""):
                    return nested[key]
    return ""


def _internal_type(config_type: str, configuration: dict[str, Any]) -> str:
    resource_type = _TYPE_MAP.get(config_type, config_type)
    if resource_type == "LoadBalancer":
        return "ALB" if str(_value(configuration, "type")).lower() == "application" else "NLB"
    return resource_type


def _resource_id(item: dict[str, Any], configuration: dict[str, Any]) -> str:
    config_type = str(item.get("resourceType", ""))
    resource_id = str(item.get("resourceId", ""))
    arn = str(item.get("arn", ""))
    internal_type = _internal_type(config_type, configuration)
    if internal_type in {"APIGateway", "APIGatewayV2"}:
        # Config represents API Gateway with an API ARN while the native
        # collectors use the API ID.  Canonicalise Config to that same ID so
        # live discovery enriches the existing node instead of producing a
        # second, visually identical API in inventory and DR selection.
        match = re.search(r"/(?:restapis|apis)/([^/]+)", arn)
        if match:
            return match.group(1)
    if internal_type in {"ALB", "NLB", "Listener", "TargetGroup", "Lambda", "RDS", "ECSCluster", "ECS", "EKS", "DynamoDB"} and arn:
        return arn
    if internal_type == "AutoScalingGroup":
        return f"asg:{resource_id}"
    if internal_type == "S3":
        return f"s3://{resource_id}"
    if internal_type == "Route53HostedZone":
        return f"route53:{resource_id.split('/')[-1]}"
    if config_type not in _TYPE_MAP:
        return f"config::{config_type}::{resource_id}"
    return resource_id or f"config::{config_type}::{arn}"


def _metadata(item: dict[str, Any], configuration: dict[str, Any], config_type: str) -> dict[str, Any]:
    metadata: dict[str, Any] = {"DiscoverySource": "AWS Config", "ConfigResourceType": config_type}
    vpc_id = _value(configuration, "vpcId", "VpcId", "VPCId")
    subnet_id = _value(configuration, "subnetId", "SubnetId")
    availability_zone = _value(configuration, "availabilityZone", "AvailabilityZone", "availabilityZoneName")
    if vpc_id:
        metadata["VpcId"] = vpc_id
    if subnet_id:
        metadata["SubnetId"] = subnet_id
    if availability_zone:
        metadata["AvailabilityZone"] = availability_zone
    return metadata


def _relation_name(value: Any) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", str(value or "related_to").lower()).strip("_")
    return text or "related_to"


def _query_all_current_resources(client) -> list[dict[str, Any]]:
    """Fallback for accounts that can query Config but not list its inventory."""
    expression = "SELECT resourceType, resourceId, resourceName, arn, awsRegion, accountId"
    result: list[dict[str, Any]] = []
    next_token = None
    while True:
        params = {"Expression": expression, "Limit": 100}
        if next_token:
            params["NextToken"] = next_token
        page = client.select_resource_config(**params)
        for raw in page.get("Results", []):
            try:
                parsed = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(parsed, dict):
                result.append(parsed)
        next_token = page.get("NextToken")
        if not next_token:
            return result


def _recorded_resources(client) -> list[dict[str, Any]]:
    """Enumerate every resource type currently recorded in this Config region.

    ``GetDiscoveredResourceCounts`` is important here: unlike Advanced Query,
    it does not exclude Config-supported resource types that are not queryable
    by that feature.  We then use ListDiscoveredResources per returned type,
    rather than maintaining an inevitably stale hard-coded AWS type list.
    """
    resource_types: list[str] = []
    next_token = None
    while True:
        params = {"limit": 100}
        if next_token:
            params["nextToken"] = next_token
        page = client.get_discovered_resource_counts(**params)
        resource_types.extend(
            str(item.get("resourceType", "")) for item in page.get("resourceCounts", [])
            if item.get("resourceType") and item.get("count", 0)
        )
        next_token = page.get("nextToken")
        if not next_token:
            break

    resources: list[dict[str, Any]] = []
    for resource_type in sorted(set(resource_types)):
        next_token = None
        while True:
            params = {"resourceType": resource_type, "includeDeletedResources": False, "limit": 100}
            if next_token:
                params["nextToken"] = next_token
            page = client.list_discovered_resources(**params)
            for identifier in page.get("resourceIdentifiers", []):
                if not identifier.get("resourceId"):
                    continue
                resources.append({
                    "resourceType": resource_type,
                    "resourceId": identifier["resourceId"],
                    "resourceName": identifier.get("resourceName", ""),
                })
            next_token = page.get("nextToken")
            if not next_token:
                break
    return resources


def _enrich_configuration_items(client, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Read detailed current configuration items in AWS Config's batch size."""
    enriched: dict[tuple[str, str], dict[str, Any]] = {}
    for start in range(0, len(items), 100):
        keys = []
        for item in items[start:start + 100]:
            key = {"resourceType": item["resourceType"], "resourceId": item["resourceId"]}
            if item.get("resourceName"):
                key["resourceName"] = item["resourceName"]
            keys.append(key)
        try:
            response = client.batch_get_resource_config(resourceKeys=keys)
        except Exception:
            # The basic advanced-query result is still a useful inventory if a
            # particular resource family cannot be read through the batch API.
            continue
        for item in response.get("baseConfigurationItems", []):
            if isinstance(item, dict):
                enriched[(str(item.get("resourceType", "")), str(item.get("resourceId", "")))] = item
    return [
        {**item, **enriched.get((str(item.get("resourceType", "")), str(item.get("resourceId", ""))), {})}
        for item in items
    ]


def discover_config_inventory(session, graph: InfrastructureGraph, region: str, account_id: str, client_config) -> ConfigSnapshotStatus:
    """Add current AWS Config resources and deterministic Config relationships.

    Failure is intentionally non-fatal: Config may be unrecorded, unavailable in
    a region, or denied to an otherwise valid operational profile.  The caller
    can then use the live service collectors as its fallback.
    """
    try:
        client = session.client("config", region_name=region, config=client_config)
        recorders = client.describe_configuration_recorders().get("ConfigurationRecorders", [])
        statuses = client.describe_configuration_recorder_status().get("ConfigurationRecordersStatus", [])
    except Exception as exc:
        return ConfigSnapshotStatus(False, False, detail=f"AWS Config unavailable: {exc}")

    active = bool(recorders) and any(item.get("recording") for item in statuses)
    if not active:
        return ConfigSnapshotStatus(True, False, detail="AWS Config recorder is not active; using live AWS service discovery.")

    try:
        items = _recorded_resources(client)
    except Exception as inventory_exc:
        # Older or tightly scoped Config policies can permit Advanced Query but
        # deny the inventory API. Keep a visibly partial fallback rather than
        # discarding useful configuration data altogether.
        try:
            items = _query_all_current_resources(client)
        except Exception as query_exc:
            return ConfigSnapshotStatus(True, True, detail=f"AWS Config inventory unavailable: {inventory_exc}; query fallback unavailable: {query_exc}")

    items = _enrich_configuration_items(client, items)
    indexed: dict[tuple[str, str], str] = {}
    accepted: list[tuple[dict[str, Any], dict[str, Any], str]] = []
    for item in items:
        item_region = str(item.get("awsRegion", ""))
        if item_region and item_region not in {region, "global"}:
            continue
        config_type = str(item.get("resourceType", ""))
        resource_id = str(item.get("resourceId", ""))
        if not config_type or not resource_id:
            continue
        configuration = _configuration(item.get("configuration"))
        internal_id = _resource_id(item, configuration)
        internal_type = _internal_type(config_type, configuration)
        name = str(item.get("resourceName") or resource_id)
        node = ResourceNode(
            internal_id, item.get("arn") or None, internal_type, name,
            item_region or region, str(item.get("accountId") or account_id),
            _tags(item.get("tags")), _metadata(item, configuration, config_type),
        )
        graph.add_node(node)
        indexed[(config_type, resource_id)] = internal_id
        accepted.append((item, configuration, internal_id))

    for item, _configuration_value, source_id in accepted:
        for relationship in item.get("relationships", []) or []:
            if not isinstance(relationship, dict):
                continue
            target_id = indexed.get((str(relationship.get("resourceType", "")), str(relationship.get("resourceId", ""))))
            if target_id:
                add_relation(
                    graph, source_id, target_id, _relation_name(relationship.get("relationshipName")),
                    confidence="AWS_CONFIG", discovery_source="AWS Config",
                )
    detail = "" if len(items) == len(accepted) else "AWS Config returned resources outside the selected region."
    return ConfigSnapshotStatus(True, True, resources=len(accepted), detail=detail)
