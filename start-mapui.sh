#!/bin/sh
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PY="$SCRIPT_DIR/mib2nds-tool/.venv/bin/python"
if [ ! -x "$PY" ]; then
    PY=python3
fi
exec "$PY" "$SCRIPT_DIR/mib2nds-tool/mapui.py" "$@"
