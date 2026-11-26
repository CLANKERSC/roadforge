"""Live checks against the real services. Skipped unless FORGE_LIVE=1 (CI runs them).

They download a few small files, so they stay well within every service's fair-use rules.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import rasterio
from fastapi.testclient import TestClient

from forge_worker.api import create_app
from forge_worker.cache import DownloadCache
from forge_worker.http import Http
from forge_worker.selection import SelectionRequest, build_selection
from forge_worker.services import RATE_LIMITS, route_along_roads, search_places
from forge_worker.sources.base import FetchContext
from forge_worker.sources.copernicus import CopernicusDem30

from ..worker.conftest import AUTH, TOKEN, wait_for
from ..worker.test_geo_selection import square

pytestmark = pytest.mark.skipif(os.environ.get("FORGE_LIVE") != "1", reason="set FORGE_LIVE=1 to run")


def run_build(
    tmp_path: Path, lon: float, lat: float, km: float
) -> tuple[dict[str, Any], dict[str, Any], Path]:
    data_dir = tmp_path / "data"
    selection = {"mode": "area", "polygon": square(lon, lat, km), "games": ["ac", "beamng"]}
    with TestClient(create_app(data_dir, TOKEN)) as client:
        body = {"kind": "build", "name": "Live test", "params": {"selection": selection, "name": "Live test"}}
        job = client.post("/jobs", json=body, headers=AUTH).json()
        job = wait_for(client, job["id"], {"done", "failed", "cancelled"}, timeout=600)
        logs = client.get(f"/jobs/{job['id']}/logs", headers=AUTH).json()
    print("\n".join(f"{entry['level']}: {entry['message']}" for entry in logs))
    assert job["state"] == "done", job["message"]
    manifest = json.loads(
        (data_dir / "builds" / job["id"] / "fetch_manifest.json").read_text(encoding="utf-8")
    )
    return job, manifest["layers"], data_dir / "cache"


def test_live_copernicus_dem_only(tmp_path: Path) -> None:
    sel = build_selection(SelectionRequest(mode="area", polygon=square(10.453, 46.529, 0.5)))  # Stelvio
    cache = DownloadCache(tmp_path / "cache")
    ctx = FetchContext(cache, Http(), lambda f, m: None, lambda m, level="info": None, lambda: None)
    files = CopernicusDem30().fetch(sel, ctx)
    assert len(files) == 1
    with rasterio.open(files[0].file.path) as ds:
        lon, lat = 10.453, 46.529
        row, col = ds.index(lon, lat)
        height = float(ds.read(1, window=((row, row + 1), (col, col + 1)))[0, 0])
    assert 2300 < height < 3000  # Stelvio pass is at 2757 m
    cache.close()


def test_live_czech_build_uses_cuzk(tmp_path: Path) -> None:
    _, layers, cache = run_build(tmp_path, 14.4009, 50.0903, 0.6)  # Prague, Hradčany
    assert layers["elevation"]["source"] == "cuzk_dmr5g", layers["elevation"]["warnings"]
    assert layers["imagery"]["source"] == "cuzk_ortofoto", layers["imagery"]["warnings"]
    with rasterio.open(cache / layers["elevation"]["files"][0]["path"]) as ds:
        dem = ds.read(1)
        assert ds.width == 1024 and abs(ds.transform.a - 2.0) < 1e-6
    valid = dem[dem > -1000]
    assert valid.size > 0.9 * dem.size and 150 < float(np.median(valid)) < 450  # Prague is 180-400 m
    with rasterio.open(cache / layers["imagery"]["files"][0]["path"]) as ds:
        rgb = ds.read()
    assert rgb.shape[1:] == (2048, 2048) and float(rgb.std()) > 5  # a real photo, not a blank tile
    roads = json.loads((cache / layers["vectors"]["files"][0]["path"]).read_text(encoding="utf-8"))
    assert sum(1 for e in roads["elements"] if e.get("tags", {}).get("highway")) > 50


def test_live_build_outside_czechia_uses_global_data(tmp_path: Path) -> None:
    _, layers, cache = run_build(tmp_path, 10.453, 46.529, 0.6)  # Stelvio pass, Italy
    assert layers["elevation"]["source"] == "copernicus_dem_glo30"
    assert layers["imagery"]["source"] == "sentinel2_l2a", layers["imagery"]["warnings"]
    with rasterio.open(cache / layers["imagery"]["files"][0]["path"]) as ds:
        assert ds.count == 3 and float(ds.read().std()) > 2
    assert layers["vectors"]["files"]


def test_live_search_and_route() -> None:
    http = Http(min_interval=RATE_LIMITS)
    places = search_places(http, "Brno")
    assert places and 49.0 < places[0].lat < 49.4
    route = route_along_roads(http, [(14.4009, 50.0903), (14.4208, 50.0875)])
    assert route.routed, route.warning
    assert 1.0 < route.km < 6.0 and len(route.coords) > 10
