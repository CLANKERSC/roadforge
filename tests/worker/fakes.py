"""A fake internet for tests: canned answers for every service the worker talks to."""

from __future__ import annotations

import io
import json
import math
import re
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


def fake_height(lon: Any, lat: Any) -> Any:
    """The fake world's terrain: rolling hills (up to ~10 % slopes) around 300 m."""
    return 300.0 + 40.0 * np.sin(np.asarray(lon) * 200.0) + 30.0 * np.cos(np.asarray(lat) * 300.0)


def fake_dem_cuzk(bbox: tuple[float, float, float, float], px: int, noise: bool) -> bytes:
    """A georeferenced 2 m LiDAR-like tile in EPSG:5514 for the requested box."""
    west, south, east, north = bbox
    res = (east - west) / px
    xs = west + (np.arange(px) + 0.5) * res
    ys = north - (np.arange(px) + 0.5) * res
    gx, gy = np.meshgrid(xs, ys)
    lon, lat = Transformer.from_crs(5514, 4326, always_xy=True).transform(gx, gy)
    z = fake_height(lon, lat)
    if noise:  # LiDAR roughness + a few spikes and holes, like the real thing
        rng = np.random.default_rng(abs(int(west)) % 100_000)
        z = z + rng.normal(0.0, 0.08, z.shape)
        z[rng.integers(0, px, 12), rng.integers(0, px, 12)] += 6.0
        z[200:204, 300:306] = -9999.0
    buf = io.BytesIO()
    with rasterio.open(
        buf, "w", driver="GTiff", width=px, height=px, count=1, dtype="float32", crs="EPSG:5514",
        transform=from_origin(west, north, res, res), nodata=-9999.0,
    ) as ds:  # fmt: skip
        ds.write(z.astype(np.float32), 1)
    return buf.getvalue()


_DEM_TILES: dict[tuple[int, int], bytes] = {}


def fake_dem_copernicus(lat: int, lon: int) -> bytes:
    """A 1-degree tile like Copernicus GLO-30, at 3 arc-seconds to keep the tests quick."""
    if (lat, lon) not in _DEM_TILES:
        n = 1200
        lons = lon + (np.arange(n) + 0.5) / n
        lats = lat + 1 - (np.arange(n) + 0.5) / n
        z = fake_height(*np.meshgrid(lons, lats)).astype(np.float32)
        buf = io.BytesIO()
        with rasterio.open(
            buf, "w", driver="GTiff", width=n, height=n, count=1, dtype="float32", crs="EPSG:4326",
            transform=from_origin(lon, lat + 1, 1 / n, 1 / n), compress="deflate",
        ) as ds:  # fmt: skip
            ds.write(z, 1)
        _DEM_TILES[(lat, lon)] = buf.getvalue()
    return _DEM_TILES[(lat, lon)]


GRID_LON, GRID_LAT = 0.003, 0.002  # the fake town's street grid, about 215 x 220 m blocks


