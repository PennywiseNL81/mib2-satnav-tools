#!/bin/sh
# stop-mapui.sh -- stop the mapui server (via its /api/shutdown endpoint).
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PY="$SCRIPT_DIR/mib2nds-tool/.venv/bin/python"
if [ ! -x "$PY" ]; then
    PY=python3
fi
PORT=${PORT:-5000}
[ "$1" = "--port" ] && [ -n "$2" ] && PORT=$2
exec "$PY" "$SCRIPT_DIR/mib2nds-tool/stop_mapui.py" --port "$PORT"
