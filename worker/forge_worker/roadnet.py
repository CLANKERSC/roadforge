"""Stage 05a: OpenStreetMap ways -> a clean road graph in local metres.

Nodes are junctions or road ends; edges are stretches of road with one set of properties.
Things that would break driving are repaired here: roads that cross on the map without being
connected get a junction, duplicate points are dropped, tiny stubs are removed.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray
from shapely import STRtree
from shapely.geometry import LineString, MultiLineString, Point
from shapely.geometry.base import BaseGeometry

from forge_worker.geo import Georef

XY = NDArray[np.float64]

# class -> (default lanes, lane width m, max grade, AI speed cap km/h)
ROAD_CLASSES: dict[str, tuple[int, float, float, float]] = {
    "motorway": (2, 3.5, 0.08, 130),
    "trunk": (2, 3.5, 0.10, 110),
    "primary": (2, 3.25, 0.12, 90),
    "secondary": (2, 3.25, 0.14, 90),
    "tertiary": (2, 3.0, 0.16, 90),
    "unclassified": (2, 2.75, 0.20, 70),
    "residential": (2, 3.0, 0.20, 50),
    "living_street": (1, 4.0, 0.20, 20),
    "service": (1, 3.0, 0.22, 30),
    "track": (1, 2.5, 0.30, 30),
    "road": (2, 3.0, 0.20, 50),
}
SKIP_SERVICE = {"parking_aisle", "driveway", "drive-through"}
UNPAVED = {
    "unpaved",
    "gravel",
    "fine_gravel",
    "compacted",
    "dirt",
    "ground",
    "earth",
    "grass",
    "sand",
    "mud",
    "pebblestone",
}
MIN_WIDTH, MAX_WIDTH = 2.5, 30.0
MIN_EDGE_M = 3.0


def road_class(tags: dict[str, str]) -> str | None:
    hw = tags.get("highway", "")
    cls = hw[:-5] if hw.endswith("_link") else hw
    if cls not in ROAD_CLASSES or tags.get("area") == "yes":
        return None
    if cls == "service" and tags.get("service") in SKIP_SERVICE:
        return None
    if tags.get("access") in {"no", "private"} and cls in {"service", "track"}:
        return None
    return cls


def _number(text: str | None) -> float | None:
    if not text:
        return None
    m = re.match(r"\s*(\d+(?:[.,]\d+)?)", text)
    return float(m.group(1).replace(",", ".")) if m else None


def road_width(tags: dict[str, str], cls: str) -> tuple[float, int, bool]:
    """(width in metres, lanes, True if the width was measured rather than assumed)."""
    default_lanes, lane_w, _, _ = ROAD_CLASSES[cls]
    oneway = tags.get("oneway") in {"yes", "1", "true", "-1"} or tags.get("junction") == "roundabout"
    lanes_tag = _number(tags.get("lanes"))
    lanes = int(lanes_tag) if lanes_tag and 1 <= lanes_tag <= 8 else default_lanes
    if lanes_tag is None and oneway and cls not in {"motorway", "trunk"}:
        lanes = 1
    width = _number(tags.get("width"))
    measured = width is not None and MIN_WIDTH <= width <= MAX_WIDTH
    if not measured:
        width = lanes * lane_w
        if lanes == 1:
            width = max(width, 3.0)
    assert width is not None
    return float(min(MAX_WIDTH, max(MIN_WIDTH, width))), lanes, measured


def surface_of(tags: dict[str, str], cls: str) -> str:
    s = tags.get("surface", "")
    if s in {"cobblestone", "sett", "unhewn_cobblestone", "paving_stones"}:
        return "cobble"
    if s in {"concrete", "concrete:plates", "concrete:lanes"}:
        return "concrete"
    if s in UNPAVED or (cls == "track" and not s):
        return "gravel"
    return "asphalt"


@dataclass
class Edge:
    id: int
    a: int  # node index at the start
    b: int  # node index at the end
    xy: XY  # (n, 2) centreline from a to b, local metres
    cls: str
    width: float
    lanes: int
    oneway: bool
    bridge: bool
    tunnel: bool
    layer: int
    surface: str
    name: str
    maxspeed: float | None
    osm_ids: list[int] = field(default_factory=list)

    @property
    def length(self) -> float:
        return float(np.linalg.norm(np.diff(self.xy, axis=0), axis=1).sum())

    def signature(self) -> tuple[Any, ...]:
        return (
            self.cls,
            round(self.width, 2),
            self.lanes,
            self.oneway,
            self.bridge,
            self.tunnel,
            self.layer,
            self.surface,
        )


@dataclass
class RoadGraph:
    nodes: list[tuple[float, float]]
    edges: list[Edge]
    repairs: dict[str, int]

    def degree(self) -> list[int]:
        deg = [0] * len(self.nodes)
        for e in self.edges:
            deg[e.a] += 1
            deg[e.b] += 1
        return deg


def load_osm(paths: list[Path]) -> tuple[dict[int, tuple[float, float]], list[dict[str, Any]]]:
    """Merge Overpass cell files: node id -> (lon, lat), and unique ways with tags."""
    nodes: dict[int, tuple[float, float]] = {}
    ways: dict[int, dict[str, Any]] = {}
    for path in sorted(paths):
        for el in json.loads(path.read_text(encoding="utf-8")).get("elements", []):
            if el["type"] == "node":
                nodes[el["id"]] = (el["lon"], el["lat"])
            elif el["type"] == "way" and "tags" in el:
                ways[el["id"]] = el
    return nodes, [ways[k] for k in sorted(ways)]


def _key(x: float, y: float) -> tuple[int, int]:
    return (round(x * 100), round(y * 100))  # centimetre grid


def _dedupe(xy: XY) -> XY:
    keep = np.ones(len(xy), dtype=bool)
    keep[1:] = np.linalg.norm(np.diff(xy, axis=0), axis=1) > 0.05
    return xy[keep]


def _lines(geom: BaseGeometry) -> list[LineString]:
    if isinstance(geom, LineString):
        return [geom] if not geom.is_empty else []
    if isinstance(geom, MultiLineString):
        return [g for g in geom.geoms if not g.is_empty]
    return [g for sub in getattr(geom, "geoms", []) for g in _lines(sub)]


def build_graph(
    osm_nodes: dict[int, tuple[float, float]], ways: list[dict[str, Any]], georef: Georef, clip: BaseGeometry
) -> RoadGraph:
    repairs: dict[str, int] = defaultdict(int)
    # 1. keep drivable ways, project to local metres
    kept: list[tuple[dict[str, Any], str, list[int]]] = []
    use: dict[int, int] = defaultdict(int)
    for way in ways:
        cls = road_class(way["tags"])
        refs = [n for n in way.get("nodes", []) if n in osm_nodes]
        if cls is None or len(refs) < 2:
            continue
        kept.append((way, cls, refs))
        for i, n in enumerate(refs):
            use[n] += 2 if i in (0, len(refs) - 1) else 1  # ends always split
    local: dict[int, tuple[float, float]] = {}

    def loc(n: int) -> tuple[float, float]:
        if n not in local:
            local[n] = georef.to_local(*osm_nodes[n])
        return local[n]

    # 2. split ways at shared nodes, clip to the selection
    pieces: list[tuple[LineString, dict[str, Any], str]] = []
    for way, cls, refs in kept:
        start = 0
        for i in range(1, len(refs)):
            if use[refs[i]] > 1 or i == len(refs) - 1:
                line = LineString([loc(n) for n in refs[start : i + 1]])
                start = i
                if line.length < 0.05 or not line.intersects(clip):
                    continue
                for part in _lines(line.intersection(clip)):
                    if part.length >= 0.05:
                        pieces.append((part, way, cls))

    # 3. repair: roads that cross on the same level without a shared point get a junction
    def level(way: dict[str, Any]) -> tuple[int, bool, bool]:
        t = way["tags"]
        layer = int(_number(t.get("layer")) or 0) * (-1 if t.get("layer", "").strip().startswith("-") else 1)
        return (layer, t.get("bridge", "no") != "no", t.get("tunnel", "no") != "no")

    if not pieces:
        return RoadGraph([], [], {})
    geoms = [p[0] for p in pieces]
    tree = STRtree(geoms)
    cuts: dict[int, list[Point]] = defaultdict(list)
    for i, j in zip(*tree.query(geoms, predicate="crosses"), strict=True):
        if i >= j or level(pieces[i][1]) != level(pieces[j][1]):
            continue
        hit = geoms[i].intersection(geoms[j])
        for pt in (
            [hit]
            if isinstance(hit, Point)
            else [g for g in getattr(hit, "geoms", []) if isinstance(g, Point)]
        ):
            cuts[int(i)].append(pt)
            cuts[int(j)].append(pt)
            repairs["unconnected_crossings_joined"] += 1
    split: list[tuple[XY, dict[str, Any], str]] = []
    for idx, (line, way, cls) in enumerate(pieces):
        coords = np.asarray(line.coords, dtype=np.float64)[:, :2]
        if idx not in cuts:
            split.append((coords, way, cls))
            continue
        dists = sorted({round(line.project(pt), 3) for pt in cuts[idx]})
        bounds = [0.0, *[d for d in dists if 0.05 < d < line.length - 0.05], line.length]
        cum = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(coords, axis=0), axis=1))])
        for d0, d1 in zip(bounds[:-1], bounds[1:], strict=True):
            inner = coords[(cum > d0 + 0.01) & (cum < d1 - 0.01)]
            p0, p1 = line.interpolate(d0), line.interpolate(d1)
            split.append((np.vstack([[p0.x, p0.y], inner, [p1.x, p1.y]]), way, cls))

    # 4. nodes by rounded position, edges with properties
    node_ids: dict[tuple[int, int], int] = {}
    nodes: list[tuple[float, float]] = []

    def node(x: float, y: float) -> int:
        k = _key(x, y)
        if k not in node_ids:
            node_ids[k] = len(nodes)
            nodes.append((float(x), float(y)))
        return node_ids[k]

    edges: list[Edge] = []
    for coords, way, cls in split:
        xy = _dedupe(coords)
        if len(xy) < 2:
            continue
        tags = way["tags"]
        width, lanes, _ = road_width(tags, cls)
        layer, bridge, tunnel = level(way)
        a, b = node(*xy[0]), node(*xy[-1])
        xy[0], xy[-1] = nodes[a], nodes[b]
        speed = _number(tags.get("maxspeed"))
        edges.append(
            Edge(
                id=len(edges), a=a, b=b, xy=xy, cls=cls, width=width, lanes=lanes,
                oneway=tags.get("oneway") in {"yes", "1", "true"} or tags.get("junction") == "roundabout",
                bridge=bridge, tunnel=tunnel, layer=layer, surface=surface_of(tags, cls),
                name=tags.get("name", ""), maxspeed=speed, osm_ids=[way["id"]],
            )
        )  # fmt: skip
    graph = RoadGraph(nodes, edges, dict(repairs))
    _merge_chains(graph)
    _drop_stubs(graph)
    _compact(graph)
    return graph


def _merge_chains(g: RoadGraph) -> None:
    """Join edges that meet at a plain point (2 roads, same properties) into one edge."""
    changed = True
    while changed:
        changed = False
        at: dict[int, list[Edge]] = defaultdict(list)
        for e in g.edges:
            at[e.a].append(e)
            if e.b != e.a:
                at[e.b].append(e)
        gone: set[int] = set()
        for n, inc in at.items():
            if len(inc) != 2:
                continue
            e1, e2 = inc
            if e1.id in gone or e2.id in gone or e1 is e2 or e1.signature() != e2.signature():
                continue
            if e1.a == e1.b or e2.a == e2.b:
                continue
            if e1.oneway and not ((e1.b == n and e2.a == n) or (e2.b == n and e1.a == n)):
                continue  # one-way directions disagree; keep the node
            first, second = (e1, e2) if e1.b == n and (e2.a == n or not e1.oneway) else (e2, e1)
            xy1 = first.xy if first.b == n else first.xy[::-1]
            xy2 = second.xy if second.a == n else second.xy[::-1]
            first.a = first.a if first.b == n else first.b
            first.b = second.b if second.a == n else second.a
            first.xy = np.vstack([xy1, xy2[1:]])
            first.osm_ids = sorted(set(first.osm_ids + second.osm_ids))
            first.name = first.name or second.name
            gone.add(second.id)
            changed = True
        if gone:
            g.edges = [e for e in g.edges if e.id not in gone]


def _drop_stubs(g: RoadGraph) -> None:
    """Remove dead-end stubs too short to be a real road (usually clipping leftovers)."""
    while True:
        deg = g.degree()
        stubs = {e.id for e in g.edges if e.length < MIN_EDGE_M and (deg[e.a] == 1 or deg[e.b] == 1)}
        if not stubs:
            return
        g.repairs["tiny_stubs_removed"] = g.repairs.get("tiny_stubs_removed", 0) + len(stubs)
        g.edges = [e for e in g.edges if e.id not in stubs]


def _compact(g: RoadGraph) -> None:
    """Renumber nodes and edges in a stable order (needed for byte-identical output)."""
    g.edges.sort(
        key=lambda e: (min(e.osm_ids), round(float(e.xy[0, 0]), 2), round(float(e.xy[0, 1]), 2), len(e.xy))
    )
    used = sorted({n for e in g.edges for n in (e.a, e.b)}, key=lambda n: g.nodes[n])
    remap = {old: new for new, old in enumerate(used)}
    g.nodes = [g.nodes[n] for n in used]
    for i, e in enumerate(g.edges):
        e.id, e.a, e.b = i, remap[e.a], remap[e.b]
