from __future__ import annotations

import json

from aws.session import get_ec2_client, get_boto3_session


def list_instance_volumes(profile: str, region: str, instance_id: str) -> list[dict]:
    response = get_ec2_client(profile, region).describe_volumes(
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
        for volume in response.get("Volumes", [])
        for attachment in volume.get("Attachments", [])
    ]


def create_cloudwatch_dashboard(profile: str, region: str, instance_name: str, volumes: list[dict]) -> str:
    dashboard_name = f"EBS_Analysis_{instance_name.split('.')[0]}"
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
    get_boto3_session(profile, region).client("cloudwatch").put_dashboard(
        DashboardName=dashboard_name,
        DashboardBody=json.dumps({"widgets": widgets}),
    )
    return dashboard_name
