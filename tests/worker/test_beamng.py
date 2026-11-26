"""BeamNG export: the terrain file, the level folder and the mod ZIP."""

from __future__ import annotations

import io
import json
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient

from forge_worker.exporters.beamng.level import heading_matrix, shrink_for_beamng
from forge_worker.exporters.beamng.ter import TerFile, pack_heights, read_ter, unpack_heights, write_ter
from forge_worker.stages.export import beamng_mods_dir
from forge_worker.terrain import Dtm

from .conftest import AUTH
from .test_build import PRAGUE_AREA, build

AC_ONLY = {**PRAGUE_AREA, "games": ["ac"]}
NS = {"c": "http://www.collada.org/2005/11/COLLADASchema"}


def test_ter_file_round_trip(tmp_path: Path) -> None:
    rng = np.random.default_rng(3)
    ter = TerFile(
        size=64,
        heights=rng.integers(0, 65536, (64, 64)).astype(np.uint16),
        layers=rng.integers(0, 2, (64, 64)).astype(np.uint8),
        materials=["rf_grass", "rf_rock"],
    )
    write_ter(tmp_path / "t.ter", ter)
    raw = (tmp_path / "t.ter").read_bytes()
    assert raw[0] == 9 and int.from_bytes(raw[1:5], "little") == 64
    assert len(raw) == 5 + 64 * 64 * 3 + 4 + (1 + 8) + (1 + 7)
    back = read_ter(tmp_path / "t.ter")
    assert back.size == 64 and back.materials == ter.materials
    assert np.array_equal(back.heights, ter.heights) and np.array_equal(back.layers, ter.layers)
    with pytest.raises(ValueError, match="power-of-two"):
        write_ter(tmp_path / "bad.ter", TerFile(48, ter.heights[:48, :48], ter.layers[:48, :48], ["a"]))


def test_heights_are_packed_south_first_and_accurate() -> None:
    rows, cols = 300, 500
    ys = 1000.0 - (np.arange(rows) + 0.5) * 2.0  # row 0 = north
    xs = -400.0 + (np.arange(cols) + 0.5) * 2.0
    z = (250.0 + 0.1 * xs[None, :] + 0.2 * ys[:, None]).astype(np.float32)
    grid = pack_heights(z, west_x=-400.0, north_y=1000.0, res=2.0, material="rf_grass")
    assert grid.ter.size == 512 and grid.square_size == 2.0
    assert grid.position[:2] == (-399.0, 401.0)  # the south-west sample
    seen = unpack_heights(grid)
    assert abs(seen[:rows, :cols] - z[::-1]).max() < 0.01  # centimetre accuracy after 16-bit packing
    # sample (0, 0) is the south-west corner; x grows with the column, y with the row
    assert abs(seen[0, 0] - (250.0 + 0.1 * -399.0 + 0.2 * 401.0)) < 0.01
    assert abs(seen[10, 20] - (250.0 + 0.1 * (-399.0 + 40) + 0.2 * (401.0 + 20))) < 0.01
    assert np.allclose(seen[rows:, :cols], seen[rows - 1, :cols])  # padding repeats the edge: no cliff


def test_oversized_terrain_is_halved_and_stays_under_the_roads() -> None:
    z = np.random.default_rng(1).uniform(100, 110, (8200, 16)).astype(np.float32)
    small, res = shrink_for_beamng(Dtm(z=z, west=0.0, north=0.0, res=2.0, proj4=""))
    assert small.shape == (4100, 8) and res == 4.0
    assert small[0, 0] == z[:2, :2].min()


def test_spawn_heading_matrix() -> None:
    assert heading_matrix(0) == [1, 0, 0, 0, 1, 0, 0, 0, 1]
    east = heading_matrix(90)
    assert east[:2] == [0, -1] and east[3:5] == [1, 0]


def mod_zip(db_path: Path, job: dict[str, Any]) -> zipfile.ZipFile:
    work = db_path / "builds" / str(job["id"])
    export = json.loads((work / "export.json").read_text(encoding="utf-8"))
    return zipfile.ZipFile(work / export["games"]["beamng"]["zip"])


def read_lines(zf: zipfile.ZipFile, name: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in zf.read(name).decode("utf-8").splitlines()]


