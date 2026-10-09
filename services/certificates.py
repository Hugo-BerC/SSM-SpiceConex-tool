from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.config import Config

from app.config import AWS_TIMEOUT_SECONDS
from utils.logging import get_logger

logger = get_logger("services.certificates")

CERTIFICATE_STATUSES = (
    "PENDING_VALIDATION",
    "ISSUED",
    "INACTIVE",
    "EXPIRED",
    "VALIDATION_TIMED_OUT",
    "REVOKED",
    "FAILED",
)
TLS_PROTOCOLS = {"HTTPS", "TLS"}
LOAD_BALANCER_TYPES = {"application", "network"}

CERTIFICATE_COLUMNS = [
    "Account",
    "AccountId",
    "Region",
    "Domain",
    "SANs",
    "Status",
    "NotAfter",
    "Expiry",
    "Type",
    "InUse",
    "Resources",
]

BALANCER_COLUMNS = [
    "Account",
    "AccountId",
    "Region",
    "Type",
    "LoadBalancer",
    "Scheme",
    "Listener",
    "Protocol",
    "Port",
    "Rule",
    "Host",
    "Path",
    "Certificate",
    "CertStatus",
    "Expiry",
    "NotAfter",
    "Match",
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
    if not value:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def expiry_bucket(value: Any) -> str:
    """Return the same operational expiry buckets used by the model report."""
    if not value:
        return "N/A"
    if not isinstance(value, datetime):
        try:
            value = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return "N/A"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    days = (value - now).total_seconds() / 86400
    if days < 0:
        return "EXPIRED"
    if days <= 90:
        return "0-3M"
    if days <= 180:
        return "3-6M"
    return "6M+"


def certificate_scope(certificate_type: str) -> str:
    return {
        "AMAZON_ISSUED": "Public",
        "PRIVATE": "Private",
        "IMPORTED": "Imported",
    }.get(certificate_type, "Unknown")


def hostname_matches(host: str, certificate_name: str) -> bool:
    """Match an exact DNS name or an AWS-style wildcard for one label."""
    host = (host or "").strip().rstrip(".").lower()
    certificate_name = (certificate_name or "").strip().rstrip(".").lower()
    if not host or not certificate_name:
        return False
    if host == certificate_name:
        return True
    if not certificate_name.startswith("*."):
        return False
    suffix = certificate_name[1:]
    return host.endswith(suffix) and host.count(".") == certificate_name.count(".")


def certificate_matches_host(certificate: dict, host: str) -> bool:
    names = [certificate.get("DomainName", "")]
    names.extend(certificate.get("SubjectAlternativeNames", []))
    return any(hostname_matches(host, name) for name in names if name)


def _listener_certificates(client, listener: dict) -> list[str]:
    arns = {
        item.get("CertificateArn")
        for item in listener.get("Certificates", [])
        if item.get("CertificateArn")
    }
    paginator = client.get_paginator("describe_listener_certificates")
    for page in paginator.paginate(ListenerArn=listener["ListenerArn"]):
        arns.update(
            item.get("CertificateArn")
            for item in page.get("Certificates", [])
            if item.get("CertificateArn")
        )
    return sorted(arn for arn in arns if arn)


def _listener_rules(client, listener_arn: str) -> list[dict]:
    rules = []
    paginator = client.get_paginator("describe_rules")
    for page in paginator.paginate(ListenerArn=listener_arn):
        for item in page.get("Rules", []):
            host_headers = []
            path_patterns = []
            for condition in item.get("Conditions", []):
                field = condition.get("Field")
                values = condition.get("Values", [])
                if field == "host-header":
                    config = condition.get("HostHeaderConfig", {})
                    host_headers.extend(config.get("Values", values))
                elif field == "path-pattern":
                    config = condition.get("PathPatternConfig", {})
                    path_patterns.extend(config.get("Values", values))
            rules.append(
                {
                    "RuleArn": item.get("RuleArn", ""),
                    "Priority": item.get("Priority", ""),
                    "HostHeaders": host_headers,
                    "PathPatterns": path_patterns,
                }
            )
    return rules


def _empty_rule() -> dict:
    return {"RuleArn": "", "Priority": "", "HostHeaders": [], "PathPatterns": []}


def discover_certificate_inventory(profile: str, region: str) -> dict:
    """Discover ACM certificates and their current ALB/NLB TLS associations.

    The function intentionally creates its own boto3 session inside the worker so
    the discovery does not share a session/client created by the Terminal UI.
    """
    profile = profile.strip()
    region = region.strip()
    if not profile:
        raise ValueError("No AWS profile is selected.")
    if not region:
        raise ValueError("No AWS region is selected in Terminal.")

    session = boto3.Session(profile_name=profile, region_name=region)
    sts = session.client("sts", config=_client_config())
    identity = sts.get_caller_identity()
    account_id = identity.get("Account", "")

    acm = session.client("acm", region_name=region, config=_client_config())
    certificates: list[dict] = []
    certificate_by_arn: dict[str, dict] = {}

    paginator = acm.get_paginator("list_certificates")
    for page in paginator.paginate(CertificateStatuses=list(CERTIFICATE_STATUSES)):
        for summary in page.get("CertificateSummaryList", []):
            arn = summary.get("CertificateArn")
            if not arn:
                continue
            try:
                detail = acm.describe_certificate(CertificateArn=arn)["Certificate"]
            except Exception as exc:
                logger.warning("Unable to describe certificate %s: %s", arn, exc)
                continue

            names = detail.get("SubjectAlternativeNames", [])
            in_use_by = detail.get("InUseBy", [])
            row = {
                "Account": profile,
                "AccountId": account_id,
                "Region": region,
                "Domain": detail.get("DomainName", ""),
                "SANs": " | ".join(names),
                "Status": detail.get("Status", ""),
                "NotAfter": _format_datetime(detail.get("NotAfter")),
                "Expiry": expiry_bucket(detail.get("NotAfter")),
                "Type": detail.get("Type", ""),
                "CertificateScope": certificate_scope(detail.get("Type", "")),
                "InUse": bool(in_use_by),
                "Resources": " | ".join(in_use_by),
                "ResourceCount": len(in_use_by),
                "CertificateArn": arn,
                "InUseByList": in_use_by,
                "SubjectAlternativeNames": names,
                "NotAfterValue": detail.get("NotAfter"),
            }
            certificates.append(row)
            certificate_by_arn[arn] = row

    balancers: list[dict] = []
    elbv2 = session.client("elbv2", region_name=region, config=_client_config())
    lb_paginator = elbv2.get_paginator("describe_load_balancers")

    for page in lb_paginator.paginate():
        for load_balancer in page.get("LoadBalancers", []):
            lb_type = load_balancer.get("Type", "")
            if lb_type not in LOAD_BALANCER_TYPES:
                continue
            try:
                listeners = []
                listener_paginator = elbv2.get_paginator("describe_listeners")
                for listener_page in listener_paginator.paginate(
                    LoadBalancerArn=load_balancer["LoadBalancerArn"]
                ):
                    listeners.extend(listener_page.get("Listeners", []))
            except Exception as exc:
                logger.warning(
                    "Unable to list listeners for %s: %s",
                    load_balancer.get("LoadBalancerName", ""),
                    exc,
                )
                continue

            for listener in listeners:
                protocol = listener.get("Protocol", "")
                if protocol not in TLS_PROTOCOLS:
                    continue
                cert_arns = [
                    arn for arn in _listener_certificates(elbv2, listener)
                    if arn in certificate_by_arn
                ]
                if not cert_arns:
                    continue

                rules = _listener_rules(elbv2, listener["ListenerArn"]) if lb_type == "application" else []
                for cert_arn in cert_arns:
                    certificate = certificate_by_arn[cert_arn]
                    matching_rows = []
                    no_host_rules = []
                    for rule in rules:
                        hosts = rule["HostHeaders"]
                        if not hosts:
                            no_host_rules.append(rule)
                            continue
                        matched_hosts = [
                            host for host in hosts
                            if certificate_matches_host(certificate, host)
                        ]
                        if matched_hosts:
                            matching_rows.append((rule, matched_hosts))

                    if matching_rows:
                        for rule, matched_hosts in matching_rows:
                            balancers.append(
                                _build_balancer_row(
                                    profile, account_id, region, load_balancer,
                                    listener, certificate, cert_arn, rule,
                                    "MATCH", matched_hosts,
                                )
                            )
                    elif no_host_rules:
                        balancers.append(
                            _build_balancer_row(
                                profile, account_id, region, load_balancer,
                                listener, certificate, cert_arn, no_host_rules[0],
                                "LISTENER", [],
                            )
                        )
                    else:
                        rule = rules[0] if rules else _empty_rule()
                        balancers.append(
                            _build_balancer_row(
                                profile, account_id, region, load_balancer,
                                listener, certificate, cert_arn, rule,
                                "MISMATCH" if lb_type == "application" else "LISTENER", [],
                            )
                        )

    for certificate in certificates:
        resource_rows = [
            row for row in balancers if row["CertificateArn"] == certificate["CertificateArn"]
        ]
        if resource_rows:
            certificate["ELBState"] = "MATCHED"
        elif certificate["InUse"]:
            certificate["ELBState"] = "LISTENER"
        else:
            certificate["ELBState"] = "NO_ELB"
        certificate["ELBResourceCount"] = len(resource_rows)

    logger.info(
        "Certificate discovery complete | profile=%s | account=%s | region=%s | certificates=%d | ALB/NLB rows=%d",
        profile, account_id, region, len(certificates), len(balancers),
    )
    return {
        "account_id": account_id,
        "profile": profile,
        "region": region,
        "certificates": certificates,
        "balancers": balancers,
    }


def _build_balancer_row(
    profile: str,
    account_id: str,
    region: str,
    load_balancer: dict,
    listener: dict,
    certificate: dict,
    certificate_arn: str,
    rule: dict,
    match: str,
    matched_hosts: list[str],
) -> dict:
    return {
        "Account": profile,
        "AccountId": account_id,
        "Region": region,
        "Type": load_balancer.get("Type", "").upper(),
        "LoadBalancer": load_balancer.get("LoadBalancerName", ""),
        "LoadBalancerArn": load_balancer.get("LoadBalancerArn", ""),
        "Scheme": load_balancer.get("Scheme", ""),
        "Listener": f"{listener.get('Protocol', '')}:{listener.get('Port', '')}",
        "Protocol": listener.get("Protocol", ""),
        "Port": listener.get("Port", ""),
        "ListenerArn": listener.get("ListenerArn", ""),
        "Rule": rule.get("Priority", ""),
        "RuleArn": rule.get("RuleArn", ""),
        "Host": " | ".join(rule.get("HostHeaders", [])),
        "MatchedHost": " | ".join(matched_hosts),
        "Path": " | ".join(rule.get("PathPatterns", [])),
        "Certificate": certificate.get("Domain", "") or certificate_arn,
        "CertificateArn": certificate_arn,
        "CertStatus": certificate.get("Status", ""),
        "Expiry": certificate.get("Expiry", ""),
        "NotAfter": certificate.get("NotAfter", ""),
        "Match": match,
    }
