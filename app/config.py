from __future__ import annotations
import os

from app.version import __version__

APP_NAME = "SSM-SpiceConex"
DEFAULT_REGION = "eu-west-1"
AWS_REGIONS = ("eu-west-1", "eu-south-2", "eu-central-1")
AWS_TIMEOUT_SECONDS = int(os.environ.get("SSM_SPICECONEX_AWS_TIMEOUT", "20"))

APP_VERSION = __version__
GITHUB_REPOSITORY = os.environ.get("SPICECONEX_GITHUB_REPOSITORY", "Hugo-BerC/SSM-SpiceConex-tool")
GITHUB_BRANCH = os.environ.get("SPICECONEX_GITHUB_BRANCH", "main")
