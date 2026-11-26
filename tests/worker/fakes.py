"""A fake internet for tests: canned answers for every service the worker talks to."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.transform import from_origin

from forge_worker.http import HttpError


class FakeResponse:
    def __init__(self, content: bytes, content_type: str = "application/json") -> None:
        self.content = content
        self.headers = {"Content-Type": content_type}
        self.text = content[:200].decode("utf-8", "replace")

    def json(self) -> Any:
        return json.loads(self.content)

    def iter_content(self, _size: int) -> list[bytes]:
        return [self.content]

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_: object) -> None:
        return None


def tiff_bytes(data: np.ndarray, crs: str = "EPSG:5514") -> bytes:
    buf = io.BytesIO()
    with rasterio.open(
        buf, "w", driver="GTiff", width=data.shape[1], height=data.shape[0], count=1,
        dtype=data.dtype, crs=crs, transform=from_origin(0, 0, 2, 2),
    ) as ds:  # fmt: skip
        ds.write(data, 1)
    return buf.getvalue()


def make_scene(path: Path, lon: float = 14.44, lat: float = 50.08) -> None:
    """A small fake Sentinel-2 'visual' scene (30 x 30 km, 10 m, UTM) centred on lon/lat."""
    zone = int((lon + 180) // 6) + 1
    epsg = (32600 if lat >= 0 else 32700) + zone
    x, y = Transformer.from_crs(4326, epsg, always_xy=True).transform(lon, lat)
    data = np.full((3, 3000, 3000), 90, dtype=np.uint8)
    with rasterio.open(
        path, "w", driver="GTiff", width=3000, height=3000, count=3, dtype="uint8",
        crs=f"EPSG:{epsg}", transform=from_origin(round(x, -1) - 15000, round(y, -1) + 15000, 10, 10),
        compress="deflate",
    ) as ds:  # fmt: skip
        ds.write(data)


class FakeHttp:
    """Stands in for forge_worker.http.Http. Set flags to simulate outages."""

    def __init__(self, scene_path: Path | None = None) -> None:
        self.calls: list[str] = []
        self.cuzk_has_data = True
        self.offline_hosts: set[str] = set()
        self.scene_path = scene_path

    def _check(self, url: str) -> None:
        self.calls.append(url)
        for host in self.offline_hosts:
            if host in url:
                raise HttpError(url, None, "offline")

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self._check(url)
        if "overpass" in url:
            el = [{"type": "way", "id": 1, "nodes": [1, 2], "tags": {"highway": "residential"}}]
            return FakeResponse(json.dumps({"elements": el}).encode())
        if "dmr5g" in url:
            value = 250.0 if self.cuzk_has_data else -9999.0
            return FakeResponse(tiff_bytes(np.full((8, 8), value, dtype=np.float32)), "image/tiff")
        if "ORTOFOTO" in url:
            return FakeResponse(b"\xff\xd8" + b"x" * (20_000 if self.cuzk_has_data else 500), "image/jpeg")
        if "copernicus-dem" in url:
            if "N00_00_E000" in url:
                raise HttpError(url, 404, "no tile")
            return FakeResponse(
                tiff_bytes(np.full((8, 8), 300.0, dtype=np.float32), "EPSG:4326"), "image/tiff"
            )
        if "earth-search" in url:
            ring = kwargs["json"]["intersects"]["coordinates"][0]
            assert self.scene_path is not None
            make_scene(self.scene_path, sum(p[0] for p in ring[:4]) / 4, sum(p[1] for p in ring[:4]) / 4)
            item = {
                "id": "S2_FAKE_33UVR",
                "properties": {
                    "grid:code": "MGRS-33UVR",
                    "eo:cloud_cover": 1.0,
                    "datetime": "2025-06-01T10:00:00Z",
                },
                "assets": {"visual": {"href": str(self.scene_path)}},
            }
            return FakeResponse(json.dumps({"features": [item]}).encode())
        if "nominatim" in url:
            hit = {"display_name": "Brno, Czechia", "lat": "49.19", "lon": "16.61",
                   "boundingbox": ["49.1", "49.3", "16.4", "16.8"]}  # fmt: skip
            return FakeResponse(json.dumps([hit]).encode())
        if "osrm" in url:
            coords = [[14.40, 50.08], [14.41, 50.085], [14.42, 50.09]]
            body = {"code": "Ok", "routes": [{"geometry": {"coordinates": coords}}]}
            return FakeResponse(json.dumps(body).encode())
        raise AssertionError(f"unexpected request in test: {url}")

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        return self.request("GET", url, **kwargs)

    def get_json(self, url: str, **kwargs: Any) -> Any:
        return self.get(url, **kwargs).json()

    def post_json(self, url: str, **kwargs: Any) -> Any:
        return self.request("POST", url, **kwargs).json()
