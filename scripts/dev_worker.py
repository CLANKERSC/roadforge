"""Developer helper: run the worker on a fixed port for browser-only UI work.

Writes app/.env.local so `npm run dev` in app/ can find the worker. Not used by the
desktop app, which starts the worker itself on a random port.
"""

from __future__ import annotations

import secrets
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORT = 8765


def main() -> None:
    token = secrets.token_urlsafe(24)
    (ROOT / "app" / ".env.local").write_text(
        f"VITE_WORKER_URL=http://127.0.0.1:{PORT}\nVITE_WORKER_TOKEN={token}\n", encoding="utf-8"
    )
    print(f"Worker on http://127.0.0.1:{PORT} (token written to app/.env.local). Ctrl+C to stop.")
    cmd = [sys.executable, "-m", "forge_worker", "--port", str(PORT), "--token", token]
    raise SystemExit(subprocess.call(cmd, cwd=ROOT / "worker"))


if __name__ == "__main__":
    main()
