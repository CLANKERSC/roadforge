"""Buildings from OpenStreetMap footprints: walls with windows, a pitched or flat roof.

Every building stands where the map says, with the height the map gives (or a sensible guess
by type and size). Nothing is copied from photos; facades are generic until street-level data
is added.
"""

from __future__ import annotations

import math
import zlib
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from numpy.typing import NDArray
from shapely import STRtree
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry
from shapely.geometry.polygon import orient
from shapely.ops import triangulate

from forge_worker.geo import Georef
from forge_worker.terrain import Dtm

A = NDArray[np.float64]

STOREY_M = 3.0
WINDOW_TILE_M = 3.0  # one window per 3 m of wall and per storey
ROOF_TILE_M = 2.0
WALL_STYLES = 4  # facade colours, picked per building
SMALL = {"garage", "garages", "shed", "hut", "carport", "kiosk", "greenhouse", "roof", "cabin"}
HOUSES = {"house", "detached", "semidetached_house", "bungalow", "residential", "terrace", "farm"}
TALL = {"apartments": 14.0, "church": 14.0, "cathedral": 22.0, "chapel": 7.0, "hotel": 14.0,
        "office": 14.0, "school": 10.0, "hospital": 14.0, "industrial": 8.0, "warehouse": 8.0,
        "retail": 6.0, "commercial": 9.0, "barn": 7.0, "farm_auxiliary": 5.0}  # fmt: skip


@dataclass(frozen=True)
class Building:
    osm_id: int
    footprint: Polygon  # local metres, outer ring counter-clockwise, no holes
    height: float  # ground to eave
    kind: str
    pitched: bool


@dataclass
class Soup:
    """Flat-shaded triangles of one material."""

    vertices: list[tuple[float, float, float]] = field(default_factory=list)
    uvs: list[tuple[float, float]] = field(default_factory=list)
    faces: list[tuple[int, int, int]] = field(default_factory=list)

    def quad(self, p: list[tuple[float, float, float]], uv: list[tuple[float, float]]) -> None:
        """Corners counter-clockwise seen from outside."""
        i = len(self.vertices)
        self.vertices += p
        self.uvs += uv
        self.faces += [(i, i + 1, i + 2), (i, i + 2, i + 3)]

    def tri(self, p: list[tuple[float, float, float]], uv: list[tuple[float, float]]) -> None:
        i = len(self.vertices)
        self.vertices += p
        self.uvs += uv
        self.faces.append((i, i + 1, i + 2))

    def arrays(self) -> tuple[A, A, NDArray[np.int64]]:
        return (
            np.asarray(self.vertices, dtype=np.float64).reshape(-1, 3),
            np.asarray(self.uvs, dtype=np.float64).reshape(-1, 2),
            np.asarray(self.faces, dtype=np.int64).reshape(-1, 3),
        )


def _metres(text: str | None) -> float | None:
    if not text:
        return None
    try:
        value = float(text.replace(",", ".").replace("m", "").strip())
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def building_height(tags: dict[str, str], area: float) -> float:
    """Eave height in metres: the map's own number when it has one, else a guess."""
    kind = tags.get("building", "yes")
    height = _metres(tags.get("height"))
    roof = _metres(tags.get("roof:height")) or 0.0
    if height is not None and 2.0 <= height <= 300.0:
        return max(2.2, height - roof)
    levels = _metres(tags.get("building:levels"))
    if levels is not None and 0.5 <= levels <= 80:
        return max(2.5, levels * STOREY_M + 0.4)
    if kind in SMALL or area < 35.0:
        return 2.8
    if kind in TALL:
        return TALL[kind]
    if kind in HOUSES or area < 260.0:
        return 3.4 if area < 90.0 else 6.2  # a cottage or a two-storey house
    return 7.5  # a big shape with no other hint: a hall


def load_buildings(
    osm_nodes: dict[int, tuple[float, float]],
    ways: list[dict[str, Any]],
    georef: Georef,
    clip: BaseGeometry,
    roads: list[BaseGeometry],
) -> tuple[list[Building], dict[str, int]]:
    """Footprints inside the selection. Buildings the map draws on top of a road are left out,
    because the road must stay drivable."""
    stats = {"buildings": 0, "buildings_on_road_left_out": 0}
    tree = STRtree(roads) if roads else None
    out: list[Building] = []
    for way in ways:
        tags = way.get("tags", {})
        kind = tags.get("building")
        refs = [n for n in way.get("nodes", []) if n in osm_nodes]
        if not kind or kind == "no" or len(refs) < 4 or refs[0] != refs[-1]:
            continue
        if tags.get("location") == "underground" or tags.get("building:part"):
            continue
        poly = Polygon([georef.to_local(*osm_nodes[n]) for n in refs])
        if not poly.is_valid:
            poly = poly.buffer(0)
        if poly.is_empty or not isinstance(poly, Polygon) or poly.area < 6.0 or not clip.contains(poly):
            continue
        simple = Polygon(poly.exterior).simplify(0.15)
        if not isinstance(simple, Polygon) or simple.is_empty or len(simple.exterior.coords) < 4:
            continue
        poly = orient(simple, sign=1.0)
        if tree is not None and any(roads[i].intersects(poly) for i in tree.query(poly)):
            stats["buildings_on_road_left_out"] += 1
            continue
        height = building_height(tags, float(poly.area))
        flat = tags.get("roof:shape") == "flat" or kind in {"industrial", "warehouse", "apartments", "retail"}
        out.append(Building(int(way["id"]), poly, height, kind, pitched=not flat and height < 13.0))
    stats["buildings"] = len(out)
    return out, stats


