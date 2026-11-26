"""Write a BeamNG.drive level from the engine-neutral scene, and zip it as a mod.

What goes in (see docs/FORMAT_NOTES_BEAMNG.md for the reasoning and what is still unverified):
- theTerrain.ter: the conformed terrain, one grass material. It sits 10 cm under every road.
- art/shapes/roads/*.dae: our welded road meshes as static objects with collision. This is the
  surface the car drives on, exactly as checked by the drivability stage.
- DecalRoads: invisible, one per stretch of road, sharing end points at junctions. They give the
  AI and traffic their road network.
- a spawn point, sky, sun, level info, a preview picture.
"""

from __future__ import annotations

import json
import math
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from numpy.typing import NDArray

from forge_worker.brand import brand
from forge_worker.exporters.beamng.dae import write_dae
from forge_worker.exporters.beamng.ter import TerrainGrid, pack_heights, write_ter
from forge_worker.roadgeom import Profile
from forge_worker.scene import MeshOut
from forge_worker.terrain import Dtm

MAX_TERRAIN = 8192  # BeamNG's largest terrain grid
BLOCK_TILES = 4  # road meshes are grouped into files of 4 x 4 terrain tiles (about 1 km)
TERRAIN_MATERIAL = "rf_grass"
TEX = 512  # terrain texture size in pixels

# our surface -> (BeamNG ground type for physics, RGB 0-1, roughness)
SURFACES: dict[str, tuple[str, tuple[float, float, float], float]] = {
    "asphalt": ("ASPHALT", (0.20, 0.20, 0.21), 0.85),
    "concrete": ("CONCRETE", (0.55, 0.55, 0.53), 0.9),
    "cobble": ("COBBLESTONE", (0.40, 0.37, 0.34), 0.9),
    "gravel": ("GRAVEL", (0.52, 0.46, 0.37), 1.0),
    "shoulder": ("GRAVEL", (0.42, 0.40, 0.34), 1.0),
    "apron": ("GRASS", (0.33, 0.42, 0.25), 1.0),
    "marking": ("ASPHALT", (0.92, 0.92, 0.90), 0.6),
    "wall": ("CONCRETE", (0.66, 0.66, 0.66), 0.9),
}
NAV_MATERIAL = "rf_road_invisible"
# AI speed and behaviour hints per road type: drivability as BeamNG's navigation uses it
DRIVABILITY = {"track": 0.5, "service": 0.7}


