from __future__ import annotations

from collections import defaultdict
from typing import Any

from botocore.config import Config

from utils.logging import get_logger

from aws.session import get_boto3_session
from .aws_config import discover_config_inventory
from .graph import add_relation
from .models import InfrastructureGraph, ResourceNode

DEFAULT_TAG_KEY = "ib:resource:application"
SUPPORTED_TAG_KEYS = ("ib:resource:application", "ib:component:repo")
AWS_TIMEOUT_SECONDS = 20
logger = get_logger("discovery")


def _session_unavailable_error(exc: Exception) -> bool:
    """Return True for AWS credential/SSO failures that must escape discovery.

    Discovery must not turn an authentication failure into an apparently
    successful empty graph. The UI wrapper needs to see the exception so it
    can perform `aws sso login` and retry with a fresh boto3 session.
    """
    text = str(exc).lower()
    markers = (
        "expiredtoken",
        "expired token",
        "unauthorizedsso",
        "sso session",
        "token has expired",
        "unable to locate credentials",
        "no credentials",
        "credential should be refreshed",
        "invalidclienttokenid",
        "unrecognizedclientexception",
        "the security token included in the request is invalid",
        "security token is invalid",
        "unable to refresh credentials",
        "refresh failed",
    )
    return any(marker in text for marker in markers)


def _config() -> Config:
    return Config(
        connect_timeout=min(5, AWS_TIMEOUT_SECONDS),
        read_timeout=AWS_TIMEOUT_SECONDS,
        retries={"mode": "standard", "max_attempts": 1},
        tcp_keepalive=True,
    )


def _tags(items: Any, key_name: str = "Key", value_name: str = "Value") -> dict[str, str]:
    if isinstance(items, dict):
        return {str(key): str(value) for key, value in items.items() if key is not None and value is not None}
    result: dict[str, str] = {}
    for item in items or []:
        if not isinstance(item, dict):
            continue
        key = item.get(key_name)
        value = item.get(value_name)
        if key is not None and value is not None:
            result[str(key)] = str(value)
    return result


def _safe_tags(fn) -> dict[str, str]:
    try:
        return _tags(fn())
    except Exception:
        return {}


def _add(graph, resource_id, arn, resource_type, name, region, account_id, tags, **metadata):
    graph.add_node(ResourceNode(resource_id, arn, resource_type, name, region, account_id, tags, metadata))
    return graph.nodes[resource_id]


def _paginate(client, operation: str, result_key: str, **kwargs):
    paginator = client.get_paginator(operation)
    for page in paginator.paginate(**kwargs):
        yield from page.get(result_key, [])


def _attach_network(
    graph: InfrastructureGraph,
    resource_id: str,
    subnet_ids: list[str] | tuple[str, ...] = (),
    security_group_ids: list[str] | tuple[str, ...] = (),
    vpc_id: str = "",
) -> str:
    """Add only explicit network relationships and return the resolved VPC.

    AWS APIs expose network placement in slightly different shapes.  Centralising
    the linking prevents the architecture map from guessing placement from
    names, descriptions or tags.  A VPC is returned only when the API supplied
    one directly or every linked subnet belongs to the same discovered VPC.
    """
    vpc_ids = {str(vpc_id)} if vpc_id else set()
    for subnet_id in subnet_ids or ():
        subnet_id = str(subnet_id)
        subnet = graph.nodes.get(subnet_id)
        if subnet:
            add_relation(graph, resource_id, subnet_id, "attached_to")
            if subnet.metadata.get("VpcId"):
                vpc_ids.add(str(subnet.metadata["VpcId"]))
    for group_id in security_group_ids or ():
        identifier = group_id.get("GroupId", "") if isinstance(group_id, dict) else str(group_id)
        if identifier in graph.nodes:
            add_relation(graph, resource_id, identifier, "associated_with")
    if len(vpc_ids) == 1:
        resolved = next(iter(vpc_ids))
        if resolved in graph.nodes:
            add_relation(graph, resolved, resource_id, "contains")
        return resolved
    return ""


