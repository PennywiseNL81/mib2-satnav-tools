#!/usr/bin/env python3
"""stop_mapui.py -- stop the running mapui server via its /api/shutdown
endpoint.

The server only accepts shutdown requests from loopback, so this only
works against a server bound to 127.0.0.1/::1. Using the endpoint (rather
than fuser/pkill) lets the server shut down cleanly and works on any
platform (Windows included).

Usage:
    stop_mapui.py [--host 127.0.0.1] [--port 5000]
"""
import argparse
import sys
import urllib.error
import urllib.request


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5000)
    args = ap.parse_args(argv)
    url = f"http://{args.host}:{args.port}/api/shutdown"
    req = urllib.request.Request(url, data=b"{}", method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = resp.read().decode("utf-8", "replace")
    except urllib.error.URLError as e:
        print(f"cannot reach the mapui server on {args.host}:{args.port} "
              f"({e}).", file=sys.stderr)
        return 1
    print(body.strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