def _id(level_id: str, name: str) -> str:
    """Stable id per object, so building the same map twice gives identical files."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"roadforge/{level_id}/{name}"))


def material_name(surface: str) -> str:
    return f"rf_{surface}"


def _lines(objects: list[dict[str, Any]]) -> str:
    """items.level.json: one JSON object per line, not an array."""
    return "".join(json.dumps(o, separators=(",", ":"), sort_keys=True) + "\n" for o in objects)


def _png(path: Path, array: NDArray[Any]) -> None:
    """array: (bands, h, w) uint8 or uint16."""
    profile = {"driver": "PNG", "width": array.shape[2], "height": array.shape[1], "count": array.shape[0]}
    with rasterio.open(path, "w", dtype=array.dtype.name, **profile) as ds:
        ds.write(array)
    for extra in path.parent.glob(path.name + ".aux.xml"):
        extra.unlink()


def _flat_texture(path: Path, rgb: tuple[int, int, int], grain: int, seed: int) -> None:
    rng = np.random.default_rng(seed)
    noise = rng.integers(-grain, grain + 1, (TEX, TEX)) if grain else np.zeros((TEX, TEX), dtype=int)
    bands = [np.clip(c + noise, 0, 255).astype(np.uint8) for c in rgb]
    _png(path, np.stack(bands))


def shrink_for_beamng(dtm: Dtm) -> tuple[NDArray[np.float32], float]:
    """Halve the grid until it fits BeamNG's limit, keeping the lowest height of each block so
    the terrain still stays under the roads."""
    z, res = dtm.z, dtm.res
    while max(z.shape) > MAX_TERRAIN:
        rows, cols = (z.shape[0] // 2) * 2, (z.shape[1] // 2) * 2
        z = z[:rows, :cols].reshape(rows // 2, 2, cols // 2, 2).min(axis=(1, 3))
        res *= 2
    return z, res


def road_nodes(p: Profile, spacing: float = 8.0) -> list[list[float]]:
    """Centreline points [x, y, z, width] for a DecalRoad: both ends exactly, then every few metres."""
    keep = [0]
    for i in range(1, len(p.s) - 1):
        if p.s[i] - p.s[keep[-1]] >= spacing and p.s[-1] - p.s[i] >= spacing / 2:
            keep.append(i)
    if len(p.s) > 1:
        keep.append(len(p.s) - 1)
    return [
        [
            round(float(p.xy[i, 0]), 3),
            round(float(p.xy[i, 1]), 3),
            round(float(p.z[i]), 3),
            round(p.edge.width, 2),
        ]
        for i in keep
    ]


def decal_road(p: Profile, level_id: str) -> dict[str, Any]:
    e = p.edge
    name = f"road_{e.id:05d}"
    if e.oneway:
        left, right = 0, max(1, e.lanes)
    else:
        left = max(1, e.lanes // 2)
        right = max(1, e.lanes - left)
    return {
        "class": "DecalRoad",
        "name": name,
        "__parent": "Roads",
        "persistentId": _id(level_id, name),
        "position": road_nodes(p)[0][:3],
        "material": NAV_MATERIAL,
        "nodes": road_nodes(p),
        "drivability": DRIVABILITY.get(e.cls, 1.0),
        "oneWay": bool(e.oneway),
        "lanesLeft": left,
        "lanesRight": right,
        "improvedSpline": True,
        "overObjects": True,  # the car drives on our road mesh, which lies above the terrain
        "textureLength": 5,
        "renderPriority": 10,
        "breakAngle": 3,
    }


def heading_matrix(heading_deg: float) -> list[float]:
    """Rotation about the up axis for a compass heading (0 = north = +y, 90 = east = +x)."""
    a = math.radians(heading_deg)
    c, s = round(math.cos(a), 6), round(math.sin(a), 6)
    return [c, -s, 0, s, c, 0, 0, 0, 1]


@dataclass
class LevelInputs:
    level_id: str
    title: str
    dtm: Dtm
    road_meshes: list[MeshOut]
    profiles: list[Profile]
    spawn: dict[str, Any]
    credits: str
    country: str = ""


def _preview(path: Path, dtm: Dtm, profiles: list[Profile], size: int = 512) -> None:
    """A simple top-down picture: green ground shaded by height, roads in light grey."""
    rows, cols = dtm.shape
    step = max(1, math.ceil(max(rows, cols) / size))
    z = dtm.z[::step, ::step].astype(np.float64)
    lo, hi = float(np.min(z)), float(np.max(z))
    shade = (z - lo) / max(hi - lo, 1.0)
    img = np.stack([60 + 60 * shade, 105 + 70 * shade, 55 + 50 * shade]).astype(np.uint8)
    for p in profiles:
        row, col = dtm.to_cell(p.xy[:, 0], p.xy[:, 1])
        r = np.clip(np.rint(row / step).astype(int), 0, img.shape[1] - 1)
        c = np.clip(np.rint(col / step).astype(int), 0, img.shape[2] - 1)
        img[:, r, c] = np.array([[225], [225], [220]], dtype=np.uint8)
    canvas = np.zeros((3, size, size), dtype=np.uint8)
    h, w = min(size, img.shape[1]), min(size, img.shape[2])
    top, left = (size - h) // 2, (size - w) // 2
    canvas[:, top : top + h, left : left + w] = img[:, :h, :w]
    _png(path, canvas)


def write_level(root: Path, inp: LevelInputs) -> dict[str, Any]:
    """Create levels/<id>/... under root. Returns a summary for the log and the tests."""
    lid = inp.level_id
    base = f"/levels/{lid}"
    level = root / "levels" / lid
    for sub in [
        "main/MissionGroup/Roads",
        "main/MissionGroup/RoadMeshes",
        "art/terrains",
        "art/shapes/roads",
        "art/road",
    ]:
        (level / sub).mkdir(parents=True, exist_ok=True)

    # -- terrain ---------------------------------------------------------------
    z, res = shrink_for_beamng(inp.dtm)
    grid: TerrainGrid = pack_heights(z, inp.dtm.west, inp.dtm.north, res, TERRAIN_MATERIAL)
    write_ter(level / "theTerrain.ter", grid.ter)
    size = grid.ter.size
    (level / "theTerrain.terrain.json").write_text(
        json.dumps(
            {
                "binaryFormat": "version(char), size(unsigned int), "
                "heightMap(heightMapSize * heightMapItemSize), "
                "layerMap(layerMapSize * layerMapItemSize), materialNames",
                "datafile": f"{base}/theTerrain.ter",
                "heightMapItemSize": 2,
                "heightMapSize": size * size,
                "heightmapImage": f"{base}/theTerrain.terrainheightmap.png",
                "layerMapItemSize": 1,
                "layerMapSize": size * size,
                "materials": [TERRAIN_MATERIAL],
                "size": size,
                "version": 9,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    _png(level / "theTerrain.terrainheightmap.png", grid.ter.heights[::-1][None, :, :])  # picture: north up
    tex = f"{base}/art/terrains"
    _flat_texture(level / "art/terrains/rf_grass_b.png", (86, 116, 62), 10, 1)
    _flat_texture(level / "art/terrains/rf_flat_nm.png", (128, 128, 255), 0, 2)
    _flat_texture(level / "art/terrains/rf_rough_r.png", (235, 235, 235), 0, 3)
    _flat_texture(level / "art/terrains/rf_white_ao.png", (255, 255, 255), 0, 4)
    _flat_texture(level / "art/terrains/rf_mid_h.png", (128, 128, 128), 0, 5)
    terrain_material: dict[str, Any] = {
        "class": "TerrainMaterial",
        "name": f"{TERRAIN_MATERIAL}-{_id(lid, 'terrain-material')}",
        "internalName": TERRAIN_MATERIAL,
        "persistentId": _id(lid, "terrain-material"),
        "groundmodelName": "GRASS",
        "annotation": "NATURE",
        "version": 1.5,
    }
    for kind, file in (("baseColor", "rf_grass_b"), ("normal", "rf_flat_nm"), ("roughness", "rf_rough_r"),
                        ("ao", "rf_white_ao"), ("height", "rf_mid_h")):  # fmt: skip
        for scale, metres in (("Base", 256), ("Macro", 60), ("Detail", 2)):
            terrain_material[f"{kind}{scale}Tex"] = f"{tex}/{file}.png"
            terrain_material[f"{kind}{scale}TexSize"] = metres
    texture_set = {
        "class": "TerrainMaterialTextureSet",
        "name": "rf_TerrainMaterialTextureSet",
        "persistentId": _id(lid, "terrain-texture-set"),
        "baseTexSize": [TEX, TEX],
        "detailTexSize": [TEX, TEX],
        "macroTexSize": [TEX, TEX],
    }
    (level / "art/terrains/main.materials.json").write_text(
        json.dumps(
            {terrain_material["name"]: terrain_material, texture_set["name"]: texture_set},
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    # -- road meshes -----------------------------------------------------------
    blocks: dict[tuple[int, int], list[MeshOut]] = {}
    for m in inp.road_meshes:
        blocks.setdefault((m.tile[0] // BLOCK_TILES, m.tile[1] // BLOCK_TILES), []).append(m)
    statics: list[dict[str, Any]] = []
    for (bx, by), meshes in sorted(blocks.items()):
        name = f"roads_{bx:02d}_{by:02d}"
        parts = [
            (
                m.name,
                material_name(m.surface),
                m.vertices.astype(np.float64),
                m.uvs.astype(np.float64),
                m.faces.astype(np.int64),
            )
            for m in sorted(meshes, key=lambda m: m.name)
        ]
        write_dae(level / "art/shapes/roads" / f"{name}.dae", parts)
        statics.append(
            {
                "class": "TSStatic",
                "name": name,
                "__parent": "RoadMeshes",
                "persistentId": _id(lid, name),
                "shapeName": f"{base}/art/shapes/roads/{name}.dae",
                "position": [0, 0, 0],
                "rotationMatrix": [1, 0, 0, 0, 1, 0, 0, 0, 1],
                "scale": [1, 1, 1],
                "collisionType": "Visible Mesh Final",
                "decalType": "Visible Mesh Final",
                "useInstanceRenderData": True,
            }
        )
    road_materials: dict[str, Any] = {}
    for surface, (ground, rgb, rough) in sorted(SURFACES.items()):
        name = material_name(surface)
        road_materials[name] = {
            "class": "Material",
            "name": name,
            "mapTo": name,
            "persistentId": _id(lid, name),
            "Stages": [
                {"baseColorFactor": [*rgb, 1], "roughnessFactor": rough, "metallicFactor": 0},
                {},
                {},
                {},
            ],
            "groundType": ground,
            "doubleSided": surface == "wall",
            "version": 1.5,
        }
    (level / "art/shapes/roads/main.materials.json").write_text(
        json.dumps(road_materials, indent=2, sort_keys=True), encoding="utf-8"
    )
    nav_material = {
        NAV_MATERIAL: {
            "class": "Material",
            "name": NAV_MATERIAL,
            "mapTo": NAV_MATERIAL,
            "persistentId": _id(lid, NAV_MATERIAL),
            "Stages": [{"baseColorFactor": [1, 1, 1, 0]}, {}, {}, {}],
            "translucent": True,
            "translucentBlendOp": "LerpAlpha",
            "translucentZWrite": False,
            "version": 1.5,
        }
    }
    (level / "art/road/main.materials.json").write_text(
        json.dumps(nav_material, indent=2, sort_keys=True), encoding="utf-8"
    )

    # -- scene objects ---------------------------------------------------------
    def group(name: str, parent: str | None) -> dict[str, Any]:
        obj = {"class": "SimGroup", "name": name, "persistentId": _id(lid, f"group-{name}")}
        return {**obj, "__parent": parent} if parent else obj

    spawn_xyz = [float(v) for v in inp.spawn["xyz"]]
    top = [
        {
            "class": "LevelInfo",
            "name": "theLevelInfo",
            "__parent": "MissionGroup",
            "persistentId": _id(lid, "theLevelInfo"),
            "gravity": -9.81,
            "nearClip": 0.1,
            "visibleDistance": 6000,
            "fogColor": [0.6, 0.72, 0.85, 1],
            "fogDensity": 0.0001,
            "canvasClearColor": [0.6, 0.72, 0.85, 1],
        },
        {
            "class": "TimeOfDay",
            "name": "tod",
            "__parent": "MissionGroup",
            "persistentId": _id(lid, "tod"),
            "startTime": 0.1,
            "time": 0.1,
            "play": False,
            "dayLength": 1800,
            "axisTilt": 23.44,
            "azimuthOverride": 0,
        },
        {
            "class": "ScatterSky",
            "name": "sunsky",
            "__parent": "MissionGroup",
            "persistentId": _id(lid, "sunsky"),
            "position": [0, 0, 0],
            "shadowDistance": 1200,
            "brightness": 40,
            "skyBrightness": 40,
            "sunScale": [0.99, 0.94, 0.83, 1],
            "ambientScale": [0.55, 0.62, 0.72, 1],
            "castShadows": True,
        },
        {
            "class": "TerrainBlock",
            "name": "theTerrain",
            "__parent": "MissionGroup",
            "persistentId": _id(lid, "theTerrain"),
            "terrainFile": f"{base}/theTerrain.ter",
            "materialTextureSet": texture_set["name"],
            "squareSize": grid.square_size,
            "maxHeight": grid.max_height,
            "position": [round(v, 3) for v in grid.position],
            "baseTexSize": 1024,
            "castShadows": True,
        },
        {
            "class": "SpawnSphere",
            "name": "spawns_default",
            "__parent": "MissionGroup",
            "persistentId": _id(lid, "spawns_default"),
            "dataBlock": "SpawnSphereMarker",
            "position": [round(spawn_xyz[0], 3), round(spawn_xyz[1], 3), round(spawn_xyz[2], 3)],
            "rotationMatrix": heading_matrix(float(inp.spawn.get("heading_deg", 0.0))),
            "radius": 1,
            "sphereWeight": 1,
            "indoorWeight": 1,
            "outdoorWeight": 1,
        },
        group("RoadMeshes", "MissionGroup"),
        group("Roads", "MissionGroup"),
    ]
    roads = [decal_road(p, lid) for p in inp.profiles if len(p.s) >= 2 and p.length >= 2.0]
    (level / "main/items.level.json").write_text(_lines([group("MissionGroup", None)]), encoding="utf-8")
    (level / "main/MissionGroup/items.level.json").write_text(_lines(top), encoding="utf-8")
    (level / "main/MissionGroup/RoadMeshes/items.level.json").write_text(_lines(statics), encoding="utf-8")
    (level / "main/MissionGroup/Roads/items.level.json").write_text(_lines(roads), encoding="utf-8")

    # -- info + preview --------------------------------------------------------
    _preview(level / "preview.png", inp.dtm, inp.profiles)
    extent = round(size * grid.square_size)
    info = {
        "title": inp.title,
        "description": f"{inp.title}: a real place rebuilt with {brand()['name']}. "
        "See CREDITS.txt for the data sources.",
        "authors": brand()["name"],
        "previews": ["preview.png"],
        "size": [extent, extent],
        "country": inp.country,
        "region": "",
        "biome": "",
        "roads": "Real roads from OpenStreetMap",
        "suitablefor": "Freeroam",
        "features": "Real-world roads and terrain",
        "defaultSpawnPointName": "spawns_default",
        "spawnPoints": [{"translationId": "Start", "objectname": "spawns_default", "preview": "preview.png"}],
        "roadRules": {"rightHandDrive": False},
        "supportsTraffic": True,
        "supportsTimeOfDay": True,
    }
    (level / "info.json").write_text(
        json.dumps(info, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8"
    )
    (level / "CREDITS.txt").write_text(inp.credits, encoding="utf-8")
    return {
        "level_id": lid,
        "terrain_size": size,
        "square_size_m": grid.square_size,
        "max_height_m": grid.max_height,
        "terrain_position": list(grid.position),
        "road_mesh_files": len(statics),
        "ai_roads": len(roads),
    }


def zip_level(root: Path, zip_path: Path) -> int:
    """Mod ZIP whose root holds levels/<id>/... Fixed dates and order, so rebuilds are identical."""
    files = sorted(p for p in root.rglob("*") if p.is_file())
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in files:
            entry = zipfile.ZipInfo(path.relative_to(root).as_posix(), date_time=(2020, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o644 << 16
            zf.writestr(entry, path.read_bytes())
    return zip_path.stat().st_size
