"""Build and preview a cross-region DR dashboard using existing metrics only.

This module never enables logging, publishes metrics, or changes AWS resources
other than the explicitly requested, newly named CloudWatch dashboard.
"""
from __future__ import annotations

import json
import re
from html import escape
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from aws.session import get_boto3_session, _client_config
from services.discovery.engine import _session_unavailable_error
from services.discovery.models import ResourceNode


@dataclass(frozen=True)
class Metric:
    resource_id: str
    resource_name: str
    resource_type: str
    region: str
    namespace: str
    name: str
    dimension_name: str
    dimension_value: str
    statistic: str
    label: str
    unit: str
    attribution: str = "ACCOUNT SCOPE"
    dimensions: tuple[tuple[str, str], ...] = ()
    comparison: str = ""


@dataclass
class MetricResult:
    metric: Metric
    status: str = "NO DATA"
    points: list[tuple[datetime, float]] = field(default_factory=list)
    detail: str = ""


@dataclass(frozen=True)
class ComparisonPair:
    """A user-approved source/DR comparison set.

    ``source`` and ``dr`` preserve the original one-to-one contract.  The
    optional groups let an operator compare a service fleet (for example all
    ECS services) as one logical DR unit without requiring identical names or
    matching counts in each region.
    """
    source: ResourceNode
    dr: ResourceNode
    label: str
    source_group: tuple[ResourceNode, ...] = ()
    dr_group: tuple[ResourceNode, ...] = ()

    @property
    def sources(self) -> tuple[ResourceNode, ...]:
        return self.source_group or (self.source,)

    @property
    def drs(self) -> tuple[ResourceNode, ...]:
        return self.dr_group or (self.dr,)


def format_metric_value(value: float) -> str:
    """Format metric values for operators without scientific notation."""
    number = float(value)
    magnitude = abs(number)
    if number == 0:
        return "0"
    if magnitude >= 1000:
        decimals = 0 if number.is_integer() else 1
        return f"{number:,.{decimals}f}"
    if magnitude >= 100:
        decimals = 0 if number.is_integer() else 1
        return f"{number:.{decimals}f}"
    if magnitude >= 1:
        return f"{number:.2f}".rstrip("0").rstrip(".")
    if magnitude >= 0.001:
        return f"{number:.3f}".rstrip("0").rstrip(".")
    return f"{number:.6f}".rstrip("0").rstrip(".")


# The statistic and dimensions are specific to the AWS service.  In
# particular, ELB-generated errors are never mislabelled as target errors.
TEMPLATES = {
    "ALB": ("AWS/ApplicationELB", "LoadBalancer", [
        # These queries use only the LoadBalancer dimension.  Do not include
        # target-group response code metrics in an ALB-level DR comparison.
        ("RequestCount", "Sum", "ALB requests", "Count"),
        ("HTTPCode_ELB_4XX_Count", "Sum", "ALB HTTP 4xx / client error", "Count"),
        ("HTTPCode_ELB_5XX_Count", "Sum", "ALB HTTP 5xx / server error", "Count"),
        ("TargetResponseTime", "tm99", "ALB target response time / tm99", "Seconds"),
    ]),
    "NLB": ("AWS/NetworkELB", "LoadBalancer", [
        ("NewFlowCount", "Sum", "New connections", "Count"),
        ("ActiveFlowCount", "Average", "Active connections", "Count"),
        ("ProcessedBytes", "Sum", "Processed bytes", "Bytes"),
    ]),
    "EC2": ("AWS/EC2", "InstanceId", [
        ("NetworkIn", "Sum", "Instance network in", "Bytes"),
        ("NetworkOut", "Sum", "Instance network out", "Bytes"),
        ("CPUUtilization", "Average", "CPU utilization", "Percent"),
        ("StatusCheckFailed", "Maximum", "Instance status check failed", "Count"),
    ]),
    "Lambda": ("AWS/Lambda", "FunctionName", [
        ("Invocations", "Sum", "Invocations", "Count"),
        ("Errors", "Sum", "Function errors", "Count"),
        ("Throttles", "Sum", "Throttled invocations", "Count"),
        ("Duration", "Average", "Duration", "Milliseconds"),
    ]),
    "RDS": ("AWS/RDS", "DBInstanceIdentifier", [
        ("DatabaseConnections", "Average", "Database client connections", "Count"),
        ("CPUUtilization", "Average", "CPU utilization", "Percent"),
        ("ReadLatency", "Average", "Read latency", "Seconds"),
        ("WriteLatency", "Average", "Write latency", "Seconds"),
    ]),
    "EFS": ("AWS/EFS", "FileSystemId", [
        ("TotalIOBytes", "Sum", "Total file system I/O", "Bytes"),
    ]),
    "APIGateway": ("AWS/ApiGateway", "ApiName", [
        ("Count", "SampleCount", "API calls", "Count"),
        ("Latency", "tm99", "Response time / tm99", "Milliseconds"),
        ("4XXError", "Sum", "HTTP 4xx errors", "Count"),
        ("5XXError", "Sum", "HTTP 5xx errors", "Count"),
    ]),
    "APIGatewayV2": ("AWS/ApiGateway", "ApiId", [
        ("Count", "SampleCount", "API calls", "Count"),
        ("Latency", "tm99", "Response time / tm99", "Milliseconds"),
        ("4xx", "Sum", "HTTP 4xx errors", "Count"),
        ("5xx", "Sum", "HTTP 5xx errors", "Count"),
    ]),
    "ECS": ("AWS/ECS", "", []),
}


