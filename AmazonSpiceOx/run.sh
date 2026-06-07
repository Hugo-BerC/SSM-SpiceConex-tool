#!/bin/sh
set -eu

APP_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
APP_FILE="$APP_DIR/ssm_powerconnect.py"
PYTHON_BIN="${PYTHON:-python3}"

if [ -x "$APP_DIR/.venv/bin/python" ]; then
    PYTHON_BIN="$APP_DIR/.venv/bin/python"
fi

if command -v python-gui >/dev/null 2>&1 && [ -z "${DISPLAY:-}" ]; then
    exec python-gui "$APP_FILE" "$@"
fi

exec "$PYTHON_BIN" "$APP_FILE" "$@"
