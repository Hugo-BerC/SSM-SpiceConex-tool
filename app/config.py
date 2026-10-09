from __future__ import annotations
import os

APP_NAME = "SSM-SpiceConex"
DEFAULT_REGION = "eu-west-1"
AWS_REGIONS = tuple(dict.fromkeys([DEFAULT_REGION, "us-east-1", "us-west-2", "eu-west-1", "eu-central-1", "ap-southeast-1"]))
AWS_TIMEOUT_SECONDS = int(os.environ.get("SSM_SPICECONEX_AWS_TIMEOUT", "20"))

APP_VERSION = "0.4.2-dev"
GITHUB_REPOSITORY = os.environ.get("SPICECONEX_GITHUB_REPOSITORY", "YOUR_ORG/SSM-SpiceConex")
GITHUB_BRANCH = os.environ.get("SPICECONEX_GITHUB_BRANCH", "main")
