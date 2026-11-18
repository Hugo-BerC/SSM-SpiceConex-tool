"""Read-only, paginated AWS Config aggregator inventory and portable exports."""
from __future__ import annotations

import csv
import json
import os
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from threading import Event

from botocore.exceptions import ClientError

from aws.auth import is_session_unavailable
from aws.session import get_boto3_session, client_config
from aws.errors import classify_error, error_code
from utils.csv_safety import csv_safe_value


RESOURCE_COLUMNS = (
    "resource_key", "account_id", "account_name", "region", "resource_type",
    "resource_id", "resource_name", "arn", "config_capture_time",
    "inventory_export_time", "source", "configuration_status", "details_status",
)
# Shared presentation order for the table and exports. Original Config keys
# remain internal; ARNs are exposed only in explicitly named ARN fields.
INVENTORY_COLUMNS = (
    "resource_type", "resource_id", "resource_name", "account_id", "region",
    "account_name", "arn", "config_resource_arn", "config_capture_time",
    "inventory_export_time", "source", "configuration_status", "details_status",
)
COVERAGE_NOTE = ("Inventory contains resources exposed by this AWS Config aggregator. "
                 "It does not certify that all organization accounts, regions or AWS resource types are recorded. "
                 "Source authorization/update status does not establish per-resource freshness.")


class InventoryCancelled(Exception):
    """Cooperative cancellation between bounded AWS requests."""


def inventory_value(resource: dict, column: str):
    """Present ARN identifiers once without guessing service-specific short IDs.

    Some Config resource IDs are ARNs. If the detailed ARN is different (for
    example a Security Hub subscription versus a standard), both are meaningful
    and are presented in explicitly named ARN columns. Internal data is not changed.
    """
    identifier = str(resource.get("resource_id") or "")
    arn = str(resource.get("arn") or "")
    is_arn = identifier.startswith("arn:")
    if column == "resource_id":
        return "" if is_arn else identifier
    if column == "arn":
        return arn or (identifier if is_arn else "")
    if column == "config_resource_arn":
        return resource.get(column, "") or (identifier if is_arn and arn and identifier != arn else "")
    if column == "resource_name":
        return _resource_name(resource.get(column))
    return resource.get(column, "")


def _resource_name(value):
    """Never substitute an identifier/ARN for a missing name."""
    return value if isinstance(value, str) and value.strip() and not value.strip().startswith("arn:") else ""


def _name_tag(tags):
    if isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except (ValueError, TypeError):
            return ""
    if isinstance(tags, dict):
        return _resource_name(tags.get("Name"))
    if isinstance(tags, list):
        for tag in tags:
            if isinstance(tag, dict) and tag.get("key", tag.get("Key")) == "Name":
                return _resource_name(tag.get("value", tag.get("Value")))
    return ""


def _matching_rows(rows, by_resource, item):
    key = _identity(item, detail=True)
    if key in rows:
        return [rows[key]]
    # Config represents IAM and other global resources with awsRegion=global,
    # whereas aggregate identifiers can carry their recording/source region.
    # Update the observations of that exact account/type/ID without merging them.
    if key[1] == "global":
        return by_resource.get((key[0], key[2], key[3]), [])
    return []


def _timestamp(value=None):
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        value = datetime.fromtimestamp(value, timezone.utc)
    if isinstance(value, datetime):
        return value.replace(tzinfo=value.tzinfo or timezone.utc).astimezone(timezone.utc).isoformat()
    return str(value)


def _now():
    return _timestamp(datetime.now(timezone.utc))


def _error(exc):
    # Avoid copying arbitrary authentication/provider output into reports.
    return error_code(exc)


class _Reader:
    def __init__(self, profile, region, run_with_sso=None, cancel=None):
        if not profile.strip() or not region.strip():
            raise ValueError("Select an AWS profile and the aggregator region.")
        self.profile, self.region = profile.strip(), region.strip()
        self.recover = run_with_sso or (lambda _profile, operation: operation())
        self.cancel = cancel or Event()
        self.clients = {}

    def check(self):
        if self.cancel.is_set():
            raise InventoryCancelled()

    def request(self, service, operation, **kwargs):
        def invoke():
            self.check()
            try:
                if service not in self.clients:
                    session = get_boto3_session(self.profile, self.region)
                    self.clients[service] = session.client(service, config=client_config())
                return getattr(self.clients[service], operation)(**kwargs)
            except Exception as exc:
                if is_session_unavailable(exc):
                    self.clients.clear()
                raise
        return self.recover(self.profile, invoke)

    def pages(self, service, operation, **kwargs):
        seen = set()
        while True:
            response = self.request(service, operation, **kwargs)
            yield response
            token = response.get("NextToken")
            if not token:
                return
            if token in seen:
                raise RuntimeError("AWS returned a repeated pagination token.")
            seen.add(token)
            kwargs["NextToken"] = token


