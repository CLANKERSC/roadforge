"""Quick check that the geo libraries work inside the packaged worker (run by CI on Windows)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import certifi
import numpy as np
import rasterio
from rasterio.transform import from_origin

from forge_worker.geo import transformer
from forge_worker.planning import make_plan
from forge_worker.selection import SelectionRequest, build_selection


def run_self_test() -> None:
    # projections incl. the Czech national grid (needs pyproj's bundled database)
    x, y = transformer(4326, 5514).transform(14.42, 50.09)
    assert -760_000 < x < -730_000 and -1_060_000 < y < -1_030_000, (x, y)
    # GeoTIFF and JPEG read/write (needs rasterio's bundled GDAL)
    with tempfile.TemporaryDirectory() as tmp:
        tif = Path(tmp) / "t.tif"
        data = np.arange(64, dtype=np.float32).reshape(8, 8)
        profile = {"driver": "GTiff", "width": 8, "height": 8, "count": 1, "dtype": "float32"}
        with rasterio.open(tif, "w", crs="EPSG:5514", transform=from_origin(0, 0, 2, 2), **profile) as ds:
            ds.write(data, 1)
        with rasterio.open(tif) as ds:
            assert np.array_equal(ds.read(1), data) and ds.crs.to_epsg() == 5514
        jpg = Path(tmp) / "t.jpg"
        rgb = np.full((3, 16, 16), 128, dtype=np.uint8)
        with rasterio.open(jpg, "w", driver="JPEG", width=16, height=16, count=3, dtype="uint8") as ds:
            ds.write(rgb)
        with rasterio.open(jpg) as ds:
            assert ds.read().shape == (3, 16, 16)
    assert Path(certifi.where()).is_file()
    # selection maths + source choice
    square = [(14.43, 50.07), (14.45, 50.07), (14.45, 50.09), (14.43, 50.09)]
    plan = make_plan(build_selection(SelectionRequest(mode="area", polygon=square)), "Self test")
    assert plan["ok"] and plan["layers"]["elevation"]["chosen"]["id"] == "cuzk_dmr5g", plan
    print("self-test OK")
