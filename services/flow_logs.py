"""VPC Flow Log discovery and compact CloudWatch Logs Insights reporting."""
from __future__ import annotations

import ipaddress
import time
import gzip
import io
import re
from datetime import datetime, timezone

from aws.session import get_boto3_session, get_ec2_client
from utils.logging import get_logger


DEFAULT_FLOW_FORMAT = "${version} ${account-id} ${interface-id} ${srcaddr} ${dstaddr} ${srcport} ${dstport} ${protocol} ${packets} ${bytes} ${start} ${end} ${action} ${log-status}"
REQUIRED_FIELDS = {"srcaddr", "dstaddr", "srcport", "dstport", "protocol", "action", "log-status"}
MAX_S3_OBJECTS = 2500
logger = get_logger("services.flow_logs")


def discover_flow_logs(profile: str, region: str) -> list[dict]:
    client = get_ec2_client(profile, region)
    logs = []
    for page in client.get_paginator("describe_flow_logs").paginate():
        for item in page.get("FlowLogs", []):
            resource = item.get("ResourceId", "")
            destination = item.get("LogDestinationType", "cloud-watch-logs")
            log_group = item.get("LogGroupName", "")
            logs.append({
                "FlowLogId": item.get("FlowLogId", ""), "ResourceId": resource, "VpcId": resource if resource.startswith("vpc-") else "",
                "DestinationType": destination, "LogGroupName": log_group,
                "Status": item.get("DeliverLogsStatus", ""), "TrafficType": item.get("TrafficType", ""),
                "LogFormat": item.get("LogFormat") or DEFAULT_FLOW_FORMAT,
                "LogDestination": item.get("LogDestination", ""),
                "DestinationOptions": item.get("DestinationOptions", {}),
            })
    return sorted(logs, key=lambda item: (item["ResourceId"], item["FlowLogId"]))


def discover_vpc_flow_inventory(profile: str, region: str) -> dict:
    """Return selectable VPC/interface/resource inventory and its flow logs."""
    ec2 = get_ec2_client(profile, region)
    interfaces, instance_names = [], {}
    for page in ec2.get_paginator("describe_network_interfaces").paginate():
        for eni in page.get("NetworkInterfaces", []):
            attachment = eni.get("Attachment", {})
            instance_id = attachment.get("InstanceId", "")
            if instance_id:
                instance_names[instance_id] = instance_id
            private_ips = [address.get("PrivateIpAddress", "") for address in eni.get("PrivateIpAddresses", [])]
            primary_ip = eni.get("PrivateIpAddress", "")
            if primary_ip and primary_ip not in private_ips:
                private_ips.insert(0, primary_ip)
            interfaces.append({"VpcId": eni.get("VpcId", ""), "SubnetId": eni.get("SubnetId", ""), "InterfaceId": eni.get("NetworkInterfaceId", ""), "PrivateIp": primary_ip, "PrivateIps": private_ips, "Resource": instance_id or eni.get("Description", "") or eni.get("InterfaceType", "ENI")})
    if instance_names:
        for page in ec2.get_paginator("describe_instances").paginate(InstanceIds=list(instance_names)):
            for reservation in page.get("Reservations", []):
                for instance in reservation.get("Instances", []):
                    name = next((tag["Value"] for tag in instance.get("Tags", []) if tag.get("Key") == "Name"), "")
                    instance_names[instance["InstanceId"]] = f"{name} ({instance['InstanceId']})" if name else instance["InstanceId"]
        for item in interfaces:
            item["Resource"] = instance_names.get(item["Resource"], item["Resource"])
    vpcs = []
    for page in ec2.get_paginator("describe_vpcs").paginate():
        for item in page.get("Vpcs", []):
            name = next(
                (tag.get("Value", "").strip() for tag in item.get("Tags", []) if tag.get("Key") == "Name"),
                "",
            )
            vpcs.append({
                "VpcId": item.get("VpcId", ""),
                "Name": name,
                "CidrBlocks": [
                    association.get("CidrBlock")
                    for association in item.get("CidrBlockAssociationSet", [])
                    if association.get("CidrBlock")
                ],
            })
    interface_vpcs = {item["InterfaceId"]: item["VpcId"] for item in interfaces if item.get("InterfaceId") and item.get("VpcId")}
    subnet_vpcs = {}
    for page in ec2.get_paginator("describe_subnets").paginate():
        for subnet in page.get("Subnets", []):
            subnet_vpcs[subnet.get("SubnetId", "")] = subnet.get("VpcId", "")
    all_flow_logs = discover_flow_logs(profile, region)
    for flow_log in all_flow_logs:
        resource_id = flow_log.get("ResourceId", "")
        if resource_id.startswith("eni-"):
            flow_log["VpcId"] = interface_vpcs.get(resource_id, "")
        elif resource_id.startswith("subnet-"):
            flow_log["VpcId"] = subnet_vpcs.get(resource_id, "")
        if flow_log.get("VpcId"):
            flow_log["Scope"] = "VPC" if resource_id.startswith("vpc-") else "INTERFACE" if resource_id.startswith("eni-") else "SUBNET"
    flow_logs = [item for item in all_flow_logs if item.get("VpcId")]
    logger.info(
        "Flow Log inventory discovered | vpcs=%d interfaces=%d flow_logs=%d destinations=%s",
        len(vpcs), len(interfaces), len(flow_logs),
        sorted({str(item.get("DestinationType", "unknown")) for item in flow_logs}),
    )
    return {"vpcs": sorted(vpcs, key=lambda item: item["VpcId"]), "interfaces": interfaces, "flow_logs": flow_logs}


