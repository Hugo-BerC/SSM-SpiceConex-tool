from __future__ import annotations

import time
from dataclasses import dataclass

from aws.session import get_ssm_client

COMMAND_DOCUMENTS = {
    "Linux": "AWS-RunShellScript",
    "Windows": "AWS-RunPowerShellScript",
}
MAX_COMMAND_TARGETS = 50
ACTIVE_COMMAND_STATUSES = {"Pending", "InProgress", "Delayed", "Cancelling"}


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
    max_targets: int = MAX_COMMAND_TARGETS,
) -> list[CommandResult]:
    if not instance_ids:
        raise ValueError("At least one instance must be selected.")
    if not command.strip():
        raise ValueError("Command cannot be empty.")
    if len(instance_ids) > max_targets:
        raise ValueError(
            f"Run Command is limited to {max_targets} targets per operation; "
            "split the selection into smaller batches."
        )
    document = COMMAND_DOCUMENTS.get(os_family)
    if not document:
        raise ValueError(f"Unsupported OS family: {os_family}")

    client = get_ssm_client(profile, region)
    # Submit once for the selected fleet.  Apart from reducing API calls this
    # keeps the operation auditable as a single SSM command ID.
    response = client.send_command(
        InstanceIds=list(instance_ids),
        DocumentName=document,
        Parameters={"commands": [command]},
        TimeoutSeconds=max(30, int(timeout_seconds)),
        MaxConcurrency=str(min(len(instance_ids), 10)),
        MaxErrors="0",
    )
    command_id = response["Command"]["CommandId"]
    deadline = time.monotonic() + timeout_seconds
    pending = set(instance_ids)
    completed: dict[str, CommandResult] = {}

    while pending and time.monotonic() < deadline:
        for instance_id in list(pending):
            try:
                invocation = client.get_command_invocation(
                    CommandId=command_id,
                    InstanceId=instance_id,
                )
            except client.exceptions.InvocationDoesNotExist:
                # SSM may need a few seconds to materialise an invocation.
                continue
            status = invocation.get("Status", "Unknown")
            if status in ACTIVE_COMMAND_STATUSES:
                continue
            completed[instance_id] = CommandResult(
                instance_id=instance_id,
                status=status,
                stdout=invocation.get("StandardOutputContent", ""),
                stderr=invocation.get("StandardErrorContent", ""),
                command_id=command_id,
            )
            pending.remove(instance_id)
        if pending:
            time.sleep(poll_interval)

    for instance_id in pending:
        completed[instance_id] = CommandResult(
            instance_id=instance_id,
            status="TimedOut",
            stdout="",
            stderr=f"No terminal SSM invocation was returned within {timeout_seconds} seconds.",
            command_id=command_id,
        )
    return [completed[instance_id] for instance_id in instance_ids]


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
