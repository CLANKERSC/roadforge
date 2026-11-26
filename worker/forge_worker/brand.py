"""App name and id, read from brand.json at the repo root (the one place to rename the app)."""

from __future__ import annotations

import json
import sys
from functools import lru_cache
from pathlib import Path
from typing import TypedDict


class Brand(TypedDict):
    name: str
    id: str


_FALLBACK: Brand = {"name": "RoadForge", "id": "roadforge"}


def _candidates() -> list[Path]:
    here = Path(__file__).resolve()
    paths = [here.parents[2] / "brand.json"]
    bundle_dir = getattr(sys, "_MEIPASS", None)  # set when frozen by PyInstaller
    if bundle_dir:
        paths.insert(0, Path(bundle_dir) / "brand.json")
    return paths


@lru_cache(maxsize=1)
def brand() -> Brand:
    for path in _candidates():
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            return {"name": str(data["name"]), "id": str(data["id"])}
    return _FALLBACK


def user_agent() -> str:
    """User-Agent for outbound downloads: app name and version only, never personal data."""
    from forge_worker import __version__

    return f"{brand()['name']}/{__version__}"
