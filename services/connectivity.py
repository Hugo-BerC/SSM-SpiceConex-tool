from __future__ import annotations

import re
import textwrap
from pathlib import Path

import pandas as pd

from services.ssm import send_command
from utils.logging import get_logger

logger = get_logger("connectivity")


def normalize_header(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).strip().lower())


def normalize_value(value: object) -> str:
    text = "" if value is None else str(value).strip()
    return text.replace("|", "/").replace("\n", " ").replace("\r", " ")


def normalize_scope(value: object) -> str:
    normalized = normalize_header(value)
    if normalized in {"internal", "interno", "private", "privado", "local"}:
        return "internal"
    if normalized in {"external", "externo", "public", "publico"}:
        return "external"
    return normalize_value(value) or "unknown"


def parse_connectivity_csv(csv_path: str | Path) -> list[dict[str, str]]:
    path = Path(csv_path)
    if not path.is_file():
        raise ValueError(f"CSV file not found: {path}")

    dataframe = pd.read_csv(path)
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
        service = normalize_value(row[service_column])
        destination = normalize_value(row[destination_column])
        port = normalize_value(row[port_column])
        scope = normalize_scope(row[scope_column])
        protocol = normalize_value(row[protocol_column]).upper() if protocol_column else "TCP"

        if not service or not destination or not port or not scope:
            raise ValueError(f"Row {index + 2} is missing a required value.")
        if not port.isdigit() or not 1 <= int(port) <= 65535:
            raise ValueError(f"Row {index + 2} has an invalid port: {port}")

        rows.append({
            "service": service,
            "destination": destination,
            "port": port,
            "scope": scope,
            "protocol": protocol or "TCP",
        })

    if not rows:
        raise ValueError("No valid connectivity rows were found in the CSV.")

    logger.info("Connectivity CSV parsed | file=%s | services=%d", path.name, len(rows))
    return rows


def build_connectivity_script(rows: list[dict[str, str]], timeout_seconds: int) -> str:
    timeout_seconds = max(1, int(timeout_seconds))
    nc_timeout = max(1, min(timeout_seconds, 10))
    command_timeout = max(timeout_seconds + 2, nc_timeout + 1)
    sanitized_rows = [
        "|".join(
            normalize_value(row[key])
            for key in ("service", "destination", "port", "scope")
        )
        for row in rows
    ]
    heredoc_rows = "\n".join(sanitized_rows)

    return textwrap.dedent(
        f"""\
        set +e
        CONNECTION_TIMEOUT={command_timeout}
        NC_TIMEOUT={nc_timeout}
        NC_BIN="$(command -v nc || command -v ncat || true)"

        if [ -z "$NC_BIN" ]; then
            printf 'ERROR|nc not found|0|unknown|KO|0|nc command not found\\n'
            exit 0
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
        {heredoc_rows}
        EOF_CONNECTIVITY
        """
    ).strip()


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
):
    if not rows:
        raise ValueError("Load a connectivity CSV before running validation.")
    script = build_connectivity_script(rows, timeout_seconds)
    logger.info(
        "Starting CSV connectivity validation | instance=%s | services=%d | timeout=%ss | region=%s",
        instance_id, len(rows), timeout_seconds, region,
    )
    command_timeout = max(int(timeout_seconds) + 10, 15)
    command_results = send_command(
        profile,
        region,
        [instance_id],
        script,
        os_family="Linux",
        timeout_seconds=command_timeout,
        poll_interval=1.0,
    )
    if not command_results:
        raise RuntimeError("SSM returned no command result.")
    result = command_results[0]
    parsed = parse_connectivity_output(result.stdout)
    if not parsed and result.stderr:
        raise RuntimeError(result.stderr.strip())
    logger.info(
        "CSV connectivity validation completed | instance=%s | results=%d | status=%s",
        instance_id, len(parsed), result.status,
    )
    return parsed, result
