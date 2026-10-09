from __future__ import annotations
import os

def aws_environment() -> dict[str, str]:
    env = os.environ.copy()
    home = os.path.expanduser("~")
    env.setdefault("HOME", home)
    env.setdefault("AWS_SDK_LOAD_CONFIG", "1")
    env.setdefault("AWS_EC2_METADATA_DISABLED", "true")
    env.setdefault("AWS_PAGER", "")
    env.setdefault("AWS_CONFIG_FILE", os.path.join(home, ".aws", "config"))
    env.setdefault("AWS_SHARED_CREDENTIALS_FILE", os.path.join(home, ".aws", "credentials"))
    return env
