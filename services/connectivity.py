from __future__ import annotations

import re
import textwrap
from collections import defaultdict, deque
from pathlib import Path

import pandas as pd

from services.ssm import MAX_EXECUTION_TIMEOUT_SECONDS, send_command
from utils.logging import get_logger

logger = get_logger("connectivity")
ROW_KEYS = ("service", "destination", "port", "scope")
DELIVERY_TIMEOUT_SECONDS = 60


def normalize_header(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).strip().lower())


def normalize_value(value: object) -> str:
    text = "" if value is None or pd.isna(value) else str(value).strip()
    return text.replace("|", "/").replace("\n", " ").replace("\r", " ")


def normalize_scope(value: object) -> str:
    normalized = normalize_header(value)
    if normalized in {"internal", "interno", "private", "privado", "local"}:
        return "internal"
    if normalized in {"external", "externo", "public", "publico"}:
        return "external"
    return normalize_value(value) or "unknown"


def _validated_row(row: dict, row_number: int) -> dict[str, str]:
    values = {key: normalize_value(row.get(key, "")) for key in ROW_KEYS}
    if not all(values.values()):
        raise ValueError(f"Row {row_number} is missing a required value.")
    if any("\x00" in value for value in values.values()):
        raise ValueError(f"Row {row_number} contains a NUL character.")
    port = values["port"]
    if not port.isascii() or not port.isdigit() or not 1 <= int(port) <= 65535:
        raise ValueError(f"Row {row_number} has an invalid port: {port}")
    if values["destination"].startswith("-"):
        raise ValueError(f"Row {row_number} has an invalid destination: it cannot start with '-'.")
    protocol = normalize_value(row.get("protocol", "TCP")).upper() or "TCP"
    if protocol != "TCP":
        raise ValueError(f"Row {row_number} has unsupported protocol {protocol}; only TCP is supported.")
    values["scope"] = normalize_scope(values["scope"])
    values["protocol"] = protocol
    return values


def _connection_timeout(value: int) -> int:
    value = int(value)
    if not 1 <= value <= 300:
        raise ValueError("The connectivity timeout per destination must be between 1 and 300 seconds.")
    return value


def parse_connectivity_csv(csv_path: str | Path) -> list[dict[str, str]]:
    path = Path(csv_path)
    if not path.is_file():
        raise ValueError(f"CSV file not found: {path}")

    dataframe = pd.read_csv(path, dtype=str, keep_default_na=False)
    if dataframe.empty:
        raise ValueError("The CSV file is empty.")

    columns = {normalize_header(column): column for column in dataframe.columns}

    def pick_column(required: bool, *aliases: str):
        for alias in aliases:
            key = normalize_header(alias)
            if key in columns:
                return columns[key]
        if required:
            raise ValueError(
                "Missing required CSV column. Accepted names: " + ", ".join(aliases)
            )
        return None

    service_column = pick_column(True, "service_name", "service", "nombre del servicio", "nombre_servicio", "servicio", "name")
    destination_column = pick_column(True, "destination", "destino", "ip destino", "ip_destino", "host", "target")
    port_column = pick_column(True, "port", "puerto")
    scope_column = pick_column(True, "scope", "type", "tipo", "internal/external", "interno o externo", "interno_externo")
    protocol_column = pick_column(False, "protocol", "protocolo")

    rows: list[dict[str, str]] = []
    for index, row in dataframe.iterrows():
        rows.append(_validated_row({
            "service": row[service_column], "destination": row[destination_column],
            "port": row[port_column], "scope": row[scope_column],
            "protocol": row[protocol_column] if protocol_column else "TCP",
        }, index + 2))

    if not rows:
        raise ValueError("No valid connectivity rows were found in the CSV.")

    logger.info("Connectivity CSV parsed | file=%s | services=%d", path.name, len(rows))
    return rows


