from __future__ import annotations

import time
from dataclasses import dataclass

from aws.session import get_ssm_client

COMMAND_DOCUMENTS = {
    "Linux": "AWS-RunShellScript",
    "Windows": "AWS-RunPowerShellScript",
}


@dataclass
class CommandResult:
    instance_id: str
    status: str
    stdout: str
    stderr: str
    command_id: str


def send_command(
    profile: str,
    region: str,
    instance_ids: list[str],
    command: str,
    os_family: str = "Linux",
    timeout_seconds: int = 300,
    poll_interval: float = 1.5,
) -> list[CommandResult]:
    if not instance_ids:
        raise ValueError("At least one instance must be selected.")
    if not command.strip():
        raise ValueError("Command cannot be empty.")
    document = COMMAND_DOCUMENTS.get(os_family)
    if not document:
        raise ValueError(f"Unsupported OS family: {os_family}")

    client = get_ssm_client(profile, region)
    results: list[CommandResult] = []
    for instance_id in instance_ids:
        response = client.send_command(
            InstanceIds=[instance_id],
            DocumentName=document,
            Parameters={"commands": [command]},
        )
        command_id = response["Command"]["CommandId"]
        deadline = time.monotonic() + timeout_seconds
        while True:
            invocation = client.get_command_invocation(
                CommandId=command_id,
                InstanceId=instance_id,
            )
            status = invocation.get("Status", "Unknown")
            if status not in {"Pending", "InProgress", "Delayed", "Cancelling"}:
                break
            if time.monotonic() >= deadline:
                raise TimeoutError(f"SSM command timed out on {instance_id} ({command_id}).")
            time.sleep(poll_interval)
        results.append(
            CommandResult(
                instance_id=instance_id,
                status=status,
                stdout=invocation.get("StandardOutputContent", ""),
                stderr=invocation.get("StandardErrorContent", ""),
                command_id=command_id,
            )
        )
    return results


def validate_connectivity_command(os_family: str, host: str, port: int) -> str:
    if os_family == "Windows":
        return (
            f"$r=Test-NetConnection -ComputerName '{host}' -Port {port} -WarningAction SilentlyContinue; "
            "if ($r.TcpTestSucceeded) { 'TCP_OK' } else { 'TCP_FAILED' }"
        )
    return (
        "python3 -c "
        f"\"import socket; s=socket.socket(); s.settimeout(5); s.connect(('{host}',{port})); "
        "s.close(); print('TCP_OK')\""
    )
