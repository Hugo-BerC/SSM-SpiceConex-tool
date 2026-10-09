from __future__ import annotations

import platform
import shlex
import subprocess


def shell_quote(value: str) -> str:
    """Quote a value for the shell used by the external terminal."""
    if platform.system() == "Windows":
        return subprocess.list2cmdline([value])
    return shlex.quote(value)


def build_start_session_command(aws_path: str, instance_id: str, profile: str, region: str) -> str:
    return " ".join([
        shell_quote(aws_path),
        "ssm", "start-session",
        "--target", shell_quote(instance_id),
        "--profile", shell_quote(profile),
        "--region", shell_quote(region),
    ])


def validate_port(value: str, label: str) -> int:
    if not value or not value.isdigit():
        raise ValueError(f"{label} must be numeric.")
    port = int(value)
    if not 0 <= port <= 65535:
        raise ValueError(f"{label} must be between 0 and 65535.")
    return port


def build_port_forward_command(
    aws_path: str,
    instance_id: str,
    profile: str,
    region: str,
    local_port: int,
    remote_port: int,
) -> str:
    return " ".join([
        shell_quote(aws_path),
        "ssm", "start-session",
        "--target", shell_quote(instance_id),
        "--profile", shell_quote(profile),
        "--region", shell_quote(region),
        "--document-name", "AWS-StartPortForwardingSession",
        "--parameters", shell_quote(f"portNumber={remote_port},localPortNumber={local_port}"),
    ])