def discover_infrastructure(profile: str, region: str, scope: str = "CURRENT REGION", tag_key: str = DEFAULT_TAG_KEY, selected_regions: list[str] | None = None) -> InfrastructureGraph:
    """Build a deterministic AWS infrastructure graph.

    The first implementation deliberately favours service APIs over heuristic
    inference. Tags define application ownership; API relationships define the
    topology. Untagged resources may be included as shared dependencies when a
    product scope is selected in the UI.
    """
    profile = profile.strip()
    region = (region or "eu-west-1").strip()
    tag_key = tag_key.strip() or DEFAULT_TAG_KEY
    if not profile:
        raise ValueError("No AWS profile is selected.")
    if not region:
        raise ValueError("No AWS region is selected in Terminal.")

    # Use the application session factory so Architecture Discovery follows the
    # same non-cached credential/SSO lifecycle as the rest of the UI.
    session = get_boto3_session(profile, region)
    sts = session.client("sts", config=_config())
    identity = sts.get_caller_identity()
    account_id = identity.get("Account", "")
    account_name = profile
    graph = InfrastructureGraph(account_id, account_name)

    if scope.upper() == "SELECTED REGIONS":
        regions = list(dict.fromkeys(item.strip() for item in (selected_regions or []) if item.strip()))
        if len(regions) < 2:
            raise ValueError("Select at least two different regions for selected-region discovery.")
    elif scope.upper() == "ALL REGIONS":
        ec2 = session.client("ec2", region_name=region, config=_config())
        try:
            regions = [r["RegionName"] for r in ec2.describe_regions(AllRegions=False).get("Regions", [])]
        except Exception as exc:
            if _session_unavailable_error(exc):
                raise
            regions = [region]
            graph.warnings.append(f"Unable to enumerate enabled regions: {exc}")
    else:
        regions = [region]

    graph.regions = sorted(set(regions))
    for current_region in graph.regions:
        try:
            _discover_region(session, graph, current_region, account_id, tag_key)
        except Exception as exc:
            if _session_unavailable_error(exc):
                raise
            logger.exception("Architecture discovery failed | region=%s", current_region)
            graph.warnings.append(f"{current_region}: discovery failed: {exc}")

    # Global services are collected once, independently from the regional loop.
    try:
        _discover_global(session, graph, account_id, tag_key)
    except Exception as exc:
        if _session_unavailable_error(exc):
            raise
        logger.exception("Architecture global discovery failed")
        graph.warnings.append(f"Global services: discovery failed: {exc}")

    return graph