def _dimension_value(node: ResourceNode) -> str:
    if node.resource_type in {"ALB", "NLB"}:
        arn = node.arn or node.id
        prefix = "app/" if node.resource_type == "ALB" else "net/"
        resource = arn.split(":loadbalancer/", 1)[-1] if ":loadbalancer/" in arn else ""
        return resource if resource.startswith(prefix) and len(resource.split("/")) == 3 else ""
    if node.resource_type == "EC2":
        return node.id if node.id.startswith("i-") else ""
    if node.resource_type == "EFS":
        return node.id if node.id.startswith("fs-") else ""
    if node.resource_type == "Lambda":
        if node.arn and ":function:" in node.arn:
            return node.arn.split(":function:", 1)[1].split(":", 1)[0]
        return node.name or ""
    if node.resource_type == "RDS":
        if node.arn and ":db:" in node.arn:
            return node.arn.split(":db:", 1)[1]
        return node.name or ""
    if node.resource_type == "APIGateway":
        return node.name or ""
    if node.resource_type == "APIGatewayV2":
        return node.id or ""
    return ""


def _dimensions(node: ResourceNode, dimension_name: str, dimension_value: str) -> tuple[tuple[str, str], ...]:
    if node.resource_type == "ECS":
        cluster_arn = str(node.metadata.get("ClusterArn", ""))
        cluster_name = cluster_arn.rsplit("/", 1)[-1]
        service_name = node.name or ""
        return (("ClusterName", cluster_name), ("ServiceName", service_name)) if cluster_name and service_name else ()
    return ((dimension_name, dimension_value),) if dimension_name and dimension_value else ()


def _node_metrics(node: ResourceNode, application: str, tag_key: str, comparison: str = "") -> tuple[list[Metric], str | None]:
    if node.resource_type not in TEMPLATES:
        return [], f"{node.region} / {node.resource_type} / {node.name or node.id}: no metric template"
    namespace, dimension_name, templates = TEMPLATES[node.resource_type]
    dimension_value = _dimension_value(node)
    dimensions = _dimensions(node, dimension_name, dimension_value)
    if not dimensions:
        return [], f"{node.region} / {node.resource_type} / {node.name or node.id}: metric dimensions unavailable"
    attribution = ("ACCOUNT SCOPE" if application == "ALL PRODUCTS" else
                   "TAGGED" if tag_key and (node.tags or {}).get(tag_key, "").strip().casefold() == application.casefold() else "SHARED / UNTAGGED")
    if node.resource_type == "ECS":
        # CPU and memory are emitted natively by ECS. RunningTaskCount is a
        # Container Insights metric and correctly returns NO DATA when that
        # optional observability feature is not enabled.
        templates = [
            ("AWS/ECS", "CPUUtilization", "Average", "CPU utilization", "Percent"),
            ("AWS/ECS", "MemoryUtilization", "Average", "Memory utilization", "Percent"),
            ("ECS/ContainerInsights", "RunningTaskCount", "Average", "Running service tasks", "Count"),
        ]
        return [Metric(node.id, node.name or node.id, node.resource_type, node.region,
                       metric_namespace, name, dimension_name, dimension_value, stat, label, unit,
                       attribution, dimensions, comparison)
                for metric_namespace, name, stat, label, unit in templates], None
    metrics = [Metric(node.id, node.name or node.id, node.resource_type, node.region,
                      namespace, name, dimension_name, dimension_value, stat, label, unit,
                      attribution, dimensions, comparison)
               for name, stat, label, unit in templates]
    return metrics, None


