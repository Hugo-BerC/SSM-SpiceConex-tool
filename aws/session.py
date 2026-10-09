from __future__ import annotations

import boto3
from botocore.config import Config

from app.config import AWS_TIMEOUT_SECONDS, DEFAULT_REGION
from utils.logging import get_logger

logger = get_logger("aws.session")


def _client_config() -> Config:
    timeout = max(5, AWS_TIMEOUT_SECONDS)
    connect_timeout = min(5, timeout)
    config = Config(
        connect_timeout=connect_timeout,
        read_timeout=timeout,
        retries={"mode": "standard", "max_attempts": 1},
        tcp_keepalive=True,
    )
    logger.debug(
        "AWS client config | connect_timeout=%ss | read_timeout=%ss | max_attempts=2",
        connect_timeout, timeout,
    )
    return config


def get_boto3_session(profile: str | None = None, region: str | None = None):
    region = (region or DEFAULT_REGION).strip()
    kwargs = {"region_name": region}
    if profile:
        kwargs["profile_name"] = profile
    logger.info("Creating boto3 session | profile=%r | region=%s", profile, region)
    return boto3.Session(**kwargs)


def get_ec2_client(profile: str | None = None, region: str | None = None):
    logger.info("Creating EC2 client | profile=%r | region=%s", profile, region)
    return get_boto3_session(profile, region).client("ec2", config=_client_config())


def get_ssm_client(profile: str | None = None, region: str | None = None):
    logger.info("Creating SSM client | profile=%r | region=%s", profile, region)
    return get_boto3_session(profile, region).client("ssm", config=_client_config())


def get_sts_client(profile: str | None = None, region: str | None = None):
    logger.info("Creating STS client | profile=%r | region=%s", profile, region)
    return get_boto3_session(profile, region).client("sts", config=_client_config())


def clear_session_cache():
    logger.info("Clearing AWS session/client cache")
    # Session/client factories are intentionally not cached: boto3 credential
    # providers, especially SSO, may refresh state between worker threads.