def list_aggregators(profile, region, *, run_with_sso=None, cancel=None):
    reader = _Reader(profile, region, run_with_sso, cancel)
    result = {}
    for page in reader.pages("config", "describe_configuration_aggregators", Limit=100):
        for item in page.get("ConfigurationAggregators", []):
            result[item["ConfigurationAggregatorName"]] = item
    return [result[name] for name in sorted(result)]


def _identity(item, *, detail=False):
    return tuple(str(item.get(key, "")) for key in (
        ("accountId", "awsRegion", "resourceType", "resourceId") if detail else
        ("SourceAccountId", "SourceRegion", "ResourceType", "ResourceId")
    ))


def collect_global_inventory(profile, region, aggregator, *, run_with_sso=None,
                             cancel=None, progress=None):
    if not aggregator.strip():
        raise ValueError("Select an AWS Config aggregator.")
    aggregator = aggregator.strip()
    reader = _Reader(profile, region, run_with_sso, cancel)
    notify = progress or (lambda _message: None)
    report = {
        "schema_version": "2.0", "status": "COLLECTED", "started_at": _now(),
        "finished_at": "", "source": {"profile": reader.profile, "region": reader.region,
                                        "aggregator": aggregator},
        "coverage": {"note": COVERAGE_NOTE, "sources": [], "resource_types": [],
                     "account_names_status": "NOT_QUERIED", "name_tags_status": "NOT_QUERIED"},
        "warnings": [], "resources": [],
    }
    rows = {}
    by_resource = {}

    def warning(stage, exc=None, **context):
        report["warnings"].append({"stage": stage, **context,
                                   **({"error": _error(exc), "category": classify_error(exc)} if exc else {})})

    def optional_failure(stage, exc, **context):
        if isinstance(exc, InventoryCancelled) or is_session_unavailable(exc):
            raise exc
        report["status"] = "PARTIAL"
        warning(stage, exc, **context)

    try:
        notify("Reading aggregator configuration and source status…")
        description = reader.request("config", "describe_configuration_aggregators",
                                     ConfigurationAggregatorNames=[aggregator])
        descriptions = description.get("ConfigurationAggregators", [])
        if not descriptions:
            raise ValueError("The selected aggregator is not available in this profile and region.")
        info = descriptions[0]
        report["source"].update({"arn": info.get("ConfigurationAggregatorArn", ""),
                                 "account_sources": info.get("AccountAggregationSources", []),
                                 "organization_source": info.get("OrganizationAggregationSource", {}),
                                 "filters": info.get("AggregatorFilters", {})})
        try:
            for page in reader.pages("config", "describe_configuration_aggregator_sources_status",
                                     ConfigurationAggregatorName=aggregator, Limit=100):
                for item in page.get("AggregatedSourceStatusList", []):
                    source = {"source_id": item.get("SourceId", ""), "source_type": item.get("SourceType", ""),
                              "region": item.get("AwsRegion", ""), "status": item.get("LastUpdateStatus", ""),
                              "last_update_time": _timestamp(item.get("LastUpdateTime")),
                              "error_code": item.get("LastErrorCode", "")}
                    report["coverage"]["sources"].append(source)
                    if source["status"] != "SUCCEEDED":
                        report["status"] = "PARTIAL"
                        warning("source_status", source=source)
        except Exception as exc:
            optional_failure("source_status", exc)
        if not report["coverage"]["sources"]:
            report["status"] = "PARTIAL"
            warning("source_status_unavailable")

        names = {}
        try:
            for page in reader.pages("organizations", "list_accounts"):
                names.update({item["Id"]: item.get("Name", "") for item in page.get("Accounts", [])})
            report["coverage"]["account_names_status"] = "AVAILABLE"
        except Exception as exc:
            if isinstance(exc, InventoryCancelled) or is_session_unavailable(exc):
                raise
            report["coverage"]["account_names_status"] = "UNAVAILABLE"
            warning("account_names", exc)

        notify("Enumerating resource types across accounts and regions…")
        counts = {}
        expected_total = None
        # If a later counts page fails, keep the types already discovered.
        try:
            for page in reader.pages("config", "get_aggregate_discovered_resource_counts",
                                     ConfigurationAggregatorName=aggregator, GroupByKey="RESOURCE_TYPE", Limit=1000):
                for item in page.get("GroupedResourceCounts", []):
                    counts[item["GroupName"]] = item.get("ResourceCount", 0)
                if expected_total is None and isinstance(page.get("TotalDiscoveredResources"), int):
                    expected_total = page["TotalDiscoveredResources"]
                    report["coverage"]["expected_resource_count"] = expected_total
            report["coverage"]["resource_type_counts_status"] = "AVAILABLE"
            if expected_total is None:
                report["coverage"]["expected_resource_count"] = sum(counts.values())
        except Exception as exc:
            report["coverage"]["resource_type_counts_status"] = "PARTIAL"
            optional_failure("resource_type_counts", exc)

        for type_index, resource_type in enumerate(sorted(counts), 1):
            reader.check()
            notify(f"Listing {resource_type} · type {type_index}/{len(counts)} · {len(rows)} resources")
            type_rows = {}
            coverage = {"resource_type": resource_type, "expected_count": counts[resource_type],
                        "listed_count": 0, "details_count": 0, "status": "COLLECTED"}
            report["coverage"]["resource_types"].append(coverage)
            try:
                for page in reader.pages("config", "list_aggregate_discovered_resources",
                                         ConfigurationAggregatorName=aggregator, ResourceType=resource_type, Limit=100):
                    for item in page.get("ResourceIdentifiers", []):
                        key = _identity(item)
                        if not all(key):
                            report["status"] = coverage["status"] = "PARTIAL"
                            warning("invalid_identifier", resource_type=resource_type)
                            continue
                        if key not in rows:
                            account, source_region, kind, identifier = key
                            rows[key] = {
                                "resource_key": json.dumps(key, separators=(",", ":")),
                                "account_id": account, "account_name": names.get(account, ""),
                                "region": source_region, "resource_type": kind, "resource_id": identifier,
                                "resource_name": _resource_name(item.get("ResourceName")), "arn": "",
                                "config_capture_time": "", "inventory_export_time": "",
                                "source": aggregator, "configuration_status": "", "details_status": "MISSING",
                            }
                            by_resource.setdefault((account, kind, identifier), []).append(rows[key])
                        type_rows[key] = rows[key]
                    coverage["listed_count"] = len(type_rows)
                    notify(f"Listed {len(rows)} resources · {resource_type}")
            except Exception as exc:
                coverage["status"] = "PARTIAL"
                optional_failure("resource_listing", exc, resource_type=resource_type)
            if len(type_rows) != counts[resource_type]:
                report["status"] = coverage["status"] = "PARTIAL"
                warning("count_mismatch", resource_type=resource_type, expected=counts[resource_type], listed=len(type_rows))

            keys = list(type_rows)
            for start in range(0, len(keys), 100):
                pending = [{"SourceAccountId": key[0], "SourceRegion": key[1],
                            "ResourceType": key[2], "ResourceId": key[3]} for key in keys[start:start + 100]]
                for attempt in range(3):
                    reader.check()
                    notify(f"Reading details · {resource_type} · {min(start + 100, len(keys))}/{len(keys)}")
                    try:
                        response = reader.request("config", "batch_get_aggregate_resource_config",
                                                  ConfigurationAggregatorName=aggregator, ResourceIdentifiers=pending)
                    except Exception as exc:
                        coverage["status"] = "PARTIAL"
                        optional_failure("resource_details", exc, resource_type=resource_type)
                        break
                    for detail in response.get("BaseConfigurationItems", []):
                        for row in _matching_rows(type_rows, by_resource, detail):
                            row.update(arn=detail.get("arn", ""),
                                       resource_name=_resource_name(detail.get("resourceName")) or row["resource_name"],
                                       config_capture_time=_timestamp(detail.get("configurationItemCaptureTime")),
                                       configuration_status=detail.get("configurationItemStatus", ""),
                                       details_status="AVAILABLE")
                    pending = response.get("UnprocessedResourceIdentifiers", [])
                    if not pending:
                        break
                    if attempt < 2 and reader.cancel.wait(0.2 * 2 ** attempt):
                        reader.check()
            coverage["details_count"] = sum(row["details_status"] == "AVAILABLE" for row in type_rows.values())
            if coverage["details_count"] != len(type_rows):
                report["status"] = coverage["status"] = "PARTIAL"
                warning("missing_details", resource_type=resource_type,
                        count=len(type_rows) - coverage["details_count"])
        if rows:
            notify("Resolving resource Name tags from the aggregator…")
            named = 0
            try:
                # Explicitly select tags: BatchGetAggregateResourceConfig omits
                # them. Stream pages instead of retaining another full inventory.
                for page in reader.pages("config", "select_aggregate_resource_config",
                                         ConfigurationAggregatorName=aggregator, Limit=100,
                                         Expression="SELECT accountId, awsRegion, resourceType, resourceId, tags"):
                    for value in page.get("Results", []):
                        try:
                            item = json.loads(value)
                        except (ValueError, TypeError):
                            report["status"] = "PARTIAL"
                            report["coverage"]["name_tags_status"] = "PARTIAL"
                            warning("invalid_name_query_result")
                            continue
                        if not isinstance(item, dict):
                            report["status"] = "PARTIAL"
                            report["coverage"]["name_tags_status"] = "PARTIAL"
                            warning("invalid_name_query_result")
                            continue
                        name = _name_tag(item.get("tags"))
                        if name:
                            for row in _matching_rows(rows, by_resource, item):
                                row["resource_name"] = name
                                named += 1
                    report["coverage"]["name_tags_resolved"] = named
                    notify(f"Resolving resource Name tags · {named} resource observations named")
                if report["coverage"]["name_tags_status"] != "PARTIAL":
                    report["coverage"]["name_tags_status"] = "AVAILABLE"
            except Exception as exc:
                report["coverage"]["name_tags_status"] = "PARTIAL" if named else "UNAVAILABLE"
                optional_failure("resource_names", exc)
        reader.check()
    except InventoryCancelled:
        report["status"] = "CANCELLED"
        if report["coverage"]["resource_types"]:
            report["coverage"]["resource_types"][-1]["status"] = "CANCELLED"
        warning("cancelled", message="Partial results retained; in-flight AWS requests finish before cancellation.")
    except Exception as exc:
        if not rows:
            raise
        report["status"] = "PARTIAL"
        if report["coverage"]["resource_types"]:
            report["coverage"]["resource_types"][-1]["status"] = "PARTIAL"
        warning("collection_stopped", exc)

    report["finished_at"] = _now()
    report["resources"] = [rows[key] for key in sorted(rows)]
    listed_counts = Counter(row["resource_type"] for row in rows.values())
    detail_counts = Counter(row["resource_type"] for row in rows.values() if row["details_status"] == "AVAILABLE")
    for coverage in report["coverage"]["resource_types"]:
        # Cancellation/fatal authentication can interrupt a batch loop before
        # its normal summary is calculated. Reflect the actual retained rows.
        coverage["listed_count"] = listed_counts[coverage["resource_type"]]
        coverage["details_count"] = detail_counts[coverage["resource_type"]]
    report["summary"] = {
        "resource_count": len(rows), "account_count": len({key[0] for key in rows}),
        "expected_resource_count": report["coverage"].get("expected_resource_count"),
        "region_count": len({key[1] for key in rows}),
        "by_account": dict(sorted(Counter(key[0] for key in rows).items())),
        "by_type": dict(sorted(Counter(key[2] for key in rows).items())),
    }
    expected = report["summary"]["expected_resource_count"]
    if expected is not None and expected != len(rows):
        if report["status"] != "CANCELLED":
            report["status"] = "PARTIAL"
        warning("inventory_count_mismatch", expected=expected, collected=len(rows))
    return report