def build_metrics(nodes: list[ResourceNode], regions: tuple[str, str], application: str = "ALL PRODUCTS", tag_key: str = "") -> tuple[list[Metric], list[str]]:
    if len(regions) != 2 or not all(regions) or regions[0] == regions[1]:
        raise ValueError("Select two different discovered regions.")
    metrics, skipped = [], []
    for node in sorted(nodes, key=lambda n: (n.region, n.resource_type, n.name or n.id)):
        if node.region not in regions:
            continue
        node_metrics, reason = _node_metrics(node, application, tag_key)
        metrics.extend(node_metrics)
        if reason:
            skipped.append(reason)
    return metrics, skipped


def build_pair_metrics(pairs: list[ComparisonPair], regions: tuple[str, str], application: str = "ALL PRODUCTS", tag_key: str = "") -> tuple[list[Metric], list[str]]:
    if len(regions) != 2 or not all(regions) or regions[0] == regions[1]:
        raise ValueError("Select two different discovered regions.")
    metrics: list[Metric] = []
    skipped: list[str] = []
    for pair in pairs:
        source_nodes, dr_nodes = pair.sources, pair.drs
        if any(node.region != regions[0] for node in source_nodes) or any(node.region != regions[1] for node in dr_nodes):
            raise ValueError(f"Pair '{pair.label}' does not match the selected source/DR regions.")
        resource_types = {node.resource_type for node in (*source_nodes, *dr_nodes)}
        if len(resource_types) != 1:
            raise ValueError(f"Pair '{pair.label}' contains different resource types.")
        for node in (*source_nodes, *dr_nodes):
            node_metrics, reason = _node_metrics(node, application, tag_key, pair.label)
            metrics.extend(node_metrics)
            if reason:
                skipped.append(reason)
    return metrics, skipped


def metric_dimensions(metric: Metric) -> tuple[tuple[str, str], ...]:
    return metric.dimensions or ((metric.dimension_name, metric.dimension_value),)


def verify_identity(session, expected_account: str) -> None:
    account = session.client("sts", config=_client_config()).get_caller_identity().get("Account", "")
    if not expected_account or account != expected_account:
        raise ValueError(f"AWS account changed since discovery (expected {expected_account}, current {account or 'unknown'}). Discover again.")