def _s3_destination(destination: str) -> tuple[str, str]:
    match = re.match(r"arn:[^:]+:s3:::(?P<bucket>[^/]+)(?:/(?P<prefix>.*))?$", destination or "")
    if not match:
        raise RuntimeError("The Flow Log S3 destination ARN is unavailable or invalid.")
    return match.group("bucket"), (match.group("prefix") or "").strip("/")


def _s3_prefixes(prefix: str, account_id: str, region: str, start: datetime, end: datetime, hive: bool) -> list[str]:
    base = f"{prefix}/" if prefix else ""
    prefixes = []
    day = start.date()
    while day <= end.date():
        date = day.strftime("%Y/%m/%d")
        if hive:
            suffix = f"AWSLogs/aws-account-id={account_id}/aws-service=vpcflowlogs/aws-region={region}/year={day:%Y}/month={day:%m}/day={day:%d}/"
        else:
            suffix = f"AWSLogs/{account_id}/vpcflowlogs/{region}/{date}/"
        prefixes.append(base + suffix)
        day = day.fromordinal(day.toordinal() + 1)
    return prefixes


def _format_fields(log_format: str) -> list[str]:
    fields = [part[2:-1] for part in log_format.split() if part.startswith("${") and part.endswith("}")]
    if not REQUIRED_FIELDS.issubset(fields):
        raise RuntimeError("The Flow Log format does not contain source/destination, port, protocol, action and log status fields.")
    return fields


def analyse_s3_flow_log(profile: str, region: str, flow_log: dict, interface_id: str, start: datetime, end: datetime) -> dict:
    """Stream text/Gzip VPC Flow Logs from S3; Athena tables are never required."""
    if flow_log.get("DestinationType") != "s3":
        raise RuntimeError("The selected Flow Log is not delivered to S3.")
    if (flow_log.get("DestinationOptions") or {}).get("FileFormat") == "parquet":
        raise RuntimeError("This S3 Flow Log uses Parquet. Direct Parquet support is not enabled in this installation yet.")
    if not interface_id or end <= start:
        raise ValueError("Select an interface and a valid date range.")
    fields = _format_fields(flow_log.get("LogFormat") or DEFAULT_FLOW_FORMAT)
    session = get_boto3_session(profile, region)
    account_id = session.client("sts").get_caller_identity().get("Account", "")
    bucket, prefix = _s3_destination(flow_log.get("LogDestination", ""))
    s3 = session.client("s3")
    hive = bool((flow_log.get("DestinationOptions") or {}).get("HiveCompatiblePartitions"))
    keys = []
    for key_prefix in _s3_prefixes(prefix, account_id, region, start, end, hive):
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=key_prefix):
            for item in page.get("Contents", []):
                key = item["Key"]
                if f"_{flow_log['FlowLogId']}_" in key and key.endswith(".log.gz"):
                    keys.append(key)
                    if len(keys) > MAX_S3_OBJECTS:
                        raise RuntimeError(f"More than {MAX_S3_OBJECTS} Flow Log files match this period. Reduce the selected date range.")
    networks = _vpc_networks(profile, region, flow_log["VpcId"])
    grouped, observations = {}, 0
    start_epoch, end_epoch = int(start.replace(tzinfo=timezone.utc).timestamp()), int(end.replace(tzinfo=timezone.utc).timestamp())
    for key in keys:
        body = s3.get_object(Bucket=bucket, Key=key)["Body"]
        with gzip.GzipFile(fileobj=body) as compressed, io.TextIOWrapper(compressed, encoding="utf-8", errors="replace") as lines:
            for line in lines:
                values = line.split()
                if len(values) < len(fields):
                    continue
                record = dict(zip(fields, values))
                if record.get("interface-id") != interface_id or record.get("log-status") != "OK" or record.get("action") not in {"ACCEPT", "REJECT"}:
                    continue
                try:
                    if not start_epoch <= int(record.get("end", "0")) <= end_epoch:
                        continue
                except ValueError:
                    continue
                key_tuple = (record["srcaddr"], record["dstaddr"], record["protocol"], record["dstport"], record["action"])
                current = grouped.setdefault(key_tuple, {"Origin": key_tuple[0], "Destination": key_tuple[1], "Protocol": key_tuple[2], "Port": key_tuple[3], "Result": key_tuple[4], "Direction": _direction(key_tuple[0], key_tuple[1], networks), "Observations": 0, "Bytes": 0})
                current["Observations"] += 1
                try: current["Bytes"] += int(record.get("bytes", "0"))
                except ValueError: pass
                observations += 1
    rows = sorted(grouped.values(), key=lambda row: row["Observations"], reverse=True)
    return {"rows": rows, "observations": observations, "vpc_id": flow_log["VpcId"], "log_group": f"s3://{bucket}/{prefix}", "objects": len(keys)}


