"""Start the worker: python -m forge_worker [--port N] [--token T].

On start it prints ONE line of JSON to stdout, {"port": ..., "token": ...}, which the desktop
app reads to know where to connect. Port 0 (the default) means "pick a free random port".
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import socket
import sys
import threading

import uvicorn

from forge_worker.api import create_app
from forge_worker.paths import data_dir


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _exit_when_stdin_closes() -> None:
    try:
        while sys.stdin.read(4096):
            pass
    finally:
        os._exit(0)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="forge_worker")
    parser.add_argument("--port", type=int, default=int(os.environ.get("FORGE_PORT", "0")))
    parser.add_argument("--token", default=os.environ.get("FORGE_TOKEN") or secrets.token_urlsafe(32))
    parser.add_argument(
        "--exit-on-stdin-close",
        action="store_true",
        help="exit when stdin closes (the desktop app passes this so the worker dies with it)",
    )
    args = parser.parse_args(argv)
    if args.exit_on_stdin_close:
        threading.Thread(target=_exit_when_stdin_closes, daemon=True).start()

    port = args.port or _free_port()
    print(json.dumps({"port": port, "token": args.token}), flush=True)

    app = create_app(data_dir(), args.token)
    # 127.0.0.1 only: nothing on the network can reach the worker.
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main(sys.argv[1:])
