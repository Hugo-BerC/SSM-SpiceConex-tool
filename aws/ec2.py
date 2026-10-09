from __future__ import annotations

from aws.session import get_ec2_client
from models.instance import EC2Instance
from utils.logging import get_logger

logger = get_logger("aws.ec2")


def list_instances(profile: str, region: str) -> list[EC2Instance]:
    """List every EC2 instance visible to the selected AWS profile/region.

    Uses boto3 directly instead of shelling out to the AWS CLI. This keeps the
    desktop application independent of CLI output/quoting differences across
    Windows, macOS and Linux while still using the same AWS credential chain
    (including IAM Identity Center / SSO after ``aws sso login``).
    """
    logger.info("Starting EC2 fleet query | profile=%r | region=%s", profile, region)
    client = get_ec2_client(profile, region)
    paginator = client.get_paginator("describe_instances")

    instances: list[EC2Instance] = []
    for page in paginator.paginate():
        for reservation in page.get("Reservations", []):
            for instance in reservation.get("Instances", []):
                instances.append(EC2Instance.from_boto(instance))

    logger.info("EC2 fleet query completed | instances=%d | profile=%r | region=%s", len(instances), profile, region)
    return instances


def get_instance_volumes(profile: str, region: str, instance_id: str) -> list[dict]:
    """Return EBS volume metadata in one API call, avoiding N+1 calls."""
    logger.info("Loading EBS volumes | instance=%s | region=%s", instance_id, region)
    response = get_ec2_client(profile, region).describe_volumes(
        Filters=[{"Name": "attachment.instance-id", "Values": [instance_id]}]
    )
    volumes = [
        {
            "Device": attachment["Device"],
            "VolumeId": volume["VolumeId"],
            "Iops": volume.get("Iops", "N/A"),
            "Throughput": volume.get("Throughput", "N/A"),
        }
        for volume in response.get("Volumes", [])
        for attachment in volume.get("Attachments", [])
    ]
    logger.info("EBS volume query completed | instance=%s | volumes=%d", instance_id, len(volumes))
    return volumes