def _insights_parse_expression(log_format: str) -> str:
    fields = [part[2:-1] for part in log_format.split() if part.startswith("${") and part.endswith("}")]
    if not REQUIRED_FIELDS.issubset(fields):
        missing = ", ".join(sorted(REQUIRED_FIELDS - set(fields)))
        raise RuntimeError(f"The Flow Log format does not contain the required fields: {missing}.")
    # CloudWatch Logs Insights can auto-discover fields such as ``bytes``.
    # Named captures cannot redefine those ephemeral fields, so parser fields
    # always use an application-owned prefix.
    captures = "\\s+".join(f"(?<flow_{field.replace('-', '_')}>\\S+)" for field in fields)
    # Logs Insights commands following ``fields`` must be pipe-separated.  In
    # particular, a bare ``parse`` at the start of a second line raises
    # MalformedQueryException ("unexpected symbol" at line 2, position 0).
    return f"| parse @message /{captures}/"


def build_insights_query(log_format: str, interface_id: str = "", limit: int = 10000, aggregation: str = "service") -> str:
    parse = _insights_parse_expression(log_format)
    interface_filter = f" and flow_interface_id = '{interface_id}'" if interface_id else ""
    if aggregation == "endpoint":
        stats = "| stats count(*) as observations, sum(flow_bytes) as total_bytes, count_distinct(flow_dstport) as port_count by flow_srcaddr, flow_dstaddr, flow_protocol, flow_action"
    else:
        # Keep both ports.  The application can then collapse the two halves
        # of a client/server conversation without treating client ephemeral
        # ports as distinct application services.
        stats = "| stats count(*) as observations, sum(flow_bytes) as total_bytes by flow_srcaddr, flow_dstaddr, flow_srcport, flow_dstport, flow_protocol, flow_action"
    return "\n".join([
        "fields @message",
        parse,
        f"| filter flow_log_status = 'OK' and flow_action in ['ACCEPT', 'REJECT']{interface_filter}",
        stats,
        "| sort observations desc",
        f"| limit {max(1, min(int(limit), 10000))}",
    ])


def _vpc_networks(profile: str, region: str, vpc_id: str) -> list[ipaddress._BaseNetwork]:
    response = get_ec2_client(profile, region).describe_vpcs(VpcIds=[vpc_id])
    vpc = response.get("Vpcs", [{}])[0]
    cidrs = [item.get("CidrBlock") for item in vpc.get("CidrBlockAssociationSet", [])]
    if vpc.get("CidrBlock"):
        cidrs.append(vpc["CidrBlock"])
    return [ipaddress.ip_network(cidr, strict=False) for cidr in cidrs if cidr]


def _inside(address: str, networks) -> bool:
    try:
        value = ipaddress.ip_address(address)
        return any(value in network for network in networks)
    except ValueError:
        return False


def _direction(source: str, destination: str, networks, interface_ips=()) -> str:
    """Classify traffic from the selected ENI's perspective when possible."""
    selected = {address for address in interface_ips if address}
    if source in selected and destination not in selected:
        return "OUTBOUND"
    if destination in selected and source not in selected:
        return "INBOUND"
    source_inside, destination_inside = _inside(source, networks), _inside(destination, networks)
    if source_inside and not destination_inside:
        return "OUTBOUND"
    if destination_inside and not source_inside:
        return "INBOUND"
    return "INTERNAL" if source_inside else "TRANSIT"


