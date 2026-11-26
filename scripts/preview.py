"""One-command preview in your web browser (no Rust needed). Works on macOS, Windows, Linux.

  python3 scripts/preview.py

First run installs what's needed (a Python virtual env in worker/.venv and app/node_modules),
then starts the worker and the UI and opens http://localhost:1420. Ctrl+C stops both.
"""

from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import sys
import time
import urllib.request
import venv
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "worker"
APP = ROOT / "app"
PORT = 8765
WIN = sys.platform == "win32"


def venv_python() -> Path:
    return WORKER / ".venv" / ("Scripts/python.exe" if WIN else "bin/python")


def ensure_setup() -> None:
    if sys.version_info < (3, 11):  # noqa: UP036 - macOS ships an older python3
        sys.exit("Python 3.11 or newer is needed. Get it from https://www.python.org/downloads/")
    npm = shutil.which("npm")
    if not npm:
        sys.exit("Node.js is needed. Get the LTS version from https://nodejs.org/")
    if not venv_python().exists():
        print("First run: setting up Python packages (1-2 minutes)…")
        venv.create(WORKER / ".venv", with_pip=True)
        subprocess.run(
            [str(venv_python()), "-m", "pip", "install", "-q", "-e", ".[dev]"], cwd=WORKER, check=True
        )
    if not (APP / "node_modules").exists():
        print("First run: setting up the UI packages (1-2 minutes)…")
        subprocess.run([npm, "ci"], cwd=APP, check=True)


def main() -> None:
    ensure_setup()
    token = secrets.token_urlsafe(24)
    (APP / ".env.local").write_text(
        f"VITE_WORKER_URL=http://127.0.0.1:{PORT}\nVITE_WORKER_TOKEN={token}\n", encoding="utf-8"
    )
    worker = subprocess.Popen(
        [str(venv_python()), "-m", "forge_worker", "--port", str(PORT), "--token", token], cwd=WORKER
    )
    ui = subprocess.Popen([shutil.which("npm") or "npm", "run", "dev"], cwd=APP, env=os.environ.copy())
    try:
        for _ in range(60):
            try:
                urllib.request.urlopen("http://localhost:1420", timeout=1)
                break
            except OSError:
                time.sleep(0.5)
        print("\nOpen http://localhost:1420 if the browser didn't open. Ctrl+C to stop.\n")
        webbrowser.open("http://localhost:1420")
        ui.wait()
    except KeyboardInterrupt:
        pass
    finally:
        for p in (ui, worker):
            p.terminate()


if __name__ == "__main__":
    main()
