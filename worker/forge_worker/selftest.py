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
    _world_self_test()
    print("self-test OK")


def _world_self_test() -> None:
    """A tiny crossroads on a hillside, taken all the way to a 3D file (needs scipy and trimesh)."""
    from shapely.geometry import box

    from forge_worker.conform import conform_terrain
    from forge_worker.geo import Georef
    from forge_worker.roadgeom import build_profiles
    from forge_worker.roadmesh import build_road_mesh
    from forge_worker.roadnet import build_graph
    from forge_worker.scene import mesh_summary, preview_terrain, road_meshes, write_glb
    from forge_worker.terrain import Dtm
    from forge_worker.validate import check_drivability

    geo = Georef(lat0=50.0, lon0=14.0)
    points = {1: (-150.0, 0.0), 2: (0.0, 0.0), 3: (150.0, 0.0), 4: (0.0, 150.0), 5: (0.0, -150.0)}
    nodes = {k: geo.to_lonlat(*xy) for k, xy in points.items()}
    ways = [
        {"type": "way", "id": 1, "nodes": [1, 2, 3], "tags": {"highway": "secondary"}},
        {"type": "way", "id": 2, "nodes": [5, 2, 4], "tags": {"highway": "residential"}},
    ]
    area = box(-190, -190, 190, 190)
    graph = build_graph(nodes, ways, geo, area)
    xs = -200 + (np.arange(200) + 0.5) * 2.0
    z = np.tile(100 + 0.08 * xs, (200, 1)).astype(np.float32)
    dtm = Dtm(z=z, west=-200.0, north=200.0, res=2.0, proj4=geo.proj4)
    profiles, node_z, repairs = build_profiles(graph, dtm, 2.0)
    mesh = build_road_mesh(profiles, graph.nodes, node_z)
    shaped, _ = conform_terrain(dtm, mesh, set())
    meshes = [*road_meshes(mesh, shaped), preview_terrain(shaped, area)]
    report = check_drivability(profiles, node_z, mesh, shaped, [mesh_summary(m) for m in meshes], repairs)
    assert report["ok"], report["checks"]
    with tempfile.TemporaryDirectory() as tmp:
        glb = Path(tmp) / "t.glb"
        write_glb(meshes, glb)
        assert glb.read_bytes()[:4] == b"glTF" and glb.stat().st_size > 10_000