def _discover_region(session, graph, region, account_id, tag_key):
    ec2 = session.client("ec2", region_name=region, config=_config())

    # Config supplies the broad account inventory and its recorded
    # relationships.  Live APIs below remain responsible for timely service
    # detail and for accounts where Config is not enabled.
    config_status = discover_config_inventory(session, graph, region, account_id, _config())
    if config_status.active and config_status.resources:
        graph.discovery_sources.append(f"AWS Config {region}: {config_status.resources} recorded resources")
    if config_status.detail:
        graph.warnings.append(f"{region}: {config_status.detail}")

    vpc_by_id = {}
    for vpc in _paginate(ec2, "describe_vpcs", "Vpcs"):
        vpc_tags = _tags(vpc.get("Tags"))
        node = _add(graph, vpc["VpcId"], vpc.get("VpcArn"), "VPC", vpc_tags.get("Name", vpc["VpcId"]), region, account_id, vpc_tags, IsDefault=vpc.get("IsDefault", False), CidrBlock=vpc.get("CidrBlock", ""))
        vpc_by_id[vpc["VpcId"]] = node

    subnet_by_id = {}
    for subnet in _paginate(ec2, "describe_subnets", "Subnets"):
        node = _add(graph, subnet["SubnetId"], None, "Subnet", subnet["SubnetId"], region, account_id, _tags(subnet.get("Tags")), VpcId=subnet.get("VpcId"), AvailabilityZone=subnet.get("AvailabilityZone", ""), CidrBlock=subnet.get("CidrBlock", ""), Public=subnet.get("MapPublicIpOnLaunch", False))
        subnet_by_id[subnet["SubnetId"]] = node
        if subnet.get("VpcId") in vpc_by_id:
            add_relation(graph, subnet["VpcId"], subnet["SubnetId"], "contains")

    rt_by_id = {}
    for rt in _paginate(ec2, "describe_route_tables", "RouteTables"):
        node = _add(graph, rt["RouteTableId"], None, "RouteTable", rt["RouteTableId"], region, account_id, _tags(rt.get("Tags")), VpcId=rt.get("VpcId"))
        rt_by_id[rt["RouteTableId"]] = node
        if rt.get("VpcId") in vpc_by_id:
            add_relation(graph, rt["VpcId"], rt["RouteTableId"], "contains")
        for assoc in rt.get("Associations", []):
            subnet_id = assoc.get("SubnetId")
            if subnet_id and subnet_id in subnet_by_id:
                add_relation(graph, rt["RouteTableId"], subnet_id, "associated_with")
        for route in rt.get("Routes", []):
            target = route.get("GatewayId") or route.get("NatGatewayId") or route.get("TransitGatewayId") or route.get("VpcPeeringConnectionId") or route.get("NetworkInterfaceId") or route.get("InstanceId") or route.get("VpcEndpointId")
            if target:
                if target not in graph.nodes:
                    target_type = "TransitGateway" if route.get("TransitGatewayId") else "VPCPeering" if route.get("VpcPeeringConnectionId") else "RouteTarget"
                    _add(graph, target, None, target_type, target, region, account_id, {})
                add_relation(graph, rt["RouteTableId"], target, "routes_to", Destination=route.get("DestinationCidrBlock") or route.get("DestinationPrefixListId", ""))

    for igw in _paginate(ec2, "describe_internet_gateways", "InternetGateways"):
        node = _add(graph, igw["InternetGatewayId"], None, "InternetGateway", igw["InternetGatewayId"], region, account_id, _tags(igw.get("Tags")))
        for attachment in igw.get("Attachments", []):
            if attachment.get("VpcId") in vpc_by_id:
                add_relation(graph, igw["InternetGatewayId"], attachment["VpcId"], "attached_to")

    for nat in _paginate(ec2, "describe_nat_gateways", "NatGateways"):
        if nat.get("State") == "deleted":
            continue
        _add(graph, nat["NatGatewayId"], None, "NATGateway", nat["NatGatewayId"], region, account_id, _tags(nat.get("Tags")), VpcId=nat.get("VpcId"), SubnetId=nat.get("SubnetId"), State=nat.get("State"))
        if nat.get("VpcId") in vpc_by_id:
            add_relation(graph, nat["VpcId"], nat["NatGatewayId"], "contains")
        if nat.get("SubnetId") in subnet_by_id:
            add_relation(graph, nat["NatGatewayId"], nat["SubnetId"], "attached_to")

    sg_by_id = {}
    for sg in _paginate(ec2, "describe_security_groups", "SecurityGroups"):
        node = _add(graph, sg["GroupId"], None, "SecurityGroup", sg.get("GroupName") or sg["GroupId"], region, account_id, _tags(sg.get("Tags")), VpcId=sg.get("VpcId"), Description=sg.get("Description", ""))
        sg_by_id[sg["GroupId"]] = node
        if sg.get("VpcId") in vpc_by_id:
            add_relation(graph, sg["VpcId"], sg["GroupId"], "contains")

    eni_by_id = {}
    for eni in _paginate(ec2, "describe_network_interfaces", "NetworkInterfaces"):
        node = _add(graph, eni["NetworkInterfaceId"], None, "NetworkInterface", eni["NetworkInterfaceId"], region, account_id, _tags(eni.get("TagSet")), VpcId=eni.get("VpcId"), SubnetId=eni.get("SubnetId"), PrivateIp=eni.get("PrivateIpAddress", ""), Description=eni.get("Description", ""), AttachmentInstanceId=eni.get("Attachment", {}).get("InstanceId", ""))
        eni_by_id[eni["NetworkInterfaceId"]] = node
        if eni.get("SubnetId") in subnet_by_id:
            add_relation(graph, eni["SubnetId"], eni["NetworkInterfaceId"], "contains")
        for group in eni.get("Groups", []):
            if group.get("GroupId") in sg_by_id:
                add_relation(graph, eni["NetworkInterfaceId"], group["GroupId"], "associated_with")

    instance_by_id = {}
    for reservation in _paginate(ec2, "describe_instances", "Reservations"):
        for instance in reservation.get("Instances", []):
            instance_id = instance["InstanceId"]
            tags = _tags(instance.get("Tags"))
            name = tags.get("Name", instance_id)
            node = _add(graph, instance_id, None, "EC2", name, region, account_id, tags, VpcId=instance.get("VpcId"), SubnetId=instance.get("SubnetId"), State=instance.get("State", {}).get("Name", ""), InstanceType=instance.get("InstanceType", ""), AvailabilityZone=instance.get("Placement", {}).get("AvailabilityZone", ""))
            instance_by_id[instance_id] = node
            if instance.get("SubnetId") in subnet_by_id:
                add_relation(graph, instance["SubnetId"], instance_id, "contains")
            for group in instance.get("SecurityGroups", []):
                if group.get("GroupId") in sg_by_id:
                    add_relation(graph, instance_id, group["GroupId"], "associated_with")
            for mapping in instance.get("BlockDeviceMappings", []):
                if mapping.get("Ebs", {}).get("VolumeId"):
                    volume_id = mapping["Ebs"]["VolumeId"]
                    if volume_id not in graph.nodes:
                        _add(graph, volume_id, None, "EBS", volume_id, region, account_id, {}, Device=mapping.get("Device", ""))
                    add_relation(graph, instance_id, volume_id, "attached_to")

    # Resolve ENI -> EC2 from the AWS attachment metadata. This is a direct
    # relationship; do not infer instance ownership from names/descriptions.
    for eni_id in eni_by_id:
        instance_id = graph.nodes[eni_id].metadata.get("AttachmentInstanceId")
        if instance_id in instance_by_id:
            add_relation(graph, eni_id, instance_id, "attached_to", confidence="DIRECT")

    # ELBv2
    elb = session.client("elbv2", region_name=region, config=_config())
    tg_by_arn = {}
    load_balancer_arns = []
    try:
        for lb in _paginate(elb, "describe_load_balancers", "LoadBalancers"):
            lb_id = lb["LoadBalancerArn"]
            load_balancer_arns.append(lb_id)
            tags = _safe_tags(lambda arn=lb_id: elb.describe_tags(ResourceArns=[arn])["TagDescriptions"][0].get("Tags", []))
            _add(graph, lb_id, lb_id, "ALB" if lb.get("Type") == "application" else "NLB", lb.get("LoadBalancerName", lb_id), region, account_id, tags, VpcId=lb.get("VpcId"), Scheme=lb.get("Scheme", ""))
            if lb.get("VpcId") in vpc_by_id:
                add_relation(graph, lb.get("VpcId"), lb_id, "contains")
            for subnet in lb.get("AvailabilityZones", []):
                subnet_id = subnet.get("SubnetId")
                if subnet_id in subnet_by_id:
                    add_relation(graph, lb_id, subnet_id, "attached_to")

        for tg in _paginate(elb, "describe_target_groups", "TargetGroups"):
            arn = tg["TargetGroupArn"]
            tags = _safe_tags(lambda arn=arn: elb.describe_tags(ResourceArns=[arn])["TagDescriptions"][0].get("Tags", []))
            tg_by_arn[arn] = _add(graph, arn, arn, "TargetGroup", tg.get("TargetGroupName", arn), region, account_id, tags, Protocol=tg.get("Protocol", ""), Port=tg.get("Port"), TargetType=tg.get("TargetType", ""), VpcId=tg.get("VpcId"))
            if tg.get("VpcId") in vpc_by_id:
                add_relation(graph, tg.get("VpcId"), arn, "contains")
            try:
                targets = elb.describe_target_health(TargetGroupArn=arn).get("TargetHealthDescriptions", [])
                for target in targets:
                    target_id = target.get("Target", {}).get("Id")
                    if target_id in instance_by_id:
                        add_relation(graph, arn, target_id, "targets")
            except Exception:
                pass

        # DescribeListeners does not support an account-wide paginator: AWS
        # requires a load-balancer ARN (or explicit listener ARNs). Query each
        # discovered load balancer so one invalid call cannot discard all ELB
        # relationships in the region.
        for lb_arn in load_balancer_arns:
            try:
                for listener in _paginate(elb, "describe_listeners", "Listeners", LoadBalancerArn=lb_arn):
                    listener_arn = listener["ListenerArn"]
                    _add(graph, listener_arn, listener_arn, "Listener", f"{listener.get('Protocol','')}:{listener.get('Port','')}", region, account_id, {}, Protocol=listener.get("Protocol", ""), Port=listener.get("Port"))
                    if lb_arn in graph.nodes:
                        add_relation(graph, lb_arn, listener_arn, "contains")
                    for cert in listener.get("Certificates", []):
                        cert_arn = cert.get("CertificateArn")
                        if cert_arn:
                            if cert_arn not in graph.nodes:
                                _add(graph, cert_arn, cert_arn, "ACMCertificate", cert_arn.rsplit("/", 1)[-1], region, account_id, {})
                            add_relation(graph, listener_arn, cert_arn, "uses")
                    for action in listener.get("DefaultActions", []):
                        tg_arn = action.get("TargetGroupArn")
                        if tg_arn in tg_by_arn:
                            add_relation(graph, listener_arn, tg_arn, "targets")
            except Exception as exc:
                if _session_unavailable_error(exc):
                    raise
                graph.warnings.append(f"{region}: listener discovery incomplete for {lb_arn.rsplit('/', 1)[-1]}: {exc}")
    except Exception as exc:
        graph.warnings.append(f"{region}: ELB discovery incomplete: {exc}")

    # RDS
    try:
        rds = session.client("rds", region_name=region, config=_config())
        for db in _paginate(rds, "describe_db_instances", "DBInstances"):
            arn = db.get("DBInstanceArn")
            db_id = arn or db.get("DBInstanceIdentifier")
            tags = _safe_tags(lambda arn=arn: rds.list_tags_for_resource(ResourceName=arn).get("TagList", [])) if arn else {}
            _add(graph, db_id, arn, "RDS", db.get("DBInstanceIdentifier", db_id), region, account_id, tags, Engine=db.get("Engine", ""), VpcId=db.get("DBSubnetGroup", {}).get("VpcId"), DBSubnetGroup=db.get("DBSubnetGroup", {}).get("DBSubnetGroupName", ""))
            for group in db.get("VpcSecurityGroups", []):
                if group.get("VpcSecurityGroupId") in sg_by_id:
                    add_relation(graph, db_id, group["VpcSecurityGroupId"], "associated_with")
            for subnet in db.get("DBSubnetGroup", {}).get("Subnets", []):
                subnet_id = subnet.get("SubnetIdentifier")
                if subnet_id in subnet_by_id:
                    add_relation(graph, db_id, subnet_id, "attached_to")
    except Exception as exc:
        graph.warnings.append(f"{region}: RDS discovery incomplete: {exc}")

    # Lambda functions with VPC configuration.
    try:
        lam = session.client("lambda", region_name=region, config=_config())
        for fn in _paginate(lam, "list_functions", "Functions"):
            name = fn.get("FunctionName", "")
            arn = fn.get("FunctionArn")
            tags = _safe_tags(lambda arn=arn: lam.list_tags(Resource=arn).get("Tags", {}))
            cfg = fn.get("VpcConfig", {})
            resource_id = arn or name
            node = _add(graph, resource_id, arn, "Lambda", name, region, account_id, tags,
                        Runtime=fn.get("Runtime", ""), State=fn.get("State", ""))
            vpc_id = _attach_network(graph, resource_id, cfg.get("SubnetIds", []), cfg.get("SecurityGroupIds", []), cfg.get("VpcId", ""))
            if vpc_id:
                node.metadata["VpcId"] = vpc_id
    except Exception as exc:
        graph.warnings.append(f"{region}: Lambda discovery incomplete: {exc}")

    # API Gateway metrics use an API name for REST APIs and an API ID for the
    # v2 HTTP/WebSocket APIs. Live discovery enriches the Config inventory so
    # metric dimensions are reliable even when Config has partial metadata.
    try:
        api_gateway = session.client("apigateway", region_name=region, config=_config())
        for api in _paginate(api_gateway, "get_rest_apis", "items"):
            api_id = api.get("id")
            if api_id:
                _add(graph, api_id, f"arn:aws:apigateway:{region}::/restapis/{api_id}", "APIGateway",
                     api.get("name", api_id), region, account_id, _tags(api.get("tags")), ApiId=api_id, Protocol="REST")
    except Exception as exc:
        if _session_unavailable_error(exc):
            raise
        graph.warnings.append(f"{region}: API Gateway REST discovery incomplete: {exc}")
    try:
        api_gateway_v2 = session.client("apigatewayv2", region_name=region, config=_config())
        for api in _paginate(api_gateway_v2, "get_apis", "Items"):
            api_id = api.get("ApiId")
            if api_id:
                _add(graph, api_id, f"arn:aws:apigateway:{region}::/apis/{api_id}", "APIGatewayV2",
                     api.get("Name", api_id), region, account_id, _tags(api.get("Tags")), ApiId=api_id, Protocol=api.get("ProtocolType", ""))
    except Exception as exc:
        if _session_unavailable_error(exc):
            raise
        graph.warnings.append(f"{region}: API Gateway v2 discovery incomplete: {exc}")

    # Auto Scaling groups link compute capacity to its actual subnets and EC2
    # members.  They are collected before EKS node groups, which can reference
    # their backing Auto Scaling group.
    try:
        autoscaling = session.client("autoscaling", region_name=region, config=_config())
        for group in _paginate(autoscaling, "describe_auto_scaling_groups", "AutoScalingGroups"):
            name = group.get("AutoScalingGroupName", "")
            if not name:
                continue
            subnets = [value.strip() for value in group.get("VPCZoneIdentifier", "").split(",") if value.strip()]
            node = _add(
                graph, f"asg:{name}", group.get("AutoScalingGroupARN"), "AutoScalingGroup", name,
                region, account_id, _tags(group.get("Tags")),
                DesiredCapacity=group.get("DesiredCapacity"), MinSize=group.get("MinSize"), MaxSize=group.get("MaxSize"),
            )
            vpc_id = _attach_network(graph, node.id, subnets)
            if vpc_id:
                node.metadata["VpcId"] = vpc_id
            for member in group.get("Instances", []):
                instance_id = member.get("InstanceId")
                if instance_id in instance_by_id:
                    add_relation(graph, node.id, instance_id, "contains")
    except Exception as exc:
        graph.warnings.append(f"{region}: Auto Scaling discovery incomplete: {exc}")

    # EFS file systems are regional, but their mount targets are VPC/subnet
    # resources.  Keep mount targets in the graph for deterministic placement
    # while hiding them from the high-level map.
    try:
        efs = session.client("efs", region_name=region, config=_config())
        for filesystem in _paginate(efs, "describe_file_systems", "FileSystems"):
            filesystem_id = filesystem.get("FileSystemId", "")
            if not filesystem_id:
                continue
            node = _add(
                graph, filesystem_id, filesystem.get("FileSystemArn"), "EFS",
                filesystem.get("Name") or filesystem_id, region, account_id, _tags(filesystem.get("Tags")),
                LifecycleState=filesystem.get("LifeCycleState", ""), PerformanceMode=filesystem.get("PerformanceMode", ""),
            )
            vpc_ids = set()
            for target in _paginate(efs, "describe_mount_targets", "MountTargets", FileSystemId=filesystem_id):
                target_id = target.get("MountTargetId", "")
                if not target_id:
                    continue
                mount = _add(
                    graph, target_id, target.get("MountTargetArn"), "EFSMountTarget", target_id,
                    region, account_id, {}, FileSystemId=filesystem_id, SubnetId=target.get("SubnetId", ""),
                    VpcId=target.get("VpcId", ""), AvailabilityZone=target.get("AvailabilityZoneName", ""),
                )
                add_relation(graph, filesystem_id, target_id, "contains")
                resolved = _attach_network(
                    graph, target_id, [target.get("SubnetId", "")], target.get("SecurityGroups", []), target.get("VpcId", "")
                )
                if resolved:
                    vpc_ids.add(resolved)
            if len(vpc_ids) == 1:
                node.metadata["VpcId"] = next(iter(vpc_ids))
                add_relation(graph, node.metadata["VpcId"], filesystem_id, "contains")
    except Exception as exc:
        graph.warnings.append(f"{region}: EFS discovery incomplete: {exc}")

    # ECS services in awsvpc mode have a direct subnet/security-group
    # configuration; classic services remain visible as regional services.
    try:
        ecs = session.client("ecs", region_name=region, config=_config())
        for cluster_arn in _paginate(ecs, "list_clusters", "clusterArns"):
            cluster_data = ecs.describe_clusters(clusters=[cluster_arn], include=["TAGS"]).get("clusters", [])
            if not cluster_data:
                continue
            cluster = cluster_data[0]
            cluster_id = cluster.get("clusterArn", cluster_arn)
            _add(graph, cluster_id, cluster_id, "ECSCluster", cluster.get("clusterName", cluster_id), region, account_id,
                 _tags(cluster.get("tags")), Status=cluster.get("status", ""))
            for service_arn in _paginate(ecs, "list_services", "serviceArns", cluster=cluster_arn):
                service_data = ecs.describe_services(cluster=cluster_arn, services=[service_arn], include=["TAGS"]).get("services", [])
                if not service_data:
                    continue
                service = service_data[0]
                service_id = service.get("serviceArn", service_arn)
                network = service.get("networkConfiguration", {}).get("awsvpcConfiguration", {})
                node = _add(
                    graph, service_id, service_id, "ECS", service.get("serviceName", service_id), region, account_id,
                    _tags(service.get("tags")), ClusterArn=cluster_id, Status=service.get("status", ""),
                    DesiredCount=service.get("desiredCount", 0), RunningCount=service.get("runningCount", 0),
                )
                add_relation(graph, cluster_id, service_id, "contains")
                vpc_id = _attach_network(graph, service_id, network.get("subnets", []), network.get("securityGroups", []))
                if vpc_id:
                    node.metadata["VpcId"] = vpc_id
                for load_balancer in service.get("loadBalancers", []):
                    target_group = load_balancer.get("targetGroupArn")
                    if target_group in tg_by_arn:
                        add_relation(graph, service_id, target_group, "uses")
    except Exception as exc:
        graph.warnings.append(f"{region}: ECS discovery incomplete: {exc}")

    # EKS clusters and managed node groups expose their VPC configuration
    # directly.  The backing ASG relation is included only when AWS returned it.
    try:
        eks = session.client("eks", region_name=region, config=_config())
        for cluster_name in _paginate(eks, "list_clusters", "clusters"):
            cluster = eks.describe_cluster(name=cluster_name).get("cluster", {})
            cluster_id = cluster.get("arn", cluster_name)
            config = cluster.get("resourcesVpcConfig", {})
            node = _add(
                graph, cluster_id, cluster.get("arn"), "EKS", cluster.get("name", cluster_name), region, account_id,
                _tags(cluster.get("tags")), Status=cluster.get("status", ""), Version=cluster.get("version", ""),
            )
            security_groups = list(config.get("securityGroupIds", []))
            if config.get("clusterSecurityGroupId"):
                security_groups.append(config["clusterSecurityGroupId"])
            vpc_id = _attach_network(graph, cluster_id, config.get("subnetIds", []), security_groups, config.get("vpcId", ""))
            if vpc_id:
                node.metadata["VpcId"] = vpc_id
            for nodegroup_name in _paginate(eks, "list_nodegroups", "nodegroups", clusterName=cluster_name):
                nodegroup = eks.describe_nodegroup(clusterName=cluster_name, nodegroupName=nodegroup_name).get("nodegroup", {})
                nodegroup_id = nodegroup.get("nodegroupArn", f"{cluster_id}:nodegroup:{nodegroup_name}")
                group_node = _add(
                    graph, nodegroup_id, nodegroup.get("nodegroupArn"), "EKSNodeGroup",
                    nodegroup.get("nodegroupName", nodegroup_name), region, account_id, _tags(nodegroup.get("tags")),
                    ClusterArn=cluster_id, Status=nodegroup.get("status", ""),
                )
                add_relation(graph, cluster_id, nodegroup_id, "contains")
                group_vpc = _attach_network(graph, nodegroup_id, nodegroup.get("subnets", []), vpc_id=vpc_id)
                if group_vpc:
                    group_node.metadata["VpcId"] = group_vpc
                for group_ref in nodegroup.get("resources", {}).get("autoScalingGroups", []):
                    asg_id = f"asg:{group_ref.get('name', '')}"
                    if asg_id in graph.nodes:
                        add_relation(graph, nodegroup_id, asg_id, "contains")
    except Exception as exc:
        graph.warnings.append(f"{region}: EKS discovery incomplete: {exc}")

    try:
        cache = session.client("elasticache", region_name=region, config=_config())
        cache_subnets = {
            item.get("CacheSubnetGroupName", ""): [subnet.get("SubnetIdentifier", "") for subnet in item.get("Subnets", [])]
            for item in _paginate(cache, "describe_cache_subnet_groups", "CacheSubnetGroups")
        }
        for cluster in _paginate(cache, "describe_cache_clusters", "CacheClusters"):
            cluster_name = cluster.get("CacheClusterId", "")
            if not cluster_name:
                continue
            arn = cluster.get("ARN")
            tags = _safe_tags(lambda arn=arn: cache.list_tags_for_resource(ResourceName=arn).get("TagList", [])) if arn else {}
            node = _add(
                graph, f"elasticache:{cluster_name}", arn, "ElastiCache", cluster_name, region, account_id, tags,
                Engine=cluster.get("Engine", ""), Status=cluster.get("CacheClusterStatus", ""),
                ReplicationGroupId=cluster.get("ReplicationGroupId", ""),
            )
            subnets = cache_subnets.get(cluster.get("CacheSubnetGroupName", ""), [])
            groups = [item.get("SecurityGroupId", "") for item in cluster.get("SecurityGroups", [])]
            vpc_id = _attach_network(graph, node.id, subnets, groups)
            if vpc_id:
                node.metadata["VpcId"] = vpc_id
    except Exception as exc:
        graph.warnings.append(f"{region}: ElastiCache discovery incomplete: {exc}")

    try:
        dynamodb = session.client("dynamodb", region_name=region, config=_config())
        for table_name in _paginate(dynamodb, "list_tables", "TableNames"):
            table = dynamodb.describe_table(TableName=table_name).get("Table", {})
            arn = table.get("TableArn")
            tags = _safe_tags(lambda arn=arn: dynamodb.list_tags_of_resource(ResourceArn=arn).get("Tags", [])) if arn else {}
            _add(graph, arn or f"dynamodb:{table_name}", arn, "DynamoDB", table_name, region, account_id, tags,
                 Status=table.get("TableStatus", ""), BillingMode=table.get("BillingModeSummary", {}).get("BillingMode", ""))
    except Exception as exc:
        graph.warnings.append(f"{region}: DynamoDB discovery incomplete: {exc}")

    try:
        fsx = session.client("fsx", region_name=region, config=_config())
        for filesystem in _paginate(fsx, "describe_file_systems", "FileSystems"):
            filesystem_id = filesystem.get("FileSystemId", "")
            if not filesystem_id:
                continue
            node = _add(
                graph, filesystem_id, filesystem.get("ResourceARN"), "FSx", filesystem_id, region, account_id,
                _tags(filesystem.get("Tags")), FileSystemType=filesystem.get("FileSystemType", ""), Lifecycle=filesystem.get("Lifecycle", ""),
            )
            vpc_id = _attach_network(
                graph, node.id, filesystem.get("SubnetIds", []), filesystem.get("SecurityGroupIds", []), filesystem.get("VpcId", "")
            )
            if vpc_id:
                node.metadata["VpcId"] = vpc_id
    except Exception as exc:
        graph.warnings.append(f"{region}: FSx discovery incomplete: {exc}")

    # Interface and gateway endpoints are key to explaining private AWS traffic.
    try:
        for endpoint in _paginate(ec2, "describe_vpc_endpoints", "VpcEndpoints"):
            endpoint_id = endpoint.get("VpcEndpointId", "")
            if not endpoint_id:
                continue
            node = _add(
                graph, endpoint_id, None, "VPCEndpoint", endpoint.get("ServiceName", endpoint_id), region, account_id,
                _tags(endpoint.get("Tags")), VpcId=endpoint.get("VpcId", ""), EndpointType=endpoint.get("VpcEndpointType", ""),
            )
            _attach_network(graph, node.id, endpoint.get("SubnetIds", []), endpoint.get("Groups", []), endpoint.get("VpcId", ""))
    except Exception as exc:
        graph.warnings.append(f"{region}: VPC endpoint discovery incomplete: {exc}")


