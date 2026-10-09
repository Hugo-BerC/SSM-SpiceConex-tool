from __future__ import annotations
import json
import shlex
import shutil
import subprocess
from app.config import AWS_TIMEOUT_SECONDS
from aws.environment import aws_environment

def get_aws_cli_path() -> str | None:
    path = shutil.which("aws")
    if path:
        return path
    if shutil.which("aws.exe"):
        return shutil.which("aws.exe")
    return None

def format_aws_cli_error(args, result) -> str:
    details = (result.stderr or result.stdout or "").strip()
    return details or f"aws {shlex.join(args)} exited with status {result.returncode}"

def run_aws_cli(args, check=True):
    aws_path = get_aws_cli_path()
    if not aws_path:
        raise RuntimeError("AWS CLI not found in PATH.")
    result = subprocess.run([aws_path, *args], capture_output=True, text=True,
                            env=aws_environment(), timeout=AWS_TIMEOUT_SECONDS)
    if check and result.returncode != 0:
        raise RuntimeError(format_aws_cli_error(args, result))
    return result

def run_aws_json(args):
    result = run_aws_cli([*args, "--output", "json"])
    try:
        return json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Could not parse AWS CLI JSON output: {exc}") from exc