def build_connectivity_script(rows: list[dict[str, str]], timeout_seconds: int) -> str:
    timeout_seconds = _connection_timeout(timeout_seconds)
    nc_timeout = timeout_seconds
    command_timeout = timeout_seconds + 2
    rows = [_validated_row(row, index + 2) for index, row in enumerate(rows)]
    sanitized_rows = [
        "|".join(
            normalize_value(row[key])
            for key in ROW_KEYS
        )
        for row in rows
    ]
    heredoc_rows = "\n".join(sanitized_rows)

    script = textwrap.dedent(
        f"""\
        set +e
        CONNECTION_TIMEOUT={command_timeout}
        NC_TIMEOUT={nc_timeout}
        NC_BIN="$(command -v nc || command -v ncat || true)"

        if [ -z "$NC_BIN" ]; then
            printf 'nc command not found\\n' >&2
            exit 127
        fi

        while IFS='|' read -r service destination port scope; do
            [ -z "$service" ] && continue
            start_ms=$(date +%s%3N 2>/dev/null)
            case "$start_ms" in
                ""|*[!0-9]*) start_ms=$(( $(date +%s) * 1000 ));;
            esac

            output=""
            code=0
            if command -v timeout >/dev/null 2>&1; then
                output=$(timeout "${{CONNECTION_TIMEOUT}}s" "$NC_BIN" -vz -w "$NC_TIMEOUT" "$destination" "$port" 2>&1)
                code=$?
            else
                output=$("$NC_BIN" -vz -w "$NC_TIMEOUT" "$destination" "$port" 2>&1)
                code=$?
            fi

            end_ms=$(date +%s%3N 2>/dev/null)
            case "$end_ms" in
                ""|*[!0-9]*) end_ms=$(( $(date +%s) * 1000 ));;
            esac
            duration=$(( end_ms - start_ms ))
            normalized_output=$(printf '%s' "$output" | tr '\\r\\n' ' ' | tr '|' '/')
            status="KO"
            detail="connection failed"

            if [ "$code" -eq 0 ]; then
                status="OK"
                detail="connected"
            elif [ "$code" -eq 124 ]; then
                status="TIMEOUT"
                detail="command timeout"
            else
                case "$normalized_output" in
                    *timed*out*|*Timed*out*) status="TIMEOUT";;
                    *refused*|*Refused*) status="REFUSED";;
                    *unreachable*|*Unreachable*|*No*route*) status="UNREACHABLE";;
                esac
                [ -n "$normalized_output" ] && detail="$normalized_output"
            fi

            printf '%s|%s|%s|%s|%s|%s|%s\\n' "$service" "$destination" "$port" "$scope" "$status" "$duration" "$detail"
        done <<'EOF_CONNECTIVITY'
        """
    ).lstrip()
    # Dedent the shell code before appending data. Interpolating several data
    # rows into the template changes its minimum indentation and breaks EOF.
    return script + heredoc_rows + "\nEOF_CONNECTIVITY\n"


def parse_connectivity_output(stdout: str) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    for raw_line in (stdout or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split("|", 6)
        if len(parts) < 6:
            continue
        while len(parts) < 7:
            parts.append("")
        service, destination, port, scope, status, duration, detail = parts[:7]
        results.append({
            "service": service,
            "destination": destination,
            "port": port,
            "scope": scope,
            "status": status or "KO",
            "duration": duration,
            "detail": detail,
        })
    return results


def validate_connectivity_csv(
    profile: str,
    region: str,
    instance_id: str,
    rows: list[dict[str, str]],
    timeout_seconds: int,
    *,
    run_with_sso=None,
):
    if not rows:
        raise ValueError("Load a connectivity CSV before running validation.")
    timeout_seconds = _connection_timeout(timeout_seconds)
    rows = [_validated_row(row, index + 2) for index, row in enumerate(rows)]
    execution_timeout = max(30, len(rows) * (timeout_seconds + 2) + 10)
    if execution_timeout > MAX_EXECUTION_TIMEOUT_SECONDS:
        raise ValueError("The connectivity matrix exceeds SSM's execution limit; split it into smaller batches.")
    script = build_connectivity_script(rows, timeout_seconds)
    logger.info(
        "Starting CSV connectivity validation | instance=%s | services=%d | timeout=%ss | region=%s",
        instance_id, len(rows), timeout_seconds, region,
    )
    command_timeout = execution_timeout + DELIVERY_TIMEOUT_SECONDS + 30
    command_results = send_command(
        profile,
        region,
        [instance_id],
        script,
        os_family="Linux",
        timeout_seconds=command_timeout,
        poll_interval=1.0,
        run_with_sso=run_with_sso,
        delivery_timeout_seconds=DELIVERY_TIMEOUT_SECONDS,
        execution_timeout_seconds=execution_timeout,
    )
    if not command_results:
        raise RuntimeError("SSM returned no command result.")
    result = command_results[0]
    parsed = parse_connectivity_output(result.stdout)
    if not parsed and not result.tracking_timed_out and (result.stderr or result.status != "Success"):
        raise RuntimeError(f"SSM command {result.command_id} ended with status {result.status}: "
                           f"{result.stderr.strip() or 'No connectivity results were returned.'}")
    by_destination = defaultdict(deque)
    for row in parsed:
        by_destination[tuple(row[key] for key in ROW_KEYS)].append(row)
    ordered_results = []
    for row in rows:
        matches = by_destination[tuple(row[key] for key in ROW_KEYS)]
        if matches:
            ordered_results.append(matches.popleft())
        else:
            ordered_results.append({
                **{key: row[key] for key in ROW_KEYS},
                "status": "UNKNOWN" if result.tracking_timed_out else "NO_RESULT",
                "duration": "",
                "detail": (f"No result was returned for this destination; the probe may have run. "
                           f"SSM status: {result.status}. CommandId: {result.command_id}. "
                           + ("Remote execution status is unknown. " if result.tracking_timed_out else "")
                           + result.stderr.strip()),
            })
    logger.info(
        "CSV connectivity validation completed | instance=%s | results=%d | status=%s",
        instance_id, len(ordered_results), result.status,
    )
    return ordered_results, result