def export_inventory(path, report, *, resources=None, delimiter=","):
    """Atomically export all resources or an explicitly filtered selection."""
    path = Path(path)
    if path.suffix.lower() not in {".json", ".csv"}:
        raise ValueError("Choose a .json or .csv export file.")
    if delimiter not in {",", ";"}:
        raise ValueError("Choose comma or semicolon as the CSV separator.")
    exported_at = _now()
    rows = report["resources"] if resources is None else resources
    rows = [{**{key: inventory_value(row, key) for key in INVENTORY_COLUMNS},
             "inventory_export_time": exported_at} for row in rows]
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8-sig" if path.suffix.lower() == ".csv" else "utf-8", newline="") as handle:
            if path.suffix.lower() == ".json":
                payload = {**report, "schema_version": "2.0", "resources": rows, "export": {
                    "exported_at": exported_at, "scope": "ALL" if resources is None else "FILTERED",
                    "resource_count": len(rows), "collected_resource_count": len(report["resources"]),
                }}
                json.dump(payload, handle, ensure_ascii=False, indent=2, default=_timestamp)
                handle.write("\n")
            else:
                writer = csv.DictWriter(handle, fieldnames=INVENTORY_COLUMNS, delimiter=delimiter)
                writer.writeheader()
                for row in rows:
                    writer.writerow({key: csv_safe_value(inventory_value(row, key)) for key in INVENTORY_COLUMNS})
        os.replace(temporary, path)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise
