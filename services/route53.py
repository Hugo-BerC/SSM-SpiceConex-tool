"""Private Route 53 hosted-zone discovery for the Edge & Security workspace."""

from __future__ import annotations

from typing import Any

import boto3
from botocore.config import Config

from app.config import AWS_TIMEOUT_SECONDS
from utils.logging import get_logger


logger = get_logger("services.route53")


ROUTE53_RECORD_COLUMNS = [
    "Name",
    "Type",
    "TTL",
    "Value / Target",
    "Routing",
    "Identifier",
    "Health Check",
]


def _client_config() -> Config:
    timeout = max(5, AWS_TIMEOUT_SECONDS)
    return Config(
        connect_timeout=min(5, timeout),
        read_timeout=timeout,
        retries={"mode": "standard", "max_attempts": 1},
        tcp_keepalive=True,
    )


def _zone_id(value: Any) -> str:
    """Normalise Route 53's /hostedzone/Z... identifier."""
    return str(value or "").rstrip("/").rsplit("/", 1)[-1]


def discover_private_hosted_zones(profile: str, region: str) -> dict:
    """Return private hosted zones visible to the selected AWS profile.

    Route 53 is a global API.  The Terminal region remains part of the returned
    context so an operator can see which connected AWS context was used, while
    the hosted-zone query itself is account scoped.
    """
    profile = profile.strip()
    region = region.strip()
    if not profile:
        raise ValueError("No AWS profile is selected.")
    if not region:
        raise ValueError("No AWS region is selected in Terminal.")

    session = boto3.Session(profile_name=profile, region_name=region)
    route53 = session.client("route53", config=_client_config())
    zones: list[dict] = []
    paginator = route53.get_paginator("list_hosted_zones")
    for page in paginator.paginate():
        for zone in page.get("HostedZones", []):
            config = zone.get("Config") or {}
            if not config.get("PrivateZone", False):
                continue
            zone_id = _zone_id(zone.get("Id"))
            if not zone_id:
                continue
            zones.append(
                {
                    "Id": zone_id,
                    "Name": str(zone.get("Name", "")).rstrip("."),
                    "RecordSetCount": int(zone.get("ResourceRecordSetCount", 0) or 0),
                    "Comment": str(config.get("Comment", "") or ""),
                    "PrivateZone": True,
                }
            )
    zones.sort(key=lambda item: (item["Name"].casefold(), item["Id"]))
    logger.info(
        "Private Route 53 zones discovered | profile=%r | zones=%d",
        profile,
        len(zones),
    )
    return {"profile": profile, "region": region, "zones": zones}


def _routing_description(record: dict) -> str:
    parts: list[str] = []
    if record.get("Failover"):
        parts.append(f"Failover: {record['Failover']}")
    if record.get("Region"):
        parts.append(f"Latency: {record['Region']}")
    if "Weight" in record:
        parts.append(f"Weighted: {record['Weight']}")
    geo = record.get("GeoLocation") or {}
    if geo:
        location = geo.get("SubdivisionCode") or geo.get("CountryCode") or geo.get("ContinentCode") or "Default"
        parts.append(f"Geolocation: {location}")
    cidr = record.get("CidrRoutingConfig") or {}
    if cidr.get("CollectionId"):
        parts.append(f"CIDR: {cidr['CollectionId']}")
    if record.get("MultiValueAnswer"):
        parts.append("Multivalue")
    return " | ".join(parts) if parts else "Simple"


def _record_value(record: dict) -> str:
    alias = record.get("AliasTarget") or {}
    if alias:
        target = str(alias.get("DNSName", "")).rstrip(".")
        if alias.get("EvaluateTargetHealth"):
            return f"Alias: {target} (evaluate target health)"
        return f"Alias: {target}"
    return " | ".join(
        str(value.get("Value", "")) for value in record.get("ResourceRecords", [])
        if value.get("Value") is not None
    )


def discover_hosted_zone_records(profile: str, region: str, hosted_zone_id: str) -> dict:
    """Return every record set for one selected private hosted zone."""
    profile = profile.strip()
    region = region.strip()
    hosted_zone_id = _zone_id(hosted_zone_id)
    if not profile:
        raise ValueError("No AWS profile is selected.")
    if not region:
        raise ValueError("No AWS region is selected in Terminal.")
    if not hosted_zone_id:
        raise ValueError("No private hosted zone is selected.")

    session = boto3.Session(profile_name=profile, region_name=region)
    route53 = session.client("route53", config=_client_config())
    records: list[dict] = []
    paginator = route53.get_paginator("list_resource_record_sets")
    for page in paginator.paginate(HostedZoneId=hosted_zone_id):
        for record in page.get("ResourceRecordSets", []):
            records.append(
                {
                    "Name": str(record.get("Name", "")).rstrip("."),
                    "Type": str(record.get("Type", "")),
                    "TTL": "" if record.get("AliasTarget") else str(record.get("TTL", "")),
                    "ValueTarget": _record_value(record),
                    "Routing": _routing_description(record),
                    "Identifier": str(record.get("SetIdentifier", "")),
                    "HealthCheck": str(record.get("HealthCheckId", "")),
                    "HostedZoneId": hosted_zone_id,
                }
            )
    records.sort(
        key=lambda item: (
            item["Name"].casefold(),
            item["Type"],
            item["Identifier"].casefold(),
        )
    )
    logger.info(
        "Route 53 records discovered | profile=%r | zone=%s | records=%d",
        profile,
        hosted_zone_id,
        len(records),
    )
    return {
        "profile": profile,
        "region": region,
        "hosted_zone_id": hosted_zone_id,
        "records": records,
    }