def query_metrics(profile: str, account: str, metrics: list[Metric], start: datetime, end: datetime, period: int = 300) -> list[MetricResult]:
    if start.tzinfo is None or end.tzinfo is None or start >= end:
        raise ValueError("Select a valid UTC start and end time.")
    if period < 60 or period % 60:
        raise ValueError("Metric period must be a multiple of 60 seconds.")
    if not metrics:
        raise ValueError("Select at least one supported resource.")
    session = get_boto3_session(profile, metrics[0].region)
    verify_identity(session, account)
    results = [MetricResult(metric) for metric in metrics]
    indexes = defaultdict(list)
    for index, metric in enumerate(metrics):
        indexes[metric.region].append(index)
    for region, region_indexes in indexes.items():
        client = session.client("cloudwatch", region_name=region, config=_client_config())
        for offset in range(0, len(region_indexes), 500):
            batch = region_indexes[offset:offset + 500]
            queries = []
            for local, index in enumerate(batch):
                metric = metrics[index]
                queries.append({"Id": f"m{local}", "MetricStat": {
                    "Metric": {"Namespace": metric.namespace, "MetricName": metric.name,
                               "Dimensions": [{"Name": name, "Value": value} for name, value in metric_dimensions(metric)]},
                    "Period": period, "Stat": metric.statistic,
                }, "ReturnData": True})
            try:
                token = None
                seen = set()
                batch_messages = []
                while True:
                    kwargs = {"MetricDataQueries": queries, "StartTime": start, "EndTime": end, "ScanBy": "TimestampAscending"}
                    if token:
                        kwargs["NextToken"] = token
                    response = client.get_metric_data(**kwargs)
                    messages = "; ".join(str(item.get("Value", "")) for item in response.get("Messages", []))
                    if messages:
                        batch_messages.append(messages)
                    for entry in response.get("MetricDataResults", []):
                        result_id = str(entry.get("Id", ""))
                        if not re.fullmatch(r"m\d+", result_id) or int(result_id[1:]) >= len(batch):
                            continue
                        result = results[batch[int(result_id[1:])]]
                        seen.add(int(result_id[1:]))
                        result.points.extend(zip(entry.get("Timestamps", []), entry.get("Values", [])))
                        status = entry.get("StatusCode", "Complete")
                        if status != "Complete":
                            result.status = "INCOMPLETE"
                            result.detail = status
                        if messages:
                            result.status = "INCOMPLETE"
                            result.detail = messages
                        if entry.get("Messages"):
                            result.status = "INCOMPLETE"
                            result.detail = "; ".join(str(m.get("Value", "")) for m in entry["Messages"])
                    token = response.get("NextToken")
                    if not token:
                        break
                for local, index in enumerate(batch):
                    result = results[index]
                    result.points.sort(key=lambda point: point[0])
                    if local not in seen:
                        result.status = "INCOMPLETE"
                        result.detail = "CloudWatch did not return this metric result."
                    elif batch_messages:
                        result.status = "INCOMPLETE"
                        result.detail = "; ".join(batch_messages)
                    elif result.status != "INCOMPLETE":
                        result.status = "DATA" if result.points else "NO DATA"
            except Exception as exc:
                if _session_unavailable_error(exc):
                    raise
                for index in batch:
                    results[index].status = "ERROR"
                    results[index].detail = str(exc)
    return results


