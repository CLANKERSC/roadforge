"""Where the worker keeps its data (job database, cache, builds) on each OS."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from forge_worker.brand import brand

# What an older version kept in the Windows install folder, to be moved out once.
_OLD_ITEMS = ("jobs.sqlite3", "jobs.sqlite3-wal", "jobs.sqlite3-shm", "cache", "builds")


def data_dir() -> Path:
    override = os.environ.get("FORGE_DATA_DIR")
    if override:
        base = Path(override)
    elif sys.platform == "win32":
        local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        # Not "<name>" alone: the installer puts the program in that folder, and data must not mix with it.
        base = local / f"{brand()['name']}Data"
        base.mkdir(parents=True, exist_ok=True)
        move_old_data(local / brand()["name"], base)
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / brand()["name"]
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / brand()["id"]
    base.mkdir(parents=True, exist_ok=True)
    return base


def move_old_data(old: Path, new: Path) -> None:
    """Version 0.2 kept its data next to the program. Move it once, so earlier builds are not lost."""
    if (new / "jobs.sqlite3").exists() or not (old / "jobs.sqlite3").exists():
        return
    for name in _OLD_ITEMS:
        src = old / name
        if src.exists() and not (new / name).exists():
            try:
                shutil.move(str(src), str(new / name))
            except OSError:
                pass  # a locked file stays behind; the app still works with a fresh database