def _discover_global(session, graph, account_id, tag_key):
    # S3 bucket inventory is account-global. Location is best-effort; bucket tags
    # are used for application ownership. We intentionally do not infer network
    # relationships for S3 here because access paths may be policy-based.
    s3 = session.client("s3", config=_config())
    try:
        for bucket in s3.list_buckets().get("Buckets", []):
            name = bucket.get("Name", "")
            tags = _safe_tags(lambda name=name: s3.get_bucket_tagging(Bucket=name).get("TagSet", []))
            _add(graph, f"s3://{name}", f"arn:aws:s3:::{name}", "S3", name, "global", account_id, tags, CreationDate=str(bucket.get("CreationDate", "")))
    except Exception as exc:
        graph.warnings.append(f"S3 discovery incomplete: {exc}")

    route53 = session.client("route53", config=_config())
    try:
        for zone in _paginate(route53, "list_hosted_zones", "HostedZones"):
            zone_id = zone.get("Id", "").split("/")[-1]
            resource_id = f"route53:{zone_id}"
            arn = f"arn:aws:route53:::hostedzone/{zone_id}"
            tags = _safe_tags(lambda arn=arn: route53.list_tags_for_resource(ResourceType="hostedzone", ResourceId=zone_id).get("ResourceTagSet", {}).get("Tags", []))
            _add(graph, resource_id, arn, "Route53HostedZone", zone.get("Name", zone_id), "global", account_id, tags, PrivateZone=zone.get("Config", {}).get("PrivateZone", False))
    except Exception as exc:
        graph.warnings.append(f"Route53 discovery incomplete: {exc}")
