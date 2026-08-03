#!/bin/sh
# setup.sh -- one-command setup: runs install.py inside this repo checkout.
exec python3 "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/install.py" "$@"
