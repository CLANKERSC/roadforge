"""Milestone 2: terrain, roads, 3D scene and the drivability check."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient
from shapely.geometry import box

from forge_worker.conform import conform_terrain
from forge_worker.geo import Georef
from forge_worker.roadgeom import (
    MAX_KINK,
    build_profiles,
    limit_grade,
    relax_kinks,
    relax_tight_corners,
    roads_from_json,
    roads_to_json,
    turn_radius,
)
from forge_worker.roadmesh import build_road_mesh, open_seams
from forge_worker.roadnet import build_graph, road_class, road_width, surface_of
from forge_worker.stages.world import BuildError, pick_resolution
from forge_worker.terrain import Dtm, fill_gaps, remove_spikes
from forge_worker.validate import check_drivability, clearance, upside_down_faces

from .conftest import AUTH
from .fakes import FakeHttp
from .test_build import NZ_AREA, PRAGUE_AREA, build

GEO = Georef(lat0=50.0, lon0=14.0)


def osm(points: dict[int, tuple[float, float]], ways: list[tuple[int, list[int], dict[str, str]]]) -> Any:
    """Hand-made map data: points in local metres, converted to lon/lat like real OSM."""
    nodes = {nid: GEO.to_lonlat(x, y) for nid, (x, y) in points.items()}
    return nodes, [{"type": "way", "id": wid, "nodes": refs, "tags": tags} for wid, refs, tags in ways]


def flat_dtm(height: Any = 100.0, size: int = 400) -> Dtm:
    z = np.full((size, size), 0.0, dtype=np.float32)
    if callable(height):
        xs = -size + (np.arange(size) + 0.5) * 2.0
        ys = size - (np.arange(size) + 0.5) * 2.0
        z = height(*np.meshgrid(xs, ys)).astype(np.float32)
    else:
        z += height
    return Dtm(z=z, west=-float(size), north=float(size), res=2.0, proj4=GEO.proj4)


CLIP = box(-390, -390, 390, 390)


# -- reading the map -----------------------------------------------------------
def test_road_types_widths_and_surfaces() -> None:
    assert road_class({"highway": "primary_link"}) == "primary"
    assert road_class({"highway": "footway"}) is None
    assert road_class({"highway": "service", "service": "parking_aisle"}) is None
    assert road_class({"highway": "residential", "area": "yes"}) is None
    assert road_width({"width": "7.5"}, "residential") == (7.5, 2, True)
    assert road_width({"width": "1 m"}, "residential")[2] is False  # nonsense width is ignored
    assert road_width({"lanes": "4"}, "primary")[:2] == (13.0, 4)
    assert road_width({"oneway": "yes"}, "residential")[:2] == (3.0, 1)
    assert surface_of({"surface": "sett"}, "residential") == "cobble"
    assert surface_of({}, "track") == "gravel"
    assert surface_of({}, "primary") == "asphalt"


def test_graph_splits_at_junctions_and_merges_plain_points() -> None:
    nodes, ways = osm(
        {1: (-200, 0), 2: (0, 0), 3: (200, 0), 4: (0, 200), 5: (100, 0)},
        [
            (10, [1, 2], {"highway": "residential"}),
            (11, [2, 5, 3], {"highway": "residential"}),  # same street, drawn as a second piece
            (12, [2, 4], {"highway": "tertiary"}),
            (13, [1, 4], {"highway": "footway"}),
        ],
    )
    graph = build_graph(nodes, ways, GEO, CLIP)
    assert len(graph.edges) == 3 and sorted(graph.degree()) == [1, 1, 1, 3]
    assert sorted(round(e.length) for e in graph.edges) == [200, 200, 200]


def test_unconnected_crossing_becomes_a_junction_but_a_bridge_does_not() -> None:
    points = {1: (-200, 0), 2: (200, 0), 3: (0, -200), 4: (0, 200)}
    nodes, ways = osm(
        points, [(10, [1, 2], {"highway": "residential"}), (11, [3, 4], {"highway": "residential"})]
    )
    graph = build_graph(nodes, ways, GEO, CLIP)
    assert graph.repairs["unconnected_crossings_joined"] == 1
    assert len(graph.edges) == 4 and max(graph.degree()) == 4

    nodes, ways = osm(
        points,
        [
            (10, [1, 2], {"highway": "residential", "bridge": "yes", "layer": "1"}),
            (11, [3, 4], {"highway": "residential"}),
        ],
    )
    graph = build_graph(nodes, ways, GEO, CLIP)
    assert "unconnected_crossings_joined" not in graph.repairs and len(graph.edges) == 2


def test_roads_are_clipped_to_the_selection() -> None:
    nodes, ways = osm({1: (-1000, 0), 2: (1000, 0)}, [(10, [1, 2], {"highway": "primary"})])
    graph = build_graph(nodes, ways, GEO, CLIP)
    assert len(graph.edges) == 1 and round(graph.edges[0].length) == 780


# -- terrain -------------------------------------------------------------------
def test_terrain_holes_and_spikes_are_repaired() -> None:
    z = np.full((50, 50), 200.0, dtype=np.float32)
    z[10:14, 10:14] = np.nan
    z[30, 30] = 260.0
    filled, count = fill_gaps(z)
    assert count == 16 and np.isfinite(filled).all() and abs(float(filled[12, 12]) - 200) < 0.01
    clean, spikes = remove_spikes(filled, 1.5)
    assert spikes == 1 and float(clean.max()) < 201
    with pytest.raises(ValueError, match="empty"):
        fill_gaps(np.full((4, 4), np.nan, dtype=np.float32))


def test_terrain_resolution_grows_with_the_selection() -> None:
    assert pick_resolution((0, 0, 5_000, 5_000)) == 2.0
    assert pick_resolution((0, 0, 16_400, 16_400)) == 3.0
    assert pick_resolution((0, 0, 40_000, 40_000)) == 8.0
    with pytest.raises(BuildError, match="split it"):
        pick_resolution((0, 0, 71_000, 71_000))


def test_dtm_surface_sampling_matches_the_grid() -> None:
    dtm = flat_dtm(lambda x, y: 100 + 0.1 * x + 0.05 * y, size=50)
    x, y = np.array([3.3, -20.7]), np.array([10.1, 4.4])
    expected = 100 + 0.1 * x + 0.05 * y
    assert np.allclose(dtm.sample(x, y), expected, atol=1e-3)
    assert np.allclose(dtm.sample_surface(x, y), expected, atol=1e-3)


# -- making roads drivable -----------------------------------------------------
def test_hairpin_tighter_than_the_road_is_opened_up() -> None:
    xy = np.array([[0, 0], [20, 0], [40, 0], [41, 1], [40, 2], [20, 2], [0, 2]], dtype=float)
    assert turn_radius(xy).min() < 2
    fixed, moved = relax_tight_corners(xy, min_radius=5.0)
    assert moved > 0 and turn_radius(fixed).min() > 2 * turn_radius(xy).min()
    assert np.allclose(fixed[0], xy[0]) and np.allclose(fixed[-1], xy[-1])


def test_cliff_in_the_height_data_becomes_a_drivable_ramp() -> None:
    s = np.arange(0, 200, 2.0)
    z = np.where(s < 100, 100.0, 110.0)  # a 10 m wall across the road
    limited, changed = limit_grade(z, s, 0.10)
    assert changed > 0 and np.abs(np.diff(limited) / 2.0).max() <= 0.10 + 1e-9
    eased, _ = relax_kinks(limited, s)
    assert np.abs(np.diff(np.diff(eased) / 2.0)).max() <= MAX_KINK
    assert eased[0] == z[0] and eased[-1] == z[-1]


def junction_world(height: Any = 100.0, tags: dict[str, str] | None = None) -> Any:
    """A crossroads with a side street, on the given terrain."""
    nodes, ways = osm(
        {1: (-300, 0), 2: (0, 0), 3: (300, 0), 4: (0, 300), 5: (0, -300), 6: (150, 0), 7: (150, 200)},
        [
            (10, [1, 2, 6, 3], {"highway": "secondary", **(tags or {})}),
            (11, [5, 2, 4], {"highway": "residential"}),
            (12, [6, 7], {"highway": "service"}),
        ],
    )
    graph = build_graph(nodes, ways, GEO, CLIP)
    dtm = flat_dtm(height)
    profiles, node_z, repairs = build_profiles(graph, dtm, 2.0)
    mesh = build_road_mesh(profiles, graph.nodes, node_z)
    return graph, dtm, profiles, node_z, mesh, repairs


def test_road_mesh_is_welded_and_faces_up() -> None:
    graph, _, profiles, _, mesh, _ = junction_world()
    assert mesh.stats["junctions"] == 2
    assert open_seams(mesh) == 0 and upside_down_faces(mesh) == 0
    assert {"asphalt", "shoulder", "apron"} <= set(mesh.face_surface)
    # the asphalt is as wide as the map says: secondary = 2 lanes of 3.25 m
    main = next(p for p in profiles if p.edge.cls == "secondary")
    faces = mesh.faces[(mesh.face_edge == main.edge.id) & (np.asarray(mesh.face_surface) == "asphalt")]
    ys = mesh.vertices[np.unique(faces), 1]
    assert abs((ys.max() - ys.min()) - 6.5) < 0.05


def test_terrain_is_pressed_under_the_road_on_a_steep_hillside() -> None:
    # 25 % cross slope plus LiDAR-like noise: the ground would stick through the asphalt
    rng = np.random.default_rng(1)
    noise = rng.normal(0, 0.15, (400, 400))

    def hillside(x: Any, y: Any) -> Any:
        return 100 + 0.25 * y + 0.05 * x + noise

    _, dtm, profiles, node_z, mesh, repairs = junction_world(hillside)
    bridges = {p.edge.id for p in profiles if p.edge.bridge}
    assert clearance(dtm, mesh, bridges)[1] > 0  # before: terrain pokes through
    shaped, stats = conform_terrain(dtm, mesh, bridges)
    low, bad, _ = clearance(shaped, mesh, bridges)
    assert bad == 0 and low >= 0.02
    assert stats["max_cut_m"] > 0.3 and stats["max_fill_m"] > 0.3
    far = (slice(0, 20), slice(0, 20))  # far from any road the ground is untouched
    assert np.array_equal(shaped.z[far], dtm.z[far])
    summaries = [{"vertices": 100}]
    report = check_drivability(profiles, node_z, mesh, shaped, summaries, repairs)
    assert report["ok"], [c for c in report["checks"] if not c["ok"]]
    broken = check_drivability(profiles, node_z, mesh, dtm, summaries, repairs)
    assert not broken["ok"] and not next(c for c in broken["checks"] if c["id"] == "terrain_under_road")["ok"]


def test_steep_hill_is_limited_to_what_the_road_type_allows() -> None:
    _, _, profiles, node_z, _, repairs = junction_world(lambda x, y: 100 + 0.30 * x)  # 30 % straight up
    main = max(profiles, key=lambda p: p.length)
    grade = np.abs(np.diff(main.z) / np.diff(main.s)).max()
    assert grade <= 0.14 * 1.08 and repairs["grade_limited_points"] > 0
    for p in profiles:  # every road arrives at its junctions at exactly the junction's height
        assert abs(p.z[0] - node_z[p.edge.a]) < 1e-6 and abs(p.z[-1] - node_z[p.edge.b]) < 1e-6


def test_bridge_over_a_road_gets_headroom_on_flat_data() -> None:
    nodes, ways = osm(
        {1: (-300, 0), 2: (-80, 0), 3: (80, 0), 4: (300, 0), 5: (0, -300), 6: (0, 300)},
        [
            (10, [1, 2], {"highway": "secondary"}),
            (11, [2, 3], {"highway": "secondary", "bridge": "yes", "layer": "1"}),
            (12, [3, 4], {"highway": "secondary"}),
            (13, [5, 6], {"highway": "residential"}),
        ],
    )
    graph = build_graph(nodes, ways, GEO, CLIP)
    dtm = flat_dtm(100.0)  # coarse data: no embankment, both roads at the same height
    profiles, node_z, repairs = build_profiles(graph, dtm, 30.0)
    assert repairs["overpasses_given_headroom"] == 1 and not repairs.get("overpasses_with_low_headroom")
    deck = next(p for p in profiles if p.edge.bridge)
    under = next(p for p in profiles if p.edge.cls == "residential")
    gap = float(np.interp(deck.length / 2, deck.s, deck.z)) - float(
        np.interp(under.length / 2, under.s, under.z)
    )
    assert gap >= 4.7
    mesh = build_road_mesh(profiles, graph.nodes, node_z)
    assert mesh.stats["bridges"] == 1 and any(s.surface == "wall" for s in mesh.extras)
    shaped, _ = conform_terrain(dtm, mesh, {deck.edge.id})
    report = check_drivability(profiles, node_z, mesh, shaped, [{"vertices": 1}], repairs)
    assert report["ok"], [c for c in report["checks"] if not c["ok"]]


@pytest.mark.parametrize("gap", [12.0, 8.0, 5.0, 3.0])
def test_switchbacks_on_a_mountainside_stay_drivable(gap: float) -> None:
    """A pass road like the Stelvio: legs stacked a few metres apart on a 55 % slope, each hairpin
    mapped with a single point. Found by the live test: the ground between two legs stuck out
    over the lower one and the surface folded inside the hairpins."""
    points: dict[int, tuple[float, float]] = {}
    refs: list[int] = []
    for leg in range(6):
        y = -30 + leg * gap
        for x in [-150.0, 0.0, 150.0] if leg % 2 == 0 else [150.0, 0.0, -150.0]:
            points[len(points) + 1] = (x, y)
            refs.append(len(points))
        if leg < 5:
            points[len(points) + 1] = (155.0 if leg % 2 == 0 else -155.0, y + gap / 2)
            refs.append(len(points))
    nodes, ways = osm(points, [(1, refs, {"highway": "secondary"})])
    graph = build_graph(nodes, ways, GEO, CLIP)
    dtm = flat_dtm(lambda x, y: 2500 + 0.55 * y + 0.03 * x)
    profiles, node_z, repairs = build_profiles(graph, dtm, 30.0)
    mesh = build_road_mesh(profiles, graph.nodes, node_z)
    assert upside_down_faces(mesh) == 0
    shaped, _ = conform_terrain(dtm, mesh, set())
    low, bad, _ = clearance(shaped, mesh, set())
    assert bad == 0 and low >= 0.02
    report = check_drivability(profiles, node_z, mesh, shaped, [{"vertices": 1}], repairs)
    assert report["ok"], [c for c in report["checks"] if not c["ok"]]
    # the road is still a real switchback road, not flattened into a straight line
    assert profiles[0].length > 0.8 * graph.edges[0].length > 1400


def test_roads_survive_saving_and_loading() -> None:
    graph, _, profiles, node_z, mesh, _ = junction_world(lambda x, y: 100 + 0.05 * x)
    data = json.loads(json.dumps(roads_to_json(profiles, graph.nodes, node_z)))
    again, node_xy, z2 = roads_from_json(data)
    mesh2 = build_road_mesh(again, node_xy, z2)
    assert mesh2.faces.shape == mesh.faces.shape and open_seams(mesh2) == 0
    assert np.allclose(mesh.vertices.min(axis=0), mesh2.vertices.min(axis=0), atol=1e-3)
    assert np.allclose(mesh.vertices.max(axis=0), mesh2.vertices.max(axis=0), atol=1e-3)
    assert roads_to_json(again, node_xy, z2) == data  # stable: saving again changes nothing


# -- the whole build -----------------------------------------------------------
def files(db_path: Path, job: dict[str, Any]) -> Path:
    return db_path / "builds" / str(job["id"])


def test_build_makes_a_drivable_scene_from_lidar(client: TestClient, db_path: Path) -> None:
    job = build(client, PRAGUE_AREA)
    assert job["state"] == "done", job["message"]
    assert job["completed_stages"] == ["select", "fetch", "terrain", "roads", "scene", "check"]
    assert "all checks passed" in job["message"]
    work = files(db_path, job)
    report = json.loads((work / "drivability.json").read_text(encoding="utf-8"))
    assert report["ok"], [c for c in report["checks"] if not c["ok"]]
    assert {c["id"] for c in report["checks"]} >= {
        "seams",
        "folds",
        "terrain_under_road",
        "slope",
        "kinks",
        "mesh_size",
    }
    assert report["summary"]["road_km"] > 5 and report["summary"]["junctions"] > 10
    repaired = {r["id"] for r in report["repairs"]}
    assert {"unconnected_crossings_joined", "terrain_spikes_removed", "overpasses_given_headroom"} <= repaired
    scene = json.loads((work / "scene.json").read_text(encoding="utf-8"))
    assert scene["georef"]["origin_lat"] == 50.08 and scene["spawn"]["xyz"][2] > 200
    assert max(m["vertices"] for m in scene["meshes"]) < 65_535
    surfaces = {m["surface"] for m in scene["meshes"]}
    assert {"asphalt", "gravel", "shoulder", "marking", "wall", "terrain"} <= surfaces
    assert all(len(e["centreline"]) >= 2 for e in scene["road_graph"]["edges"])
    logs = [entry["message"] for entry in client.get(f"/jobs/{job['id']}/logs", headers=AUTH).json()]
    assert any(m.startswith("OK: Terrain never pokes through the road") for m in logs)
    assert not any(m.startswith("PROBLEM") for m in logs)


def test_build_is_drivable_on_coarse_world_data(client: TestClient, db_path: Path) -> None:
    job = build(client, NZ_AREA)
    assert job["state"] == "done", job["message"]
    report = json.loads((files(db_path, job) / "drivability.json").read_text(encoding="utf-8"))
    assert report["ok"], [c for c in report["checks"] if not c["ok"]]
    terrain = json.loads((files(db_path, job) / "terrain.json").read_text(encoding="utf-8"))
    assert terrain["source"] == "copernicus_dem_glo30" and terrain["res_m"] == 2.0


def test_preview_file_is_a_real_3d_model(client: TestClient, db_path: Path) -> None:
    import trimesh

    job = build(client, PRAGUE_AREA)
    resp = client.get(f"/jobs/{job['id']}/files/preview.glb", headers=AUTH)
    assert resp.status_code == 200 and resp.content[:4] == b"glTF"
    scene = trimesh.load(files(db_path, job) / "preview.glb")
    names = set(scene.geometry)
    assert "terrain_preview" in names and any(n.startswith("road_asphalt") for n in names)
    lo, hi = scene.bounds
    assert 200 < lo[1] < hi[1] < 400  # glTF is y-up: heights of the fake hills
    assert client.get(f"/jobs/{job['id']}/files/drivability.json", headers=AUTH).json()["ok"] is True
    assert client.get(f"/jobs/{job['id']}/files/georef.json", headers=AUTH).status_code == 404
    assert client.get(f"/jobs/{job['id']}/files/preview.glb").status_code == 401
    assert client.get("/jobs/nope/files/preview.glb", headers=AUTH).status_code == 404


def test_rebuild_gives_byte_identical_results(client: TestClient, db_path: Path) -> None:
    first = build(client, PRAGUE_AREA)
    second = build(client, PRAGUE_AREA)
    for name in ["scene.json", "roads.json", "drivability.json", "preview.glb"]:
        assert (files(db_path, first) / name).read_bytes() == (files(db_path, second) / name).read_bytes(), (
            name
        )


def test_selection_without_roads_fails_with_a_clear_message(client: TestClient, fake_http: FakeHttp) -> None:
    fake_http.has_roads = False
    job = build(client, PRAGUE_AREA)
    assert job["state"] == "failed" and "no drivable roads" in job["message"]
    assert job["completed_stages"] == ["select", "fetch", "terrain"]
