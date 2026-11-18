from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, TypeVar

from aws.session import get_ssm_client

COMMAND_DOCUMENTS = {
    "Linux": "AWS-RunShellScript",
    "Windows": "AWS-RunPowerShellScript",
}
MAX_COMMAND_TARGETS = 50
ACTIVE_COMMAND_STATUSES = {"Pending", "InProgress", "Delayed", "Cancelling"}
MAX_EXECUTION_TIMEOUT_SECONDS = 172800
T = TypeVar("T")


@dataclass
class CommandResult:
    instance_id: str
    status: str
    stdout: str
    stderr: str
    command_id: str
    tracking_timed_out: bool = False


def send_command(
    profile: str,
    region: str,
    instance_ids: list[str],
    command: str,
    os_family: str = "Linux",
    timeout_seconds: int = 300,
    poll_interval: float = 1.5,
    max_targets: int = MAX_COMMAND_TARGETS,
    *,
    run_with_sso: Callable | None = None,
    delivery_timeout_seconds: int | None = None,
    execution_timeout_seconds: int | None = None,
) -> list[CommandResult]:
    """Submit a command and collect its invocations without replaying the send.

    An optional SSO recovery callback wraps individual AWS requests, rather
    than this entire function, so polling resumes with the same command ID.
    """
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

    parameters = {"commands": [command]}
    if execution_timeout_seconds is not None:
        execution_timeout_seconds = int(execution_timeout_seconds)
        if not 1 <= execution_timeout_seconds <= MAX_EXECUTION_TIMEOUT_SECONDS:
            raise ValueError(f"Execution timeout must be between 1 and {MAX_EXECUTION_TIMEOUT_SECONDS} seconds.")
        parameters["executionTimeout"] = [str(execution_timeout_seconds)]
    delivery_timeout = max(30, int(timeout_seconds))
    if delivery_timeout_seconds is not None:
        delivery_timeout = int(delivery_timeout_seconds)
        if not 30 <= delivery_timeout <= 259200:
            raise ValueError("Delivery timeout must be between 30 and 259200 seconds.")

    client = None

    def request(operation: Callable[[object], T]) -> T:
        """Recover one AWS request, never replay the submitted operation.

        The recovery callback retries only rejected authentication requests.
        Constructing a new client on that retry picks up the refreshed SSO
        credentials while keeping the original command ID and pending targets.
        """
        attempts = 0

        def attempt():
            nonlocal client, attempts
            if client is None or attempts:
                client = get_ssm_client(profile, region)
            attempts += 1
            return operation(client)

        return run_with_sso(profile, attempt) if run_with_sso else attempt()

    # Submit once for the selected fleet.  Apart from reducing API calls this
    # keeps the operation auditable as a single SSM command ID.
    response = request(lambda ssm: ssm.send_command(
        InstanceIds=list(instance_ids),
        DocumentName=document,
        Parameters=parameters,
        TimeoutSeconds=delivery_timeout,
        MaxConcurrency=str(min(len(instance_ids), 10)),
        MaxErrors="0",
    ))
    command_id = response["Command"]["CommandId"]
    deadline = time.monotonic() + timeout_seconds
    pending = set(instance_ids)
    completed: dict[str, CommandResult] = {}
    latest: dict[str, dict] = {}

    while pending and time.monotonic() < deadline:
        for instance_id in list(pending):
            if time.monotonic() >= deadline:
                break
            try:
                invocation = request(lambda ssm: ssm.get_command_invocation(
                    CommandId=command_id,
                    InstanceId=instance_id,
                ))
            except client.exceptions.InvocationDoesNotExist:
                # SSM may need a few seconds to materialise an invocation.
                continue
            status = invocation.get("Status", "Unknown")
            latest[instance_id] = invocation
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
            time.sleep(min(poll_interval, max(0, deadline - time.monotonic())))

    for instance_id in pending:
        invocation = latest.get(instance_id, {})
        detail = (f"Local tracking stopped after {timeout_seconds} seconds; the remote command may still be running. "
                  f"Remote status is unknown. CommandId: {command_id}. No cancellation was requested.")
        stderr = invocation.get("StandardErrorContent", "")
        completed[instance_id] = CommandResult(
            instance_id=instance_id,
            status="TimedOut",
            stdout=invocation.get("StandardOutputContent", ""),
            stderr=f"{stderr}\n{detail}" if stderr else detail,
            command_id=command_id,
            tracking_timed_out=True,
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
