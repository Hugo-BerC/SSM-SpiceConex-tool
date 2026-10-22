"""CloudFront distribution inventory for the Edge & Security workspace."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import boto3
from botocore.config import Config

from app.config import AWS_TIMEOUT_SECONDS
from utils.logging import get_logger


logger = get_logger("services.cloudfront")


CLOUDFRONT_COLUMNS = [
    "Distribution ID",
    "Domain Name",
    "Aliases",
    "Status",
    "Enabled",
    "Origins",
    "Default Origin",
    "Behaviors",
    "Viewer Protocol",
    "HTTP Version",
    "WAF",
    "IPv6",
    "Price Class",
    "Last Modified",
]


def _client_config() -> Config:
    timeout = max(5, AWS_TIMEOUT_SECONDS)
    return Config(
        connect_timeout=min(5, timeout),
        read_timeout=timeout,
        retries={"mode": "standard", "max_attempts": 1},
        tcp_keepalive=True,
    )


def _format_datetime(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    return str(value or "")


def _origin_summary(distribution: dict) -> tuple[str, str]:
    origins = (distribution.get("Origins") or {}).get("Items") or []
    by_id = {str(origin.get("Id", "")): origin for origin in origins}
    default_id = str((distribution.get("DefaultCacheBehavior") or {}).get("TargetOriginId", ""))
    default = by_id.get(default_id, {})
    target = str(default.get("DomainName", ""))
    if default_id and target:
        target = f"{default_id} · {target}"
    return str(len(origins)), target or default_id


def discover_distributions(profile: str, region: str) -> dict:
    """Discover all CloudFront distributions visible to the selected profile.

    CloudFront is global; ``region`` is retained for the visible application
    context and for a consistent profile/session construction.
    """
    profile = profile.strip()
    region = region.strip()
    if not profile:
        raise ValueError("No AWS profile is selected.")
    if not region:
        raise ValueError("No AWS region is selected in Terminal.")

    session = boto3.Session(profile_name=profile, region_name=region)
    cloudfront = session.client("cloudfront", config=_client_config())
    distributions: list[dict] = []
    paginator = cloudfront.get_paginator("list_distributions")
    for page in paginator.paginate():
        items = (page.get("DistributionList") or {}).get("Items") or []
        for distribution in items:
            aliases = (distribution.get("Aliases") or {}).get("Items") or []
            behaviors = (distribution.get("CacheBehaviors") or {}).get("Quantity", 0)
            origins, default_origin = _origin_summary(distribution)
            web_acl_id = str(distribution.get("WebACLId", "") or "")
            distributions.append(
                {
                    "DistributionId": str(distribution.get("Id", "")),
                    "DomainName": str(distribution.get("DomainName", "")),
                    "Aliases": " | ".join(str(alias) for alias in aliases),
                    "Status": str(distribution.get("Status", "")),
                    "Enabled": bool(distribution.get("Enabled", False)),
                    "Origins": origins,
                    "DefaultOrigin": default_origin,
                    "Behaviors": str(behaviors),
                    "ViewerProtocol": str((distribution.get("DefaultCacheBehavior") or {}).get("ViewerProtocolPolicy", "")),
                    "HTTPVersion": str(distribution.get("HttpVersion", "")),
                    "WAF": "Enabled" if web_acl_id else "None",
                    "WebACLId": web_acl_id,
                    "IPv6": bool(distribution.get("IsIPV6Enabled", False)),
                    "PriceClass": str(distribution.get("PriceClass", "")),
                    "LastModified": _format_datetime(distribution.get("LastModifiedTime")),
                    "Arn": str(distribution.get("ARN", "")),
                }
            )
    distributions.sort(key=lambda item: (item["DomainName"].casefold(), item["DistributionId"]))
    logger.info(
        "CloudFront distributions discovered | profile=%r | distributions=%d",
        profile,
        len(distributions),
    )
    return {"profile": profile, "region": region, "distributions": distributions}
