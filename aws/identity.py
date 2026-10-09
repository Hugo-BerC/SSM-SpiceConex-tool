from __future__ import annotations

from aws.session import get_boto3_session
from utils.logging import get_logger

logger = get_logger("aws.identity")


def get_caller_identity(profile: str | None = None, region: str | None = None) -> dict:
    """Validate the active AWS credentials/session without persisting account data."""
    logger.info("Validating AWS session with STS | profile=%r | region=%s", profile, region)
    identity = get_boto3_session(profile, region).client("sts").get_caller_identity()
    logger.info("AWS STS validation succeeded | profile=%r | region=%s", profile, region)
    return identity