def test_build_writes_a_complete_beamng_mod(client: TestClient, db_path: Path) -> None:
    job = build(client, PRAGUE_AREA, "Praha Test")
    assert job["state"] == "done", job["message"]
    assert job["completed_stages"][-1] == "export" and job["message"].endswith("BeamNG mod ready")
    zf = mod_zip(db_path, job)
    names = set(zf.namelist())
    root = "levels/praha_test/"
    assert all(n.startswith(root) for n in names)  # the mod's only top folder is levels/<id>/
    for needed in ["info.json", "preview.png", "theTerrain.ter", "theTerrain.terrain.json", "CREDITS.txt",
                   "main/items.level.json", "main/MissionGroup/items.level.json",
                   "main/MissionGroup/Roads/items.level.json",
                   "main/MissionGroup/RoadMeshes/items.level.json",
                   "art/terrains/main.materials.json", "art/shapes/roads/main.materials.json",
                   "art/road/main.materials.json"]:  # fmt: skip
        assert root + needed in names, needed

    # scene objects: one JSON object per line, every object has a class, a name and a known parent
    assert read_lines(zf, root + "main/items.level.json") == [
        {
            "class": "SimGroup",
            "name": "MissionGroup",
            "persistentId": read_lines(zf, root + "main/items.level.json")[0]["persistentId"],
        }
    ]
    top = read_lines(zf, root + "main/MissionGroup/items.level.json")
    by_class = {o["class"]: o for o in top}
    assert {"LevelInfo", "TimeOfDay", "ScatterSky", "TerrainBlock", "SpawnSphere", "SimGroup"} <= set(
        by_class
    )
    assert all(o["__parent"] == "MissionGroup" for o in top)
    ids = [o["persistentId"] for o in top]
    assert len(set(ids)) == len(ids)

    # terrain: the file BeamNG reads matches the object that places it
    info = json.loads(zf.read(root + "info.json"))
    terrain = by_class["TerrainBlock"]
    assert terrain["terrainFile"] == "/levels/praha_test/theTerrain.ter"
    (db_path / "t.ter").write_bytes(zf.read(root + "theTerrain.ter"))
    ter = read_ter(db_path / "t.ter")
    assert ter.size == 1024 and terrain["squareSize"] == 2.0 and ter.materials == ["rf_grass"]
    assert info["size"] == [2048, 2048]
    meta = json.loads(zf.read(root + "theTerrain.terrain.json"))
    assert meta["size"] == 1024 and meta["heightMapSize"] == 1024 * 1024 and meta["materials"] == ["rf_grass"]
    terrain_mats = json.loads(zf.read(root + "art/terrains/main.materials.json"))
    grass = next(m for m in terrain_mats.values() if m["class"] == "TerrainMaterial")
    assert grass["internalName"] == "rf_grass" and grass["groundmodelName"] == "GRASS"
    assert terrain["materialTextureSet"] in terrain_mats
    for key, value in grass.items():  # every texture the material names is really in the ZIP
        if key.endswith("Tex"):
            assert value.lstrip("/") in names, value

    # spawn: named in info.json, sits on a road, just above the terrain
    spawn = by_class["SpawnSphere"]
    assert info["defaultSpawnPointName"] == spawn["name"] == info["spawnPoints"][0]["objectname"]
    x, y, z = spawn["position"]
    col = round((x - terrain["position"][0]) / 2.0)
    row = round((y - terrain["position"][1]) / 2.0)
    ground = ter.heights[row, col] * terrain["maxHeight"] / 65535.0 + terrain["position"][2]
    assert 0.3 < z - ground < 1.5

    # road meshes: valid COLLADA, Z-up, every material defined, files referenced exist
    statics = read_lines(zf, root + "main/MissionGroup/RoadMeshes/items.level.json")
    road_mats = json.loads(zf.read(root + "art/shapes/roads/main.materials.json"))
    assert (
        road_mats["rf_asphalt"]["groundType"] == "ASPHALT"
        and road_mats["rf_asphalt"]["mapTo"] == "rf_asphalt"
    )
    triangles = 0
    for static in statics:
        assert static["class"] == "TSStatic" and static["collisionType"] == "Visible Mesh Final"
        dae = ET.parse(io.BytesIO(zf.read(static["shapeName"].lstrip("/")))).getroot()
        assert dae.find("c:asset/c:up_axis", NS).text == "Z_UP"  # type: ignore[union-attr]
        for tri in dae.iterfind(".//c:triangles", NS):
            assert tri.get("material") in road_mats
            count = int(tri.get("count", "0"))
            assert len(tri.find("c:p", NS).text.split()) == count * 9  # type: ignore[union-attr]
            triangles += count
    assert triangles > 50_000

    # AI roads: one per stretch, ends shared at junctions so the navigation graph is connected
    roads = read_lines(zf, root + "main/MissionGroup/Roads/items.level.json")
    scene = json.loads((db_path / "builds" / job["id"] / "scene.json").read_text(encoding="utf-8"))
    assert len(roads) == len(scene["road_graph"]["edges"])
    ends: dict[tuple[float, float, float], int] = {}
    for road in roads:
        assert road["class"] == "DecalRoad" and road["__parent"] == "Roads" and len(road["nodes"]) >= 2
        assert all(len(n) == 4 and n[3] >= 2.5 for n in road["nodes"])
        assert road["lanesLeft"] + road["lanesRight"] >= 1 and road["overObjects"] is True
        for node in (road["nodes"][0], road["nodes"][-1]):
            ends[tuple(node[:3])] = ends.get(tuple(node[:3]), 0) + 1
    assert sum(1 for n in ends.values() if n >= 3) >= 10  # real junctions share one exact point
    assert road["material"] in json.loads(zf.read(root + "art/road/main.materials.json"))
    credits = zf.read(root + "CREDITS.txt").decode("utf-8")
    assert "OpenStreetMap contributors" in credits and "ČÚZK" in credits


