#!/bin/sh
PORT=${PORT:-5000}
[ "$1" = "--port" ] && [ -n "$2" ] && PORT=$2

if command -v fuser >/dev/null 2>&1 && fuser -n tcp "$PORT" >/dev/null 2>&1; then
    fuser -k -TERM -n tcp "$PORT" >/dev/null 2>&1
    echo "mapui-server gestopt (poort $PORT)."
elif pkill -f "mib2nds-tool/mapui.py" 2>/dev/null; then
    echo "mapui-server gestopt."
else
    echo "Geen actieve mapui-server gevonden op poort $PORT." >&2
    exit 1
fi