def _rectangle(poly: Polygon) -> A | None:
    """The 4 corners (counter-clockwise) when the footprint is close to a rectangle."""
    box = poly.minimum_rotated_rectangle
    if not isinstance(box, Polygon) or box.area <= 0 or poly.area / box.area < 0.82:
        return None
    return np.asarray(orient(box, sign=1.0).exterior.coords[:4], dtype=np.float64)


def style_of(b: Building) -> int:
    return zlib.crc32(str(b.osm_id).encode()) % WALL_STYLES


def building_meshes(buildings: list[Building], dtm: Dtm) -> dict[str, Soup]:
    """Material name -> triangles. Materials: wall_0..3, roof_tile, roof_flat."""
    soups: dict[str, Soup] = {f"wall_{i}": Soup() for i in range(WALL_STYLES)}
    soups["roof_tile"], soups["roof_flat"] = Soup(), Soup()
    for b in buildings:
        ring = np.asarray(b.footprint.exterior.coords[:-1], dtype=np.float64)
        ground = dtm.sample(ring[:, 0], ring[:, 1])
        low, high = float(np.min(ground)), float(np.max(ground))
        base = low - 0.6  # the walls go into the ground, so no gap on a slope
        eave = high + b.height
        wall = soups[f"wall_{style_of(b)}"]
        storeys = max(1, round(b.height / STOREY_M))
        v_top = float(storeys)  # whole storeys from the eave down, so windows are never cut at the top
        v_base = v_top - (eave - base) / (b.height / storeys)
        n = len(ring)
        for i in range(n):
            p, q = ring[i], ring[(i + 1) % n]
            length = float(np.hypot(*(q - p)))
            if length < 0.05:
                continue
            windows = float(round(length / WINDOW_TILE_M))  # whole windows per wall; 0 = blank wall
            wall.quad(
                [(p[0], p[1], base), (q[0], q[1], base), (q[0], q[1], eave), (p[0], p[1], eave)],
                [(0.0, v_base), (windows, v_base), (windows, v_top), (0.0, v_top)],
            )
        rect = _rectangle(b.footprint) if b.pitched else None
        if rect is None:
            roof = soups["roof_flat"]
            for tri in triangulate(b.footprint):
                if not b.footprint.contains(tri.representative_point()):
                    continue
                c = np.asarray(orient(tri, sign=1.0).exterior.coords[:3], dtype=np.float64)
                roof.tri([(x, y, eave) for x, y in c], [(x / ROOF_TILE_M, y / ROOF_TILE_M) for x, y in c])
            continue
        _gable_roof(soups["roof_tile"], wall, rect, eave)
    return {k: s for k, s in soups.items() if s.faces}


def _gable_roof(roof: Soup, wall: Soup, rect: A, eave: float) -> None:
    """A ridge along the long side, with a small overhang; the two end triangles are wall."""
    a, b, c, d = rect
    if np.hypot(*(b - a)) < np.hypot(*(d - a)):  # make a->b the long side
        a, b, c, d = b, c, d, a
    long_v, short_v = b - a, d - a
    length, width = float(np.hypot(*long_v)), float(np.hypot(*short_v))
    lu, su = long_v / length, short_v / width
    rise = min(4.5, max(0.8, width * 0.38))  # about a 37 degree roof
    over = 0.35
    ridge0, ridge1 = a + short_v / 2, b + short_v / 2
    for sign, e0, e1 in ((-1.0, a, b), (1.0, d, c)):
        o0 = e0 - lu * over + su * sign * over
        o1 = e1 + lu * over + su * sign * over
        r0, r1 = ridge0 - lu * over, ridge1 + lu * over
        drop = rise * over / (width / 2)
        slope = math.hypot(width / 2 + over, rise + drop) / ROOF_TILE_M
        run = (length + 2 * over) / ROOF_TILE_M
        quad = [(o0[0], o0[1], eave - drop), (o1[0], o1[1], eave - drop),
                (r1[0], r1[1], eave + rise), (r0[0], r0[1], eave + rise)]  # fmt: skip
        uv = [(0.0, 0.0), (run, 0.0), (run, slope), (0.0, slope)]
        if sign > 0:  # keep the outside counter-clockwise on the far side too
            quad, uv = [quad[1], quad[0], quad[3], quad[2]], [uv[1], uv[0], uv[3], uv[2]]
        roof.quad(quad, uv)
    for p, q, r in ((d, a, ridge0), (b, c, ridge1)):  # gable ends, blank wall
        wall.tri(
            [(p[0], p[1], eave), (q[0], q[1], eave), (r[0], r[1], eave + rise)],
            [(0.0, 0.0), (0.0, 0.0), (0.0, 0.0)],
        )
