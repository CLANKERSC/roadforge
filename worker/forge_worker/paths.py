"""Where the worker keeps its data (job database, cache) on each OS."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from forge_worker.brand import brand


def data_dir() -> Path:
    override = os.environ.get("FORGE_DATA_DIR")
    if override:
        base = Path(override)
    elif sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / brand()["name"]
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / brand()["name"]
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / brand()["id"]
    base.mkdir(parents=True, exist_ok=True)
    return base
