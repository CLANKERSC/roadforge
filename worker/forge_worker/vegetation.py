"""Trees and bushes from OpenStreetMap: single trees, tree rows, forests, orchards, scrub.

Only positions and kinds are decided here. The models are the game's own (BeamNG ships a tree
library with every install); nothing is copied into the mod.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray
from shapely import contains_xy, prepare
from shapely.geometry import LineString, MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import polygonize, unary_union

from forge_worker.geo import Georef
from forge_worker.terrain import Dtm

MAX_PLANTS = 60_000  # more than this and the spacing in forests is widened
FOREST_SPACING_M = 7.5
SCRUB_SPACING_M = 5.0
ORCHARD_SPACING_M = 6.0
ROW_SPACING_M = 9.0
WOODED = {("landuse", "forest"), ("natural", "wood")}
SCRUB = {("natural", "scrub"), ("natural", "heath")}
ORCHARD = {("landuse", "orchard")}


@dataclass(frozen=True)
class Plant:
    x: float
    y: float
    z: float
    kind: str  # forest / single / row / orchard / scrub
    pick: float  # 0..1, chooses the model
    scale: float
    turn_deg: float


def load_areas(
    paths: list[Path], georef: Georef
) -> tuple[dict[str, list[BaseGeometry]], list[tuple[float, float]], list[LineString]]:  # noqa: E501
    """(areas by kind, single tree points, tree rows) in local metres.

    Reads the raw map files again, because large forests are multipolygon relations whose
    outline ways carry no tags of their own.
    """
    nodes: dict[int, tuple[float, float]] = {}
    ways: dict[int, dict[str, Any]] = {}
    relations: dict[int, dict[str, Any]] = {}
    trees: dict[int, tuple[float, float]] = {}
    for path in sorted(paths):
        for el in json.loads(path.read_text(encoding="utf-8")).get("elements", []):
            if el["type"] == "node":
                nodes[el["id"]] = (el["lon"], el["lat"])
                if el.get("tags", {}).get("natural") == "tree":
                    trees[el["id"]] = (el["lon"], el["lat"])
            elif el["type"] == "way":
                ways[el["id"]] = el
            elif el["type"] == "relation":
                relations[el["id"]] = el

    def kind_of(tags: dict[str, str]) -> str | None:
        for key in ("landuse", "natural"):
            pair = (key, tags.get(key, ""))
            if pair in WOODED:
                return "forest"
            if pair in SCRUB:
                return "scrub"
            if pair in ORCHARD:
                return "orchard"
        return None

    def line(way: dict[str, Any]) -> LineString | None:
        pts = [georef.to_local(*nodes[n]) for n in way.get("nodes", []) if n in nodes]
        return LineString(pts) if len(pts) >= 2 else None

    areas: dict[str, list[BaseGeometry]] = {"forest": [], "scrub": [], "orchard": []}
    rows: list[LineString] = []
    for wid in sorted(ways):
        way = ways[wid]
        tags = way.get("tags", {})
        ln = line(way)
        if ln is None:
            continue
        if tags.get("natural") == "tree_row":
            rows.append(ln)
            continue
        kind = kind_of(tags)
        if kind and ln.is_ring and len(ln.coords) >= 4:
            poly = Polygon(ln.coords).buffer(0)
            if not poly.is_empty:
                areas[kind].append(poly)
    for rid in sorted(relations):
        rel = relations[rid]
        tags = rel.get("tags", {})
        kind = kind_of(tags)
        if not kind or tags.get("type") != "multipolygon":
            continue
        outer: list[LineString] = []
        inner: list[LineString] = []
        for m in rel.get("members", []):
            if m.get("type") != "way" or m.get("ref") not in ways:
                continue
            ln = line(ways[m["ref"]])
            if ln is not None:
                (inner if m.get("role") == "inner" else outer).append(ln)
        shape = unary_union(list(polygonize(unary_union(outer)))) if outer else None
        if shape is None or shape.is_empty:
            continue
        holes = unary_union(list(polygonize(unary_union(inner)))) if inner else None
        if holes is not None and not holes.is_empty:
            shape = shape.difference(holes)
        if not shape.is_empty:
            areas[kind].append(shape)
    points = [georef.to_local(*trees[k]) for k in sorted(trees)]
    return areas, points, rows


def _polygons(geom: BaseGeometry) -> list[Polygon]:
    if isinstance(geom, Polygon):
        return [] if geom.is_empty else [geom]
    if isinstance(geom, MultiPolygon):
        return [g for g in geom.geoms if not g.is_empty]
    return [p for sub in getattr(geom, "geoms", []) for p in _polygons(sub)]


def _scatter(
    geom: BaseGeometry, spacing: float, rng: np.random.Generator, jitter: float
) -> NDArray[np.float64]:
    """Points on a jittered grid inside a shape: even cover without visible rows."""
    out: list[NDArray[np.float64]] = []
    for poly in _polygons(geom):
        minx, miny, maxx, maxy = poly.bounds
        xs = np.arange(math.floor(minx / spacing), math.ceil(maxx / spacing) + 1) * spacing
        ys = np.arange(math.floor(miny / spacing), math.ceil(maxy / spacing) + 1) * spacing
        gx, gy = np.meshgrid(xs, ys)
        pts = np.column_stack([gx.ravel(), gy.ravel()])
        pts += rng.uniform(-jitter, jitter, pts.shape) * spacing
        prepare(poly)
        out.append(pts[contains_xy(poly, pts[:, 0], pts[:, 1])])
    return np.concatenate(out) if out else np.zeros((0, 2))


def place_plants(
    areas: dict[str, list[BaseGeometry]],
    points: list[tuple[float, float]],
    rows: list[LineString],
    clip: BaseGeometry,
    keep_clear: list[BaseGeometry],
    dtm: Dtm,
    seed: int,
) -> tuple[list[Plant], dict[str, int]]:
    """Every tree and bush of the map area. `keep_clear` holds roads and buildings (already
    widened by the clearance wanted): nothing is planted there, so no tree blocks a road."""
    rng = np.random.default_rng(seed)
    blocked = unary_union(keep_clear) if keep_clear else None
    stats = {"left_out_near_roads_or_buildings": 0}

    def shape(kind: str) -> BaseGeometry:
        geom = unary_union(areas.get(kind, [])).intersection(clip) if areas.get(kind) else Polygon()
        return geom.difference(blocked) if blocked is not None and not geom.is_empty else geom

    forest, scrub, orchard = shape("forest"), shape("scrub"), shape("orchard")
    spacing = FOREST_SPACING_M
    expected = forest.area / spacing**2
    if expected > MAX_PLANTS * 0.8:
        spacing *= math.sqrt(expected / (MAX_PLANTS * 0.8))  # a huge forest gets thinner, not cut off
    sets: list[tuple[str, NDArray[np.float64]]] = [
        ("forest", _scatter(forest, spacing, rng, 0.42)),
        ("scrub", _scatter(scrub, SCRUB_SPACING_M, rng, 0.45)),
        ("orchard", _scatter(orchard, ORCHARD_SPACING_M, rng, 0.08)),
    ]
    loose: list[tuple[str, tuple[float, float]]] = [("single", p) for p in points]
    for row in rows:
        n = max(2, round(row.length / ROW_SPACING_M) + 1)
        for d in np.linspace(0.0, row.length, n):
            c = row.interpolate(float(d)).coords[0]
            loose.append(("row", (float(c[0]), float(c[1]))))
    for kind in ("single", "row"):
        pts = np.asarray([p for k, p in loose if k == kind], dtype=np.float64).reshape(-1, 2)
        if len(pts):
            ok = contains_xy(clip, pts[:, 0], pts[:, 1])
            if blocked is not None:
                free = ~contains_xy(blocked, pts[:, 0], pts[:, 1])
                stats["left_out_near_roads_or_buildings"] += int((ok & ~free).sum())
                ok &= free
            pts = pts[ok]
        sets.append((kind, pts))
    plants: list[Plant] = []
    for kind, pts in sets:
        if not len(pts):
            continue
        z = dtm.sample(pts[:, 0], pts[:, 1])
        pick = rng.uniform(0.0, 1.0, len(pts))
        scale = rng.uniform(0.8, 1.25, len(pts))
        turn = rng.uniform(0.0, 360.0, len(pts))
        plants += [
            Plant(float(x), float(y), float(h) - 0.1, kind, float(p), float(s), float(t))
            for (x, y), h, p, s, t in zip(pts, z, pick, scale, turn, strict=True)
        ]
    for kind in ("forest", "scrub", "orchard", "single", "row"):
        stats[kind] = sum(1 for p in plants if p.kind == kind)
    return plants, stats