def build_dashboard(account: str, application: str, regions: tuple[str, str], metrics: list[Metric], period: int = 300) -> dict:
    if not metrics:
        raise ValueError("No supported metrics are selected for the dashboard.")
    if len(regions) != 2 or not all(regions) or regions[0] == regions[1] or any(metric.region not in regions for metric in metrics):
        raise ValueError("The dashboard requires two different regions matching its metrics.")
    widgets = [{"type": "text", "x": 0, "y": 0, "width": 24, "height": 3, "properties": {"markdown":
        f"# Disaster recovery | {application}\nAccount: {account} | Source: {regions[0]} | DR: {regions[1]}\n"
        "Existing native metrics only. Missing data is not zero. "
        "Each full-width widget is one metric and compares the selected source/DR resource set."}}]
    y = 3
    if any(metric.comparison for metric in metrics):
        # Preserve the explicit set selected in the UI. A widget has one
        # regional series per one-to-one pair, or two metric-math aggregate
        # series when either side contains several resources.
        comparisons: dict[str, list[Metric]] = defaultdict(list)
        for metric in metrics:
            comparisons[metric.comparison].append(metric)
        for comparison, comparison_metrics in comparisons.items():
            resource_type = comparison_metrics[0].resource_type
            widgets.append({"type": "text", "x": 0, "y": y, "width": 24, "height": 1,
                            "properties": {"markdown": f"## {resource_type.upper()} | {comparison}"}})
            y += 1
            per_metric: dict[tuple[str, str, str, str], list[Metric]] = defaultdict(list)
            for metric in comparison_metrics:
                per_metric[(metric.name, metric.label, metric.statistic, metric.unit)].append(metric)
            for (_name, label, stat, unit), pair_series in per_metric.items():
                by_region: dict[str, list[Metric]] = defaultdict(list)
                for metric in pair_series:
                    by_region[metric.region].append(metric)
                if set(by_region) != set(regions):
                    raise ValueError(f"Comparison '{comparison}' does not have a series in each selected region.")
                series = []
                grouped = any(len(by_region[region]) != 1 for region in regions)
                if not grouped:
                    for metric in sorted(pair_series, key=lambda item: regions.index(item.region)):
                        entry = [metric.namespace, metric.name]
                        for dimension_name, dimension_value in metric_dimensions(metric):
                            entry.extend([dimension_name, dimension_value])
                        entry.append({"region": metric.region, "label": f"{metric.region} | {metric.resource_name}"})
                        series.append(entry)
                else:
                    # Keep raw metrics hidden and expose one aggregate series
                    # for each region. Counts/errors/tasks are summed; rates,
                    # percentages and latency use an explicitly unweighted
                    # average across the selected resources.
                    function = "SUM" if stat in {"Sum", "SampleCount"} or _name == "RunningTaskCount" else "AVG"
                    for prefix, region in zip(("s", "d"), regions):
                        ids = []
                        for index, metric in enumerate(by_region[region]):
                            metric_id = f"{prefix}{index}"
                            ids.append(metric_id)
                            entry = [metric.namespace, metric.name]
                            for dimension_name, dimension_value in metric_dimensions(metric):
                                entry.extend([dimension_name, dimension_value])
                            entry.append({"id": metric_id, "region": region, "visible": False})
                            series.append(entry)
                        expression = f"{function}([{','.join(ids)}])"
                        series.append([{"expression": expression, "id": f"e{prefix}", "region": region,
                                        "label": f"{region} | {function.lower()} of {len(ids)} resources"}])
                widgets.append({"type": "metric", "x": 0, "y": y, "width": 24, "height": 6,
                                "properties": {"view": "timeSeries", "stacked": False, "region": regions[0],
                                "period": period, "stat": stat, "title": f"{resource_type} | {label} ({stat}, {unit})", "metrics": series}})
                y += 6
    else:
        # Backwards-compatible layout for callers outside the pair selector.
        groups = defaultdict(list)
        for metric in metrics:
            groups[(metric.resource_type, metric.name, metric.label, metric.statistic, metric.unit)].append(metric)
        for (resource_type, name, label, stat, unit), group in sorted(groups.items()):
            for offset in range(0, len(group), 500):
                part = group[offset:offset + 500]
                series = []
                for metric in part:
                    entry = [metric.namespace, metric.name]
                    for dimension_name, dimension_value in metric_dimensions(metric):
                        entry.extend([dimension_name, dimension_value])
                    entry.append({"region": metric.region, "label": f"{metric.region} | {metric.resource_name} | {metric.attribution}"})
                    series.append(entry)
                title = f"{resource_type} | {label} ({stat}, {unit})"
                if len(group) > 500: title += f" | part {offset // 500 + 1}"
                widgets.append({"type": "metric", "x": 0, "y": y, "width": 24, "height": 6,
                                "properties": {"view": "timeSeries", "stacked": False, "region": regions[0],
                                "period": period, "stat": stat, "title": title, "metrics": series}})
                y += 6
    if len(widgets) > 500:
        raise ValueError("Selection exceeds the 500-widget CloudWatch dashboard limit; select fewer resources.")
    return {"start": "-PT6H", "periodOverride": "inherit", "widgets": widgets}


def dashboard_name_for_profile(profile: str) -> str:
    safe_profile = re.sub(r"[^A-Za-z0-9_-]+", "-", profile).strip("-_")[:180]
    return f"{safe_profile or 'AWS'}-Disaster_Recovery"


