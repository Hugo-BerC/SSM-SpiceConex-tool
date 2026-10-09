#!/bin/sh
set -eu

APP_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PYTHON_BIN="$APP_DIR/.venv/bin/python"

if [ ! -x "$PYTHON_BIN" ]; then
    echo "SSM-SpiceConex is not set up yet. Run ./setup.sh first." >&2
    exit 1
fi

exec "$PYTHON_BIN" "$APP_DIR/ssm_spiceconex.py" "$@"