def fake_osm(south: float, west: float, north: float, east: float) -> dict[str, Any]:
    """A fake town covering the requested box: a wobbly street grid with the things that make
    real map data hard: bridges over other roads, crossings without a shared point, a hairpin,
    one-way streets, tracks, and plenty of things that are not roads at all."""
    i0, i1 = math.floor(west / GRID_LON) - 1, math.ceil(east / GRID_LON) + 1
    j0, j1 = math.floor(south / GRID_LAT) - 1, math.ceil(north / GRID_LAT) + 1
    nodes: dict[int, tuple[float, float]] = {}
    ways: list[dict[str, Any]] = []

    def node(i: float, j: float, kind: int = 0) -> int:
        nid = kind * 10**12 + int(round((i + 200_000) * 10)) * 10**7 + int(round((j + 200_000) * 10))
        wob = 0.12 * math.sin(i * 1.7 + j * 0.9) if kind == 0 else 0.0  # streets are not ruler-straight
        nodes[nid] = (round((i + wob) * GRID_LON, 7), round((j + wob * 0.8) * GRID_LAT, 7))
        return nid

    def way(wid: int, refs: list[int], **tags: str) -> None:
        ways.append({"type": "way", "id": wid, "nodes": refs, "tags": tags})

    for j in range(j0, j1 + 1):  # east-west streets, with a mid-block point so they curve
        cls = "secondary" if j % 3 == 0 else "residential"
        for i in range(i0, i1):
            wid = 1_000_000_000 + (i + 200_000) * 1_000_000 + (j + 200_000)
            refs = [node(i, j), node(i + 0.5, j + 0.06), node(i + 1, j)]
            if i % 5 == 2 and j % 4 == 1:
                way(wid, refs, highway=cls, bridge="yes", layer="1", name=f"Street {j}")
            elif j % 7 == 3:
                way(wid, refs, highway=cls, oneway="yes", name=f"Street {j}")
            else:
                way(wid, refs, highway=cls, name=f"Street {j}", **({"width": "7.5"} if j % 5 == 0 else {}))
    # north-south streets. Like the real service, a street that only touches the box still
    # comes back whole, so every piece (10 blocks long) has the same id and points in any request
    for i in range(i0, i1 + 1):
        cls = "tertiary" if i % 4 == 0 else "track" if i % 9 == 5 else "residential"
        for block in range(math.floor(j0 / 10), math.floor(j1 / 10) + 1):
            wid = 2_000_000_000 + (i + 200_000) * 100_000 + (block + 50_000)
            refs = [node(i, j) for j in range(block * 10, block * 10 + 11)]
            way(wid, refs, highway=cls, name=f"Avenue {i}")
    for j in range(j0, j1 + 1):
        for i in range(i0, i1):
            base = 3_000_000_000 + (i + 200_000) * 1_000_000 + (j + 200_000)
            if i % 5 == 2 and j % 4 == 1:
                # a lane passing under the bridge above, drawn without any shared point
                way(base, [node(i + 0.5, j - 0.4, 1), node(i + 0.5, j + 0.4, 1)], highway="service")
            if i % 4 == 1 and j % 4 == 2:
                # two streets crossing on the same level that the mapper forgot to connect
                way(base, [node(i + 0.3, j - 0.35, 2), node(i + 0.3, j + 0.35, 2)], highway="residential")
            if i % 6 == 3 and j % 5 == 2:
                # a hairpin far tighter than any car can turn, hanging off the street corner
                pts = [(i, j), (i + 0.25, j + 0.3), (i + 0.27, j + 0.33), (i + 0.25, j + 0.36), (i, j + 0.38)]
                way(base, [node(i, j), *[node(a, b, 3) for a, b in pts[1:]]], highway="unclassified")
            if i % 3 == 0 and j % 3 == 1:
                # not roads: a footpath, a house, a fence
                way(base + 1, [node(i + 0.1, j + 0.1, 4), node(i + 0.4, j + 0.3, 4)], highway="footway")
                ring = [node(i + 0.6, j + 0.6, 5), node(i + 0.7, j + 0.6, 5), node(i + 0.7, j + 0.7, 5)]
                way(base + 2, [*ring, ring[0]], building="yes")
    elements: list[dict[str, Any]] = [*ways]
    elements += [
        {"type": "node", "id": nid, "lon": lon, "lat": lat} for nid, (lon, lat) in sorted(nodes.items())
    ]
    return {"elements": elements}


class FakeHttp:
    """Stands in for forge_worker.http.Http. Set flags to simulate outages."""

    def __init__(self, scene_path: Path | None = None) -> None:
        self.calls: list[str] = []
        self.cuzk_has_data = True
        self.has_roads = True
        self.noisy_lidar = True
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
            if not self.has_roads:
                return FakeResponse(json.dumps({"elements": []}).encode())
            box = re.search(r"\(([-\d.]+),([-\d.]+),([-\d.]+),([-\d.]+)\)", kwargs["data"]["data"])
            assert box is not None
            return FakeResponse(json.dumps(fake_osm(*(float(v) for v in box.groups()))).encode())
        if "dmr5g" in url:
            if not self.cuzk_has_data:
                return FakeResponse(tiff_bytes(np.full((8, 8), -9999.0, dtype=np.float32)), "image/tiff")
            params = kwargs["params"]
            bbox = tuple(float(v) for v in params["bbox"].split(","))
            assert len(bbox) == 4
            px = int(params["size"].split(",")[0])
            return FakeResponse(fake_dem_cuzk(bbox, px, self.noisy_lidar), "image/tiff")
        if "ORTOFOTO" in url:
            return FakeResponse(b"\xff\xd8" + b"x" * (20_000 if self.cuzk_has_data else 500), "image/jpeg")
        if "copernicus-dem" in url:
            if "N00_00_E000" in url:
                raise HttpError(url, 404, "no tile")
            tile = re.search(r"_([NS])(\d\d)_00_([EW])(\d\d\d)_00_DEM\.tif", url)
            assert tile is not None
            lat = int(tile.group(2)) * (1 if tile.group(1) == "N" else -1)
            lon = int(tile.group(4)) * (1 if tile.group(3) == "E" else -1)
            return FakeResponse(fake_dem_copernicus(lat, lon), "image/tiff")
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