def artifact_prefix(profile: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", profile).strip(".-")[:120] or "AWS"


def _aggregate_result_series(results: list[MetricResult]) -> tuple[list[tuple[datetime, float]], int, int]:
    """Aggregate a selected resource set for exported DR evidence and charts.

    Count-like CloudWatch series are added. Utilisation/latency series are an
    unweighted average, which is the only safe aggregate without traffic or
    capacity weighting metadata.
    """
    usable = [result for result in results if result.status == "DATA" and result.points]
    if not usable:
        return [], 0, len(results)
    by_timestamp: dict[datetime, list[float]] = defaultdict(list)
    for result in usable:
        for timestamp, value in result.points:
            by_timestamp[timestamp].append(float(value))
    statistic = usable[0].metric.statistic
    is_sum = statistic in {"Sum", "SampleCount"} or usable[0].metric.name == "RunningTaskCount"
    series = [(timestamp, sum(points) if is_sum else sum(points) / len(points)) for timestamp, points in sorted(by_timestamp.items())]
    return series, len(usable), len(results)


def _aggregate_result_points(results: list[MetricResult]) -> tuple[list[float], int, int]:
    series, data_count, resource_count = _aggregate_result_series(results)
    return [value for _timestamp, value in series], data_count, resource_count


def _svg_line_chart(title: str, unit: str, regions: tuple[str, str], regional_results: dict[str, list[MetricResult]]) -> str:
    """Return a compact, dependency-free two-region CloudWatch evidence chart."""
    series_by_region = {region: _aggregate_result_series(regional_results.get(region, []))[0] for region in regions}
    samples = [point for series in series_by_region.values() for point in series]
    if not samples:
        return f'<p class="chart-empty">{escape(title)}: no CloudWatch data returned for either region.</p>'
    width, height = 920, 250
    left, right, top, bottom = 58, 16, 26, 40
    plot_width, plot_height = width - left - right, height - top - bottom
    timestamps = [point[0].timestamp() for point in samples]
    values = [point[1] for point in samples]
    min_time, max_time = min(timestamps), max(timestamps)
    min_value, max_value = min(values), max(values)
    if max_time == min_time:
        max_time += 1
    if max_value == min_value:
        max_value += max(1.0, abs(max_value) * 0.1)

    def point_path(series):
        if not series:
            return ""
        commands = []
        for index, (timestamp, value) in enumerate(series):
            x = left + ((timestamp.timestamp() - min_time) / (max_time - min_time)) * plot_width
            y = top + (1 - ((value - min_value) / (max_value - min_value))) * plot_height
            commands.append(f"{'M' if index == 0 else 'L'}{x:.1f},{y:.1f}")
        return " ".join(commands)

    colors = ("#e0b66d", "#6cb7d6")
    paths = "".join(
        f'<path d="{point_path(series_by_region[region])}" fill="none" stroke="{colors[index]}" stroke-width="2.5"/>'
        for index, region in enumerate(regions) if series_by_region[region]
    )
    legend = "".join(
        f'<span><i style="background:{colors[index]}"></i>{escape(region)}'
        f' ({len(series_by_region[region])} points)' + ("</span>" if series_by_region[region] else " — no data</span>")
        for index, region in enumerate(regions)
    )
    return f'''<figure class="chart"><figcaption>{escape(title)} <small>({escape(unit)})</small></figcaption>
<div class="legend">{legend}</div><svg viewBox="0 0 {width} {height}" role="img" aria-label="{escape(title)} comparison for {escape(regions[0])} and {escape(regions[1])}">
<line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_height}" class="axis"/><line x1="{left}" y1="{top + plot_height}" x2="{left + plot_width}" y2="{top + plot_height}" class="axis"/>
<text x="4" y="{top + 6}" class="tick">{format_metric_value(max_value)}</text><text x="4" y="{top + plot_height}" class="tick">{format_metric_value(min_value)}</text>
<text x="{left}" y="{height - 10}" class="tick">UTC {datetime.fromtimestamp(min_time, timezone.utc).strftime('%H:%M')}</text><text x="{left + plot_width - 72}" y="{height - 10}" class="tick">UTC {datetime.fromtimestamp(max_time, timezone.utc).strftime('%H:%M')}</text>{paths}</svg></figure>'''


def render_html_report(profile: str, account: str, application: str, regions: tuple[str, str], start: datetime, end: datetime, results: list[MetricResult]) -> str:
    """Create a self-contained, evidence-oriented comparison report.

    Values are derived only from returned CloudWatch points. In particular,
    absent data remains absent and is never converted to a zero.
    """
    duration_minutes = max((end - start).total_seconds() / 60, 1)
    by_comparison: dict[str, list[MetricResult]] = defaultdict(list)
    for result in results:
        by_comparison[result.metric.comparison or result.metric.resource_name].append(result)
    sections = []
    for comparison, comparison_results in by_comparison.items():
        resource_type = comparison_results[0].metric.resource_type
        metric_groups: dict[tuple[str, str, str], list[MetricResult]] = defaultdict(list)
        for result in comparison_results:
            metric = result.metric
            metric_groups[(metric.name, metric.label, metric.unit)].append(result)
        rows = []
        for (_metric_name, label, unit), values in metric_groups.items():
            cells = []
            for region in regions:
                regional_values = [item for item in values if item.metric.region == region]
                points, data_count, resource_count = _aggregate_result_points(regional_values)
                if not points:
                    statuses = ", ".join(sorted({item.status for item in regional_values})) or "MISSING"
                    detail = "; ".join(item.detail for item in regional_values if item.detail) or "No returned series"
                    cells.append(f"<td><strong>{escape(statuses)}</strong><br><small>{escape(detail)}</small></td>")
                    continue
                total = sum(points)
                mean = total / len(points)
                metric = regional_values[0].metric
                note = f"Resources with data: {data_count}/{resource_count}"
                if metric.statistic in {"Sum", "SampleCount"} or metric.name == "RunningTaskCount":
                    cells.append(f"<td><strong>Total: {format_metric_value(total)} {escape(unit)}</strong><br><small>Per minute: {format_metric_value(total / duration_minutes)} | Samples: {len(points)} | {note}</small></td>")
                else:
                    cells.append(f"<td><strong>Mean: {format_metric_value(mean)} {escape(unit)}</strong><br><small>Min: {format_metric_value(min(points))} | Max: {format_metric_value(max(points))} | Samples: {len(points)} | {note}</small></td>")
            rows.append(f"<tr><th>{escape(label)}</th>{''.join(cells)}</tr>")
        charts = []
        if resource_type == "ALB":
            for metric_name, chart_title, chart_unit in (
                ("RequestCount", "ALB request count", "requests per period"),
                ("TargetResponseTime", "ALB target response time / tm99", "seconds"),
            ):
                regional_results = {
                    region: [item for item in comparison_results if item.metric.name == metric_name and item.metric.region == region]
                    for region in regions
                }
                charts.append(_svg_line_chart(chart_title, chart_unit, regions, regional_results))
        sections.append(f"<section><h2>{escape(resource_type)} · {escape(comparison)}</h2>{''.join(charts)}<table><thead><tr><th>Metric</th><th>{escape(regions[0])}</th><th>{escape(regions[1])}</th></tr></thead><tbody>{''.join(rows)}</tbody></table></section>")
    return f"""<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><title>{escape(profile)} - Disaster Recovery</title>
<style>body{{background:#0b1010;color:#e8e1d3;font:14px Segoe UI,Arial,sans-serif;margin:32px}}h1{{color:#e0b66d}}h2{{color:#e0b66d;margin:0 0 12px}}section{{background:#111818;border:1px solid #394241;padding:18px;margin:18px 0}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #394241;padding:10px;text-align:left;vertical-align:top}}th{{background:#171f1f;color:#e0b66d}}small{{color:#abb3ad}}.notice,.chart-empty{{color:#cfaa78}}.chart{{border:1px solid #2d3938;background:#0d1414;padding:14px;margin:16px 0}}.chart figcaption{{font-weight:700;color:#e0b66d;margin-bottom:7px}}.legend{{display:flex;gap:18px;margin:0 0 7px;color:#abb3ad;font-size:12px}}.legend i{{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:5px}}svg{{width:100%;height:auto;background:#0b1010}}.axis{{stroke:#475351;stroke-width:1}}.tick{{fill:#abb3ad;font-size:11px}}</style></head><body>
<h1>{escape(profile)} — Disaster Recovery evidence</h1><p>Account: <strong>{escape(account)}</strong> · Application: <strong>{escape(application)}</strong><br>Source: <strong>{escape(regions[0])}</strong> · DR: <strong>{escape(regions[1])}</strong><br>UTC interval: {escape(start.isoformat())} — {escape(end.isoformat())}</p>
<p class=\"notice\">Native CloudWatch metrics only. Missing data is not zero. Latency labelled tm99 excludes the highest 1% of raw samples where CloudWatch supports it.</p>{''.join(sections)}</body></html>"""


def publish_new_dashboard(profile: str, account: str, region: str, application: str, body: dict) -> tuple[str, list]:
    session = get_boto3_session(profile, region)
    verify_identity(session, account)
    client = session.client("cloudwatch", region_name=region, config=_client_config())
    name = dashboard_name_for_profile(profile)
    result = client.put_dashboard(DashboardName=name, DashboardBody=json.dumps(body, ensure_ascii=True))
    return name, result.get("DashboardValidationMessages", [])