def test_beamng_mod_is_identical_when_built_twice(client: TestClient, db_path: Path) -> None:
    first, second = build(client, PRAGUE_AREA, "Twice"), build(client, PRAGUE_AREA, "Twice")
    a = (db_path / "builds" / first["id"] / "export" / "twice.zip").read_bytes()
    b = (db_path / "builds" / second["id"] / "export" / "twice.zip").read_bytes()
    assert a == b


def test_install_copies_the_zip_into_the_mods_folder(
    client: TestClient, db_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = build(client, PRAGUE_AREA, "Install me")
    mods = tmp_path / "BeamNG" / "current" / "mods"
    monkeypatch.setenv("FORGE_BEAMNG_MODS", str(mods))
    resp = client.post(f"/jobs/{job['id']}/install/beamng", headers=AUTH)
    assert resp.status_code == 200, resp.text
    target = Path(resp.json()["installed_to"])
    assert target == mods / "install_me.zip" and zipfile.is_zipfile(target)
    monkeypatch.delenv("FORGE_BEAMNG_MODS")
    monkeypatch.setattr("sys.platform", "linux")
    missing = client.post(f"/jobs/{job['id']}/install/beamng", headers=AUTH)
    assert missing.status_code == 409 and "install_me.zip" in missing.json()["detail"]


def test_build_without_beamng_writes_no_mod(client: TestClient, db_path: Path) -> None:
    job = build(client, AC_ONLY, "Only AC")
    assert job["state"] == "done" and "BeamNG" not in job["message"]
    assert not list((db_path / "builds" / job["id"] / "export").glob("*.zip"))
    resp = client.post(f"/jobs/{job['id']}/install/beamng", headers=AUTH)
    assert resp.status_code == 409 and "Tick BeamNG" in resp.json()["detail"]


def test_mods_folder_is_found_on_windows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FORGE_BEAMNG_MODS", raising=False)
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert beamng_mods_dir() is None  # BeamNG never ran on this computer
    base = tmp_path / "BeamNG" / "BeamNG.drive"
    (base / "0.33").mkdir(parents=True)
    (base / "0.9").mkdir()
    assert beamng_mods_dir() == base / "0.33" / "mods"  # newest version folder, not alphabetical
    (base / "current").mkdir()
    assert beamng_mods_dir() == base / "current" / "mods"


def test_windows_data_moves_out_of_the_install_folder(tmp_path: Path) -> None:
    from forge_worker.paths import move_old_data

    old, new = tmp_path / "RoadForge", tmp_path / "RoadForgeData"
    (old / "builds" / "j1").mkdir(parents=True)
    (old / "builds" / "j1" / "scene.json").write_text("{}")
    (old / "jobs.sqlite3").write_text("db")
    (old / "roadforge.exe").write_text("program")
    new.mkdir()
    move_old_data(old, new)
    assert (new / "jobs.sqlite3").read_text() == "db"
    assert (new / "builds" / "j1" / "scene.json").is_file()
    assert sorted(p.name for p in old.iterdir()) == ["roadforge.exe"]  # the program stays put
    (old / "jobs.sqlite3").write_text("stale")
    move_old_data(old, new)  # a second run never overwrites the new data
    assert (new / "jobs.sqlite3").read_text() == "db"


def _png_kind(data: bytes) -> tuple[int, int, int, int]:
    """(width, height, bit depth, colour type) from a PNG header. Colour type 0 = grey, 2 = RGB."""
    import struct

    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width, height, depth, kind = struct.unpack(">IIBB", data[16:26])
    return width, height, depth, kind


def test_textures_use_the_formats_of_the_games_own_levels(client: TestClient, db_path: Path) -> None:
    """RGB roughness/ao/height maps showed up as rainbow noise on the ground in the real game."""
    job = build(client, PRAGUE_AREA, name="Praha test")
    assert job["state"] == "done", job["message"]
    export = json.loads((db_path / "builds" / job["id"] / "export.json").read_text(encoding="utf-8"))
    with zipfile.ZipFile(db_path / "builds" / job["id"] / export["games"]["beamng"]["zip"]) as z:
        names = z.namelist()
        terrain = [n for n in names if "/art/terrains/" in n and n.endswith(".png")]
        assert len(terrain) == 15
        for n in terrain:
            w, h, depth, kind = _png_kind(z.read(n))
            stem = n.rsplit("/", 1)[1][:-4]
            assert depth == 8 and w == h
            assert w == (2048 if stem.startswith("rf_base") else 1024), n
            assert kind == (2 if stem.endswith(("_b", "_nm")) else 0), n  # colour and normal RGB, data grey
        roads = [n for n in names if "/art/shapes/roads/" in n and n.endswith(".png")]
        assert len(roads) == 8 * 4
        for n in roads:
            _, _, depth, kind = _png_kind(z.read(n))
            assert depth == 8 and kind == (0 if n.endswith(".data.png") else 2), n
        materials = json.loads(z.read("levels/praha_test/art/shapes/roads/main.materials.json"))
        stage = materials["rf_asphalt"]["Stages"][0]
        for key in ("baseColorMap", "normalMap", "roughnessMap", "ambientOcclusionMap"):
            assert stage[key].lstrip("/") in names, key  # every texture a material names is in the mod
        ground = json.loads(z.read("levels/praha_test/art/terrains/main.materials.json"))
        grass = next(v for v in ground.values() if v["class"] == "TerrainMaterial")
        for key, value in grass.items():
            if key.endswith("Tex"):
                assert value.lstrip("/") in names, key
        # the aerial photo is on the ground: the base colour is not one flat green
        from rasterio.io import MemoryFile

        with MemoryFile(z.read("levels/praha_test/art/terrains/rf_base_b.png")) as mem, mem.open() as ds:
            photo = ds.read()
        assert photo.shape == (3, 2048, 2048)
        assert float(photo.std(axis=(1, 2)).mean()) > 12
    logs = client.get(f"/jobs/{job['id']}/logs", headers=AUTH).json()
    assert any("Ground photo" in entry["message"] for entry in logs)


def test_seamless_textures() -> None:
    from forge_worker.exporters import textures

    for ts in (textures.asphalt(256), textures.grass(256), textures.gravel(256), textures.cobble(256)):
        colour = ts.color.astype(np.float64)
        inner = np.abs(np.diff(colour, axis=2)).mean()
        seam = np.abs(colour[:, :, 0] - colour[:, :, -1]).mean()
        assert seam < 3 * inner + 2  # the left edge continues the right edge: no visible tile border
        assert ts.roughness.shape == (1, 256, 256) and ts.normal.shape == (3, 256, 256)
    asphalt = textures.asphalt(256).color.mean(axis=(1, 2))
    assert 45 < asphalt[0] < 95 and abs(asphalt[0] - asphalt[2]) < 8  # dark neutral grey


def test_motorway_gets_shoulders_and_lane_lines() -> None:
    from forge_worker.roadnet import road_width

    width, lanes, measured = road_width({"highway": "motorway", "lanes": "2"}, "motorway")
    assert (width, lanes, measured) == (10.0, 2, False)
    assert road_width({"highway": "motorway", "width": "8"}, "motorway")[0] == 8.0


def test_buildings_stand_on_the_ground_and_off_the_road() -> None:
    from shapely.geometry import LineString, box

    from forge_worker.buildings import building_height, building_meshes, load_buildings
    from forge_worker.geo import Georef

    georef = Georef(50.0, 14.0)
    nodes: dict[int, tuple[float, float]] = {}

    def ring(start: int, x: float, y: float, w: float, d: float) -> list[int]:
        ids = list(range(start, start + 4))
        for i, (px, py) in zip(ids, [(x, y), (x + w, y), (x + w, y + d), (x, y + d)], strict=True):
            nodes[i] = georef.to_lonlat(px, py)
        return [*ids, ids[0]]

    ways = [
        {"id": 1, "nodes": ring(10, 20, 20, 12, 8), "tags": {"building": "house", "building:levels": "2"}},
        {"id": 2, "nodes": ring(20, -4, 60, 8, 8), "tags": {"building": "yes"}},  # drawn across the road
        {"id": 3, "nodes": ring(30, 60, 20, 30, 40), "tags": {"building": "industrial", "height": "9"}},
        {"id": 4, "nodes": ring(40, 500, 500, 10, 10), "tags": {"building": "yes"}},  # outside the area
    ]
    road = LineString([(0, 0), (0, 100)]).buffer(4)
    found, stats = load_buildings(nodes, ways, georef, box(-100, -100, 200, 200), [road])
    assert [b.osm_id for b in found] == [1, 3] and stats["buildings_on_road_left_out"] == 1
    assert found[0].height == pytest.approx(6.4) and found[0].pitched
    assert found[1].height == 9.0 and not found[1].pitched
    assert building_height({"building": "garage"}, 30.0) == 2.8

    z = np.fromfunction(lambda r, c: 300 + 0.1 * c, (150, 150)).astype(np.float32)  # ground rises to the east
    dtm = Dtm(z=z, west=-100.0, north=200.0, res=2.0, proj4=georef.proj4)
    soups = building_meshes(found, dtm)
    assert {"roof_tile", "roof_flat"} <= set(soups) and any(k.startswith("wall_") for k in soups)
    for soup in soups.values():
        v, uv, f = soup.arrays()
        assert len(v) == len(uv) and f.max() < len(v) and np.isfinite(v).all()
    house = np.concatenate([s.arrays()[0] for k, s in soups.items() if k.startswith("wall_")])
    near = house[(house[:, 0] < 40)]
    ground_low = float(dtm.sample([20.0], [20.0])[0])
    assert near[:, 2].min() < ground_low  # the walls reach into the ground: no gap under the house
    roof = soups["roof_tile"].arrays()[0]
    assert (
        roof[:, 2].max() > near[:, 2].max() - 1e-6 and roof[:, 2].max() - ground_low > 7.0
    )  # a pitched roof on top


def test_trees_fill_forests_but_never_stand_on_a_road(tmp_path: Path) -> None:
    from shapely.geometry import LineString, Point, box

    from forge_worker.geo import Georef
    from forge_worker.vegetation import load_areas, place_plants

    georef = Georef(50.0, 14.0)
    elements: list[dict[str, Any]] = []

    def node(i: int, x: float, y: float, **tags: str) -> int:
        lon, lat = georef.to_lonlat(x, y)
        elements.append({"type": "node", "id": i, "lon": lon, "lat": lat, **({"tags": tags} if tags else {})})
        return i

    wood = [node(1, 0, 0), node(2, 200, 0), node(3, 200, 200), node(4, 0, 200)]
    elements.append({"type": "way", "id": 10, "nodes": [*wood, wood[0]], "tags": {"natural": "wood"}})
    # a forest drawn as a relation: its outline way has no tags of its own
    ring = [node(5, 300, 0), node(6, 400, 0), node(7, 400, 100), node(8, 300, 100)]
    elements.append({"type": "way", "id": 11, "nodes": [*ring, ring[0]]})
    elements.append(
        {
            "type": "relation",
            "id": 20,
            "tags": {"type": "multipolygon", "landuse": "forest"},
            "members": [{"type": "way", "ref": 11, "role": "outer"}],
        }  # fmt: skip
    )
    node(30, -50, 50, natural="tree")
    node(31, 100, 100, natural="tree")  # stands in the middle of the road below
    row = [node(40, -100, -50), node(41, -10, -50)]
    elements.append({"type": "way", "id": 12, "nodes": row, "tags": {"natural": "tree_row"}})
    path = tmp_path / "cell.json"
    path.write_text(json.dumps({"elements": elements}), encoding="utf-8")

    areas, points, rows = load_areas([path], georef)
    assert len(areas["forest"]) == 2 and len(points) == 2 and len(rows) == 1
    road = LineString([(100, -300), (100, 300)]).buffer(6)
    dtm = Dtm(
        z=np.full((400, 400), 250, dtype=np.float32), west=-300.0, north=400.0, res=2.0, proj4=georef.proj4
    )
    plants, stats = place_plants(areas, points, rows, box(-250, -250, 450, 350), [road], dtm, seed=7)
    assert stats["forest"] > 700 and stats["single"] == 1 and stats["row"] >= 10
    assert stats["left_out_near_roads_or_buildings"] == 1
    assert not any(road.contains(Point(p.x, p.y)) for p in plants)  # the road stays clear
    assert any(300 < p.x < 400 for p in plants)  # the relation forest was found too
    assert all(abs(p.z - 249.9) < 1e-6 and 0.8 <= p.scale <= 1.25 for p in plants)
    again, _ = place_plants(areas, points, rows, box(-250, -250, 450, 350), [road], dtm, seed=7)
    assert again == plants  # same map, same trees