def _integer(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _is_ephemeral(port, start, end):
    value = _integer(port, -1)
    return start <= value <= end


def _rows(results: list[list[dict]], networks, interface_ips=(), normalise_ports=False, ephemeral_start=32768, ephemeral_end=65535) -> tuple[list[dict], int]:
    rows, observations = [], 0
    normalised = {}
    selected = {address for address in interface_ips if address}
    for result in results:
        item = {field["field"]: field.get("value", "") for field in result}
        # Accept collision-safe aliases from the current query and historical
        # field names so already-exported query output remains readable.
        def value(name: str, default: str = ""):
            return item.get(f"flow_{name}", item.get(name, default))

        source, destination = value("srcaddr"), value("dstaddr")
        if not source or not destination:
            continue
        count = int(item.get("observations", "0") or 0)
        observations += count
        port = value("dstport")
        if not port and item.get("port_count"):
            port = f"MULTI ({item['port_count']})"
        if not normalise_ports and value("srcport") and value("dstport"):
            port = f"{value('srcport')} → {value('dstport')}"
        raw_row = {
            "Origin": source, "Destination": destination, "Protocol": value("protocol"),
            "Port": port, "Result": value("action"),
            "Direction": _direction(source, destination, networks, interface_ips),
            "Scope": "INTERNAL" if _inside(source, networks) and _inside(destination, networks) else "EXTERNAL",
            "Observations": count,
            "Bytes": _integer(item.get("total_bytes", item.get("bytes", "0"))),
            "RawGroups": 1,
        }
        if not normalise_ports or not selected or not value("srcport") or not value("dstport"):
            rows.append(raw_row)
            continue

        src_port, dst_port = value("srcport"), value("dstport")
        local_source, local_destination = source in selected, destination in selected
        if local_destination:
            origin, destination_value, service_port, direction = source, destination, dst_port, "INBOUND"
        elif local_source and not _is_ephemeral(src_port, ephemeral_start, ephemeral_end) and _is_ephemeral(dst_port, ephemeral_start, ephemeral_end):
            # A local listening port replying to a client ephemeral port is
            # the return half of an inbound conversation.
            origin, destination_value, service_port, direction = destination, source, src_port, "INBOUND"
        elif local_source:
            origin, destination_value, service_port, direction = source, destination, dst_port, "OUTBOUND"
        else:
            origin, destination_value, service_port, direction = source, destination, dst_port, raw_row["Direction"]
        scope = "INTERNAL" if _inside(origin, networks) and _inside(destination_value, networks) else "EXTERNAL"
        key = (origin, destination_value, value("protocol"), str(service_port), value("action"), direction, scope)
        target = normalised.setdefault(key, {
            "Origin": origin, "Destination": destination_value, "Protocol": value("protocol"),
            "Port": str(service_port), "Result": item.get("action", ""), "Direction": direction,
            "Scope": scope, "Observations": 0, "Bytes": 0, "RawGroups": 0,
        })
        target["Observations"] += count
        target["Bytes"] += _integer(item.get("total_bytes", item.get("bytes", "0")))
        target["RawGroups"] += 1
    if normalise_ports:
        rows.extend(normalised.values())
    return rows, observations


def analyse_flow_log(profile: str, region: str, flow_log: dict, start: datetime, end: datetime, interface_id: str = "", interface_ips=(), aggregation: str = "service", normalise_ports=True, ephemeral_start=32768, ephemeral_end=65535) -> dict:
    if flow_log.get("DestinationType") != "cloud-watch-logs" or not flow_log.get("LogGroupName"):
        raise RuntimeError("This Flow Log is not delivered to CloudWatch Logs and cannot be queried from the application.")
    if end <= start:
        raise ValueError("The end date must be later than the start date.")
    logs = get_boto3_session(profile, region).client("logs")
    query_id = logs.start_query(
        logGroupName=flow_log["LogGroupName"], startTime=int(start.replace(tzinfo=timezone.utc).timestamp()),
        endTime=int(end.replace(tzinfo=timezone.utc).timestamp()), queryString=build_insights_query(flow_log.get("LogFormat") or DEFAULT_FLOW_FORMAT, interface_id, aggregation=aggregation),
    )["queryId"]
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        response = logs.get_query_results(queryId=query_id)
        if response.get("status") == "Complete":
            networks = _vpc_networks(profile, region, flow_log["VpcId"])
            rows, observations = _rows(response.get("results", []), networks, interface_ips, normalise_ports and aggregation == "service", ephemeral_start, ephemeral_end)
            return {"rows": rows, "observations": observations, "raw_groups": len(response.get("results", [])), "vpc_id": flow_log["VpcId"], "log_group": flow_log["LogGroupName"], "normalised": normalise_ports and aggregation == "service"}
        if response.get("status") in {"Failed", "Cancelled", "Timeout", "Unknown"}:
            raise RuntimeError(f"CloudWatch Logs Insights query ended with status {response.get('status')}.")
        time.sleep(1)
    logs.stop_query(queryId=query_id)
    raise RuntimeError("CloudWatch Logs Insights query exceeded the 120 second application limit.")
