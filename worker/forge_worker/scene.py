"""Stage 08: the engine-neutral scene. Tiled meshes -> scene.glb (preview) + scene.json.

scene.json and all arrays here use local metres with z up (x east, y north). Only the GLB is
converted to glTF's y-up convention for the preview. Exporters convert again per game.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import trimesh
from numpy.typing import NDArray
from scipy import ndimage
from shapely import contains_xy
from shapely.geometry import box
from shapely.geometry.base import BaseGeometry

from forge_worker.roadgeom import Profile
from forge_worker.roadmesh import RoadMesh
from forge_worker.terrain import Dtm

A = NDArray[np.float64]
TILE_CELLS = 128  # terrain tile = 128 cells (256 m at 2 m)
COARSE_STEP = 4  # far-away tiles keep every 4th height sample
NEAR_ROAD_M = 30.0
MAX_VERTS = 65_000  # Assetto Corsa needs < 65,535 vertices per mesh

# surface -> preview colour (RGB 0-255). Real textures arrive with the exporters.
SURFACE_COLOURS: dict[str, tuple[int, int, int]] = {
    "asphalt": (62, 64, 68),
    "concrete": (150, 150, 145),
    "cobble": (110, 100, 92),
    "gravel": (140, 122, 96),
    "shoulder": (112, 108, 92),
    "apron": (98, 112, 78),
    "marking": (235, 235, 230),
    "wall": (170, 170, 170),
    "terrain": (88, 122, 72),
}
DRIVABLE = ("asphalt", "concrete", "cobble", "gravel", "shoulder")
ROAD_SURFACES = (*DRIVABLE, "apron")


@dataclass
class MeshOut:
    name: str
    surface: str
    tile: tuple[int, int]
    vertices: NDArray[np.float32]  # (n, 3) local, z up
    uvs: NDArray[np.float32]  # (n, 2) metres
    faces: NDArray[np.int32]  # (m, 3)


def _compact(vertices: A, uvs: A, faces: NDArray[np.int64]) -> tuple[A, A, NDArray[np.int64]]:
    used, inverse = np.unique(faces.reshape(-1), return_inverse=True)
    return vertices[used], uvs[used], inverse.reshape(-1, 3)


def _split(
    name: str, surface: str, tile: tuple[int, int], v: A, uv: A, f: NDArray[np.int64]
) -> list[MeshOut]:
    """One mesh, or several if it would pass the vertex limit."""
    out: list[MeshOut] = []
    chunk = MAX_VERTS // 3  # worst case: every triangle has its own 3 vertices
    parts = [f[i : i + chunk] for i in range(0, len(f), chunk)] if len(np.unique(f)) > MAX_VERTS else [f]
    for i, part in enumerate(parts):
        pv, puv, pf = _compact(v, uv, part)
        suffix = f"_{i}" if len(parts) > 1 else ""
        out.append(
            MeshOut(
                f"{name}{suffix}",
                surface,
                tile,
                pv.astype(np.float32),
                puv.astype(np.float32),
                pf.astype(np.int32),
            )
        )
    return out


def tile_meshes(prefix: str, surface: str, v: A, uv: A, f: NDArray[np.int64], dtm: Dtm) -> list[MeshOut]:
    """Split triangles by the terrain tile their centre falls in."""
    if len(f) == 0:
        return []
    size = TILE_CELLS * dtm.res
    centre = v[f].mean(axis=1)
    tx = np.floor((centre[:, 0] - dtm.west) / size).astype(int)
    ty = np.floor((dtm.north - centre[:, 1]) / size).astype(int)
    out: list[MeshOut] = []
    for key in sorted(set(zip(tx.tolist(), ty.tolist(), strict=True))):
        sel = (tx == key[0]) & (ty == key[1])
        out += _split(f"{prefix}_{key[0]:02d}_{key[1]:02d}", surface, key, v, uv, f[sel])
    return out


def road_meshes(mesh: RoadMesh, dtm: Dtm) -> list[MeshOut]:
    out: list[MeshOut] = []
    surf = np.asarray(mesh.face_surface)
    for surface in ROAD_SURFACES:
        out += tile_meshes(
            f"road_{surface}", surface, mesh.vertices, mesh.uvs, mesh.faces[surf == surface], dtm
        )
    for extra in mesh.extras:
        v = np.asarray(extra.vertices, dtype=np.float64).reshape(-1, 3)
        uv = np.asarray(extra.uvs, dtype=np.float64).reshape(-1, 2)
        f = np.asarray(extra.faces, dtype=np.int64).reshape(-1, 3)
        out += tile_meshes(extra.surface, extra.surface, v, uv, f, dtm)
    return out


def _grid_faces(rows: int, cols: int) -> NDArray[np.int64]:
    r, c = np.meshgrid(np.arange(rows - 1), np.arange(cols - 1), indexing="ij")
    a = (r * cols + c).reshape(-1)
    b, d, e = a + 1, a + cols, a + cols + 1
    # rows run north -> south, columns west -> east: (a, d, b) is counter-clockwise from above
    return np.concatenate([np.column_stack([a, d, b]), np.column_stack([b, d, e])])


def iter_terrain_meshes(dtm: Dtm, road_cells: NDArray[np.bool_], keep: BaseGeometry) -> Iterator[MeshOut]:
    """Terrain as tiles: full detail near roads, every 4th sample elsewhere, no cracks between.

    Tiles are produced one at a time, so a big map never has to hold all of them in memory.
    """
    rows, cols = dtm.shape
    xs, ys = dtm.cell_xy()
    if road_cells.any():
        near = ndimage.distance_transform_edt(~road_cells, sampling=dtm.res) <= NEAR_ROAD_M
    else:
        near = np.zeros_like(road_cells)
    n_ty, n_tx = math.ceil((rows - 1) / TILE_CELLS), math.ceil((cols - 1) / TILE_CELLS)
    fine = np.zeros((n_ty, n_tx), dtype=bool)
    spans: dict[tuple[int, int], tuple[int, int, int, int]] = {}
    for ty in range(n_ty):
        for tx in range(n_tx):
            r0, c0 = ty * TILE_CELLS, tx * TILE_CELLS
            r1, c1 = min(r0 + TILE_CELLS, rows - 1), min(c0 + TILE_CELLS, cols - 1)
            spans[(ty, tx)] = (r0, r1, c0, c1)
            fine[ty, tx] = bool(near[r0 : r1 + 1, c0 : c1 + 1].any())

    def coarse_index(lo: int, hi: int) -> NDArray[np.int64]:
        idx = np.arange(lo, hi + 1, COARSE_STEP)
        return idx if idx[-1] == hi else np.append(idx, hi)

    # the border of every coarse tile is flattened onto its coarse samples, so a detailed
    # neighbour (which shares those border cells) meets it without a crack
    z = dtm.z.astype(np.float64)
    for (ty, tx), (r0, r1, c0, c1) in spans.items():
        if fine[ty, tx]:
            continue
        ci, ri = coarse_index(c0, c1), coarse_index(r0, r1)
        for r in (r0, r1):
            z[r, c0 : c1 + 1] = np.interp(np.arange(c0, c1 + 1), ci, z[r, ci])
        for c in (c0, c1):
            z[r0 : r1 + 1, c] = np.interp(np.arange(r0, r1 + 1), ri, z[ri, c])

    for (ty, tx), (r0, r1, c0, c1) in sorted(spans.items()):
        if not box(xs[c0], ys[r1], xs[c1], ys[r0]).intersects(keep):
            continue
        if fine[ty, tx]:
            ri, ci = np.arange(r0, r1 + 1), np.arange(c0, c1 + 1)
        else:
            ri, ci = coarse_index(r0, r1), coarse_index(c0, c1)
        gx, gy = np.meshgrid(xs[ci], ys[ri])
        gz = z[np.ix_(ri, ci)]
        v = np.column_stack([gx.reshape(-1), gy.reshape(-1), gz.reshape(-1)])
        uv = v[:, :2] - np.array([dtm.west, dtm.north - rows * dtm.res])
        faces = _grid_faces(len(ri), len(ci))
        name = f"terrain_{tx:02d}_{ty:02d}" + ("" if fine[ty, tx] else "_far")
        yield MeshOut(
            name, "terrain", (tx, ty), v.astype(np.float32), uv.astype(np.float32), faces.astype(np.int32)
        )


def preview_terrain(dtm: Dtm, keep: BaseGeometry, max_vertices: int = 1_200_000) -> MeshOut:
    """One light terrain mesh for the 3D preview. On big maps it keeps every n-th sample and
    takes the lowest height around it, so the preview terrain never covers a road."""
    rows, cols = dtm.shape
    step = max(1, math.ceil(math.sqrt(rows * cols / max_vertices)))
    z = ndimage.minimum_filter(dtm.z, size=2 * step - 1, mode="nearest") if step > 1 else dtm.z
    xs, ys = dtm.cell_xy()
    ri, ci = np.arange(0, rows, step), np.arange(0, cols, step)
    gx, gy = np.meshgrid(xs[ci], ys[ri])
    v = np.column_stack([gx.reshape(-1), gy.reshape(-1), z[np.ix_(ri, ci)].reshape(-1)]).astype(np.float64)
    faces = _grid_faces(len(ri), len(ci))
    centre = v[faces].mean(axis=1)
    faces = faces[contains_xy(keep, centre[:, 0], centre[:, 1])]
    pv, puv, pf = _compact(v, v[:, :2].copy(), faces)
    return MeshOut(
        "terrain_preview",
        "terrain",
        (0, 0),
        pv.astype(np.float32),
        puv.astype(np.float32),
        pf.astype(np.int32),
    )


def write_glb(meshes: list[MeshOut], path: Path) -> None:
    scene = trimesh.Scene()
    for m in meshes:
        v = m.vertices.astype(np.float64)
        y_up = np.column_stack([v[:, 0], v[:, 2], -v[:, 1]])  # glTF: x right, y up, -z forward (north)
        r, g, b = SURFACE_COLOURS[m.surface]
        material = trimesh.visual.material.PBRMaterial(  # type: ignore[no-untyped-call]
            name=m.surface, baseColorFactor=[r, g, b, 255], metallicFactor=0.0,
            roughnessFactor=0.6 if m.surface == "marking" else 0.95, doubleSided=m.surface == "wall",
        )  # fmt: skip
        visual = trimesh.visual.TextureVisuals(  # type: ignore[no-untyped-call]
            uv=m.uvs.astype(np.float64), material=material
        )
        geom = trimesh.Trimesh(vertices=y_up, faces=m.faces, visual=visual, process=False)
        scene.add_geometry(geom, node_name=m.name, geom_name=m.name)
    path.write_bytes(scene.export(file_type="glb"))  # type: ignore[no-untyped-call]


def mesh_summary(m: MeshOut) -> dict[str, Any]:
    return {
        "name": m.name,
        "surface": m.surface,
        "tile": list(m.tile),
        "vertices": int(len(m.vertices)),
        "triangles": int(len(m.faces)),
    }


def _r(values: Any, digits: int = 3) -> Any:
    return np.round(np.asarray(values, dtype=np.float64), digits).tolist()


def scene_manifest(
    meshes: list[dict[str, Any]], profiles: list[Profile], node_xy: list[tuple[float, float]], node_z: A,
    georef: dict[str, Any], dtm: Dtm, spawn: dict[str, Any] | None,
) -> dict[str, Any]:  # fmt: skip
    """Everything an exporter needs besides the triangles. Deterministic: no timestamps."""
    return {
        "version": 1,
        "units": "metres",
        "axes": "x=east, y=north, z=up; origin = georef origin",
        "georef": georef,
        "terrain": {
            "res_m": dtm.res,
            "rows": dtm.shape[0],
            "cols": dtm.shape[1],
            "west": dtm.west,
            "north": dtm.north,
            "file": "dtm_conformed.tif",
        },
        "materials": {
            name: {"preview_rgb": list(rgb), "drivable": name in DRIVABLE}
            for name, rgb in sorted(SURFACE_COLOURS.items())
        },
        "meshes": meshes,
        "road_graph": {
            "nodes": [{"id": i, "xyz": _r([x, y, node_z[i]])} for i, (x, y) in enumerate(node_xy)],
            "edges": [
                {
                    "id": p.edge.id,
                    "a": p.edge.a,
                    "b": p.edge.b,
                    "class": p.edge.cls,
                    "name": p.edge.name,
                    "width_m": round(p.edge.width, 2),
                    "lanes": p.edge.lanes,
                    "oneway": p.edge.oneway,
                    "bridge": p.edge.bridge,
                    "tunnel": p.edge.tunnel,
                    "surface": p.edge.surface,
                    "maxspeed_kmh": p.edge.maxspeed,
                    "length_m": round(p.length, 2),
                    "osm_way_ids": p.edge.osm_ids,
                    "centreline": _r(np.column_stack([p.xy, p.z])),
                }
                for p in profiles
            ],  # fmt: skip
        },
        "spawn": spawn,
    }


def write_scene_json(manifest: dict[str, Any], path: Path) -> None:
    path.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")), encoding="utf-8")
