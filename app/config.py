from __future__ import annotations
import os
import warnings

from app.version import __version__

APP_NAME = "SSM-SpiceConex"
DEFAULT_REGION = "eu-west-1"
AWS_REGIONS = ("eu-west-1", "eu-south-2", "eu-central-1")
def _aws_timeout():
    try:
        value = int(os.environ.get("SSM_SPICECONEX_AWS_TIMEOUT", "20"))
        if value <= 0:
            raise ValueError()
        return value
    except ValueError:
        warnings.warn("Invalid SSM_SPICECONEX_AWS_TIMEOUT; using 20 seconds.", RuntimeWarning, stacklevel=2)
        return 20


AWS_TIMEOUT_SECONDS = _aws_timeout()

APP_VERSION = __version__
GITHUB_REPOSITORY = os.environ.get("SPICECONEX_GITHUB_REPOSITORY", "Hugo-BerC/SSM-SpiceConex-tool")
GITHUB_BRANCH = os.environ.get("SPICECONEX_GITHUB_BRANCH", "main")
