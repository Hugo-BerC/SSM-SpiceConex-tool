from __future__ import annotations

import json
import re
import uuid

from botocore.exceptions import ClientError

from aws.session import get_ec2_client, get_client


def list_instance_volumes(profile: str, region: str, instance_id: str) -> list[dict]:
    pages = get_ec2_client(profile, region).get_paginator("describe_volumes").paginate(
        Filters=[{"Name": "attachment.instance-id", "Values": [instance_id]}]
    )
    return [
        {
            "Device": attachment["Device"],
            "VolumeId": volume["VolumeId"],
            "Iops": volume.get("Iops", "N/A"),
            "Throughput": volume.get("Throughput", "N/A"),
            "Size": volume.get("Size", "N/A"),
            "Type": volume.get("VolumeType", "N/A"),
        }
        for page in pages
        for volume in page.get("Volumes", [])
        for attachment in volume.get("Attachments", [])
        if attachment.get("InstanceId") == instance_id
    ]


def suggested_dashboard_name(instance_name: str, region: str, instance_id: str = "") -> str:
    name = re.sub(r"[^A-Za-z0-9_-]+", "-", instance_name).strip("-")[:80] or "instance"
    return f"EBS_Analysis_{name}_{region}_{instance_id}_{uuid.uuid4().hex[:12]}"


def create_cloudwatch_dashboard(profile: str, region: str, instance_name: str, volumes: list[dict],
                                *, dashboard_name: str | None = None, instance_id: str = "") -> str:
    dashboard_name = suggested_dashboard_name(instance_name, region, instance_id) if dashboard_name is None else dashboard_name
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,255}", dashboard_name):
        raise ValueError("Dashboard name must contain 1–255 letters, digits, underscores or hyphens.")
    if not volumes:
        raise ValueError("Load at least one attached EBS volume before publishing a dashboard.")
    widgets = []
    for volume in volumes:
        vid = volume["VolumeId"]
        device = volume["Device"]
        widgets.append({
            "type": "metric",
            "properties": {
                "metrics": [
                    [{"expression": "(m1+m2)/(PERIOD(m1)-m3)", "label": "Average IOPS/s when volume is active", "id": "e1", "region": region}],
                    [{"expression": "(m1+m2)/(PERIOD(m1))", "label": "Average IOPS/s", "id": "e2", "visible": False, "region": region}],
                    [{"expression": "(m4+m5)/(PERIOD(m4)-m3)", "label": "Average Throughput when volume is active", "id": "e3", "region": region}],
                    [{"expression": "(m4+m5)/(PERIOD(m4))", "label": "Average Throughput in bytes", "id": "e4", "visible": False, "region": region}],
                    ["AWS/EBS", "VolumeReadOps", "VolumeId", vid, {"region": region, "id": "m1", "visible": False}],
                    ["AWS/EBS", "VolumeWriteOps", "VolumeId", vid, {"region": region, "id": "m2", "visible": False}],
                    ["AWS/EBS", "VolumeIdleTime", "VolumeId", vid, {"region": region, "id": "m3", "visible": False}],
                    ["AWS/EBS", "VolumeReadBytes", "VolumeId", vid, {"region": region, "id": "m4", "visible": False}],
                    ["AWS/EBS", "VolumeWriteBytes", "VolumeId", vid, {"region": region, "id": "m5", "visible": False}],
                ],
                "view": "timeSeries", "stacked": False, "region": region,
                "stat": "Sum", "period": 60, "title": device,
            },
        })
    client = get_client("cloudwatch", profile, region)
    try:
        client.get_dashboard(DashboardName=dashboard_name)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ResourceNotFound":
            raise
    else:
        raise FileExistsError("A CloudWatch dashboard with this name already exists. Choose a different name; it was not overwritten.")
    response = client.put_dashboard(
        DashboardName=dashboard_name,
        DashboardBody=json.dumps({"widgets": widgets}),
    )
    if response.get("DashboardValidationMessages"):
        raise RuntimeError(f"CloudWatch returned validation messages for dashboard {dashboard_name}. "
                           "Review the dashboard in CloudWatch before using it; the write may have succeeded.")
    return dashboard_name
