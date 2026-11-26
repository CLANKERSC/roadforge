"""Stage 05b: smooth centrelines and drivable elevation profiles.

Rule: follow the real road as closely as the data allows, but never output something a car
can't drive. Every correction is counted so the build can report what it repaired.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray
from scipy.interpolate import PchipInterpolator
from scipy.ndimage import gaussian_filter1d
from shapely import STRtree
from shapely.geometry import LineString, Point

from forge_worker.roadnet import ROAD_CLASSES, Edge, RoadGraph
from forge_worker.terrain import Dtm

A = NDArray[np.float64]
STEP_M = 2.0  # spacing of centreline points
CROWN = 0.02  # cross slope from the centre to each edge
SHOULDER_M = 0.5
SHOULDER_SLOPE = 0.06
APRON_M = 1.2  # sloping strip outside the verge that dips into the terrain (hides any gap)
APRON_DROP = 0.7
LEVEL_GAP_M = 5.0  # a road passing over another one must clear it by this much
MAX_KINK = 0.06  # largest change of slope between two 2 m steps (a sharper one launches a car)


@dataclass
class Profile:
    """One edge resampled every ~2 m: position, distance along, height."""

    edge: Edge
    xy: A  # (n, 2)
    s: A  # (n,) distance from the start
    z: A  # (n,) centre height
    z_ground: A  # (n,) raw terrain height under the centreline, for the report

    @property
    def length(self) -> float:
        return float(self.s[-1])


def cumulative(xy: A) -> A:
    return np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(xy, axis=0), axis=1))])


def resample(xy: A, step: float = STEP_M) -> A:
    """Smooth curve through the map points (no overshoot), sampled at an even spacing."""
    s = cumulative(xy)
    total = float(s[-1])
    n = max(2, int(round(total / step)) + 1)
    t = np.linspace(0.0, total, n)
    if len(xy) < 3:
        out = np.column_stack([np.interp(t, s, xy[:, 0]), np.interp(t, s, xy[:, 1])])
    else:
        out = np.column_stack([PchipInterpolator(s, xy[:, 0])(t), PchipInterpolator(s, xy[:, 1])(t)])
    out[0], out[-1] = xy[0], xy[-1]  # junction points must stay exactly where they are
    return out


def turn_radius(xy: A) -> A:
    """Radius of the circle through each point and its neighbours (inf on straights)."""
    r = np.full(len(xy), np.inf)
    if len(xy) < 3:
        return r
    a, b, c = xy[:-2], xy[1:-1], xy[2:]
    ab, bc, ac = (np.linalg.norm(b - a, axis=1), np.linalg.norm(c - b, axis=1), np.linalg.norm(c - a, axis=1))
    cross = np.abs((b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0]))
    with np.errstate(divide="ignore", invalid="ignore"):
        r[1:-1] = np.where(cross > 1e-9, ab * bc * ac / (2.0 * cross), np.inf)
    return r


def curvature(xy: A) -> A:
    """Signed bend at each point in 1/m: positive turns left, negative right, 0 on straights."""
    k = np.zeros(len(xy))
    if len(xy) < 3:
        return k
    a, b, c = xy[:-2], xy[1:-1], xy[2:]
    ab, bc, ac = (np.linalg.norm(b - a, axis=1), np.linalg.norm(c - b, axis=1), np.linalg.norm(c - a, axis=1))
    cross = (b[:, 0] - a[:, 0]) * (c[:, 1] - b[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - b[:, 0])
    k[1:-1] = 2.0 * cross / np.maximum(ab * bc * ac, 1e-9)
    return k


def relax_tight_corners(xy: A, min_radius: float, max_iter: int = 200) -> tuple[A, int]:
    """Open up corners tighter than the road is wide, a little at a time.

    Only a gentle correction: it must not flatten a real hairpin. Whatever is still too tight
    afterwards is handled when the surface is built, by narrowing the inside of the bend.
    """
    xy = xy.copy()
    touched = np.zeros(len(xy), dtype=bool)
    for _ in range(max_iter):
        tight = turn_radius(xy) < min_radius
        tight[0] = tight[-1] = False
        if not tight.any():
            break
        touched |= tight
        avg = (xy[:-2] + xy[2:]) / 2.0
        inner = xy[1:-1]
        inner[tight[1:-1]] += 0.5 * (avg[tight[1:-1]] - inner[tight[1:-1]])
    return xy, int(touched.sum())


def limit_grade(z: A, s: A, max_grade: float) -> tuple[A, int]:
    """Clamp the slope between neighbours to max_grade, spreading the change both ways."""
    ds = np.diff(s)
    up = z.copy()
    for i in range(1, len(z)):
        up[i] = min(max(up[i], up[i - 1] - max_grade * ds[i - 1]), up[i - 1] + max_grade * ds[i - 1])
    down = z.copy()
    for i in range(len(z) - 2, -1, -1):
        down[i] = min(max(down[i], down[i + 1] - max_grade * ds[i]), down[i + 1] + max_grade * ds[i])
    out = (up + down) / 2.0
    # the average of two valid profiles is valid; count what changed noticeably
    return out, int((np.abs(out - z) > 0.05).sum())


def smooth_heights(z: A, sigma_m: float) -> A:
    if len(z) < 5:
        return z.copy()
    return np.asarray(gaussian_filter1d(z, sigma=sigma_m / STEP_M, mode="nearest"), dtype=np.float64)


def relax_kinks(z: A, s: A, max_kink: float = MAX_KINK * 0.8) -> tuple[A, int]:
    """Round off sudden changes of slope (they would launch a car). The two ends stay put."""
    if len(z) < 3:
        return z, 0
    z = z.copy()
    ds = np.maximum(np.diff(s), 1e-6)
    touched = np.zeros(len(z), dtype=bool)
    for _ in range(400):
        kink = np.abs(np.diff(np.diff(z) / ds))
        bad = np.zeros(len(z), dtype=bool)
        bad[1:-1] = kink > max_kink
        if not bad.any():
            break
        bad[1:-1] |= bad[:-2] | bad[2:]  # also move the neighbours, so the bend spreads out
        bad[0] = bad[-1] = False
        touched |= bad
        w0, w1 = ds[1:], ds[:-1]  # weights so unevenly spaced points land on the straight line
        target = (z[:-2] * w0 + z[2:] * w1) / (w0 + w1)
        z[1:-1] = np.where(bad[1:-1], 0.5 * z[1:-1] + 0.5 * target, z[1:-1])
    return z, int(touched.sum())


def _falloff(s: A, reach: float) -> A:
    """1 at s=0 easing to 0 at s>=reach (smoothstep)."""
    t = np.clip(s / max(reach, 1e-6), 0.0, 1.0)
    return np.asarray(1.0 - t * t * (3.0 - 2.0 * t), dtype=np.float64)


def _bump(s: A, centre: float, half: float, height: float) -> A:
    """Smooth hill of the given height centred at `centre`, zero from `half` metres away."""
    d = np.abs(s - centre)
    return np.asarray(np.where(d < half, height * (0.5 + 0.5 * np.cos(np.pi * d / max(half, 1e-6))), 0.0))


def _rank(e: Edge) -> tuple[int, int]:
    return (e.layer, int(e.bridge) - int(e.tunnel))


def separate_levels(profiles: list[Profile], repairs: dict[str, int]) -> None:
    """Where one road passes over another, make sure there is real headroom between them.

    Good terrain data already contains the embankments. Coarse data does not, and the two
    roads would cut through each other. The bridge is arched up as far as its slope limit
    allows and the lower road is dipped for the rest.
    """
    if len(profiles) < 2:
        return
    lines = [LineString(p.xy) for p in profiles]
    tree = STRtree(lines)
    pairs = sorted(
        {
            (int(min(i, j)), int(max(i, j)))
            for i, j in zip(*tree.query(lines, predicate="crosses"), strict=True)
        }
    )
    for i, j in pairs:
        if _rank(profiles[i].edge) == _rank(profiles[j].edge):
            continue
        up, low = (i, j) if _rank(profiles[i].edge) > _rank(profiles[j].edge) else (j, i)
        pu, pl = profiles[up], profiles[low]
        hit = lines[up].intersection(lines[low])
        points = (
            [hit]
            if isinstance(hit, Point)
            else [g for g in getattr(hit, "geoms", []) if isinstance(g, Point)]
        )
        for pt in points:
            su, sl = float(lines[up].project(pt)), float(lines[low].project(pt))
            need = LEVEL_GAP_M - (float(np.interp(su, pu.s, pu.z)) - float(np.interp(sl, pl.s, pl.z)))
            if need <= 0.01:
                continue
            if pu.edge.bridge:
                half = min(su, pu.length - su)
                rise = min(need, 0.8 * 2.0 * ROAD_CLASSES[pu.edge.cls][2] * half / math.pi)
                if rise > 0.01:
                    pu.z = pu.z + _bump(pu.s, su, half, rise)
                    need -= rise
            if need > 0.01:
                half = min(sl, pl.length - sl, max(60.0, need * 12.0))
                dip = min(need, 0.8 * 2.0 * ROAD_CLASSES[pl.edge.cls][2] * half / math.pi)
                if dip > 0.01:
                    pl.z = pl.z - _bump(pl.s, sl, half, dip)
                    need -= dip
            repairs["overpasses_given_headroom"] += 1
            if need > 0.25:
                repairs["overpasses_with_low_headroom"] += 1


def build_profiles(graph: RoadGraph, dtm: Dtm, source_res: float) -> tuple[list[Profile], A, dict[str, int]]:
    """Centrelines + heights for every edge, and one agreed height per junction node."""
    repairs: dict[str, int] = defaultdict(int)
    # noisy LiDAR needs a little smoothing, a coarse 30 m model needs a lot
    sigma = 6.0 if source_res <= 5 else max(20.0, source_res * 0.8)
    profiles: list[Profile] = []
    for e in graph.edges:
        xy = resample(e.xy)
        xy, fixed = relax_tight_corners(xy, min_radius=e.width / 2 + SHOULDER_M + 0.25)
        repairs["tight_corner_points_opened"] += fixed
        s = cumulative(xy)
        ground = dtm.sample(xy[:, 0], xy[:, 1])
        if e.bridge or e.tunnel:
            z = np.interp(s, [0.0, s[-1]], [ground[0], ground[-1]])  # deck: straight between the ends
        else:
            z = smooth_heights(ground, sigma)
        profiles.append(Profile(edge=e, xy=xy, s=s, z=z, z_ground=ground))
    separate_levels(profiles, repairs)

    # every road meeting at a junction must arrive at the same height
    total = np.zeros(len(graph.nodes))
    weight = np.zeros(len(graph.nodes))
    for p in profiles:
        w = p.edge.width
        total[p.edge.a] += p.z[0] * w
        total[p.edge.b] += p.z[-1] * w
        weight[p.edge.a] += w
        weight[p.edge.b] += w
    node_z = total / np.maximum(weight, 1e-9)

    degree = np.zeros(len(graph.nodes), dtype=int)
    for p in profiles:
        degree[p.edge.a] += 1
        degree[p.edge.b] += 1

    def dead_end(e: Edge) -> bool:
        return e.a != e.b and (degree[e.a] == 1) != (degree[e.b] == 1)

    # junction heights must also be reachable: two junctions 10 m apart can't differ by 5 m
    for _ in range(200):
        moved = 0
        for p in profiles:
            e = p.edge
            if e.a == e.b or dead_end(e):
                continue
            allowed = ROAD_CLASSES[e.cls][2] * p.length * 0.9
            excess = abs(node_z[e.b] - node_z[e.a]) - allowed
            if excess > 0.01:
                shift = 0.5 * excess * np.sign(node_z[e.b] - node_z[e.a])
                node_z[e.a] += shift
                node_z[e.b] -= shift
                moved += 1
        if moved == 0:
            break
        repairs["junction_heights_evened"] = max(repairs["junction_heights_evened"], moved)

    # a dead end has no other road to agree with: it follows the junction it hangs off, and
    # its free end lands wherever a drivable slope takes it
    for p in profiles:
        e = p.edge
        if not dead_end(e):
            continue
        near, far, k_near, k_far = (e.a, e.b, 0, -1) if degree[e.a] > 1 else (e.b, e.a, -1, 0)
        limited, changed = limit_grade(p.z, p.s, ROAD_CLASSES[e.cls][2])
        repairs["grade_limited_points"] += changed
        p.z = limited + (node_z[near] - limited[k_near])
        node_z[far] = p.z[k_far]

    for p in profiles:
        e = p.edge
        max_grade = ROAD_CLASSES[e.cls][2]
        reach = min(40.0, p.length)

        def pin(z: A, p: Profile = p, e: Edge = e, reach: float = reach) -> A:
            d0, d1 = node_z[e.a] - z[0], node_z[e.b] - z[-1]
            return np.asarray(z + d0 * _falloff(p.s, reach) + d1 * _falloff(p.s[-1] - p.s, reach))

        for _ in range(4):
            limited, changed = limit_grade(pin(p.z), p.s, max_grade)
            p.z = limited
            repairs["grade_limited_points"] += changed
            if changed == 0:
                break
        p.z = pin(p.z)  # ends meet the junction height exactly, eased in smoothly
        p.z, eased = relax_kinks(p.z, p.s)
        repairs["height_kinks_eased"] += eased
        if not (e.bridge or e.tunnel):
            repairs["bumpy_metres_smoothed"] += int(STEP_M * (np.abs(p.z - p.z_ground) > 0.5).sum())
    return profiles, node_z, dict(repairs)


def cross_section_offsets(width: float) -> A:
    """9 points across the road, left to right: apron, verge, edge, lane, crown, lane, edge, verge, apron."""
    hw = width / 2.0
    v = hw + SHOULDER_M
    return np.array([-(v + APRON_M), -v, -hw, -hw / 2.0, 0.0, hw / 2.0, hw, v, v + APRON_M])


def cross_section_drop(width: float) -> A:
    """How far below the crown each cross-section point sits."""
    edge = CROWN * width / 2.0
    v = edge + SHOULDER_SLOPE * SHOULDER_M
    return np.array([v + APRON_DROP, v, edge, edge / 2.0, 0.0, edge / 2.0, edge, v, v + APRON_DROP])


def normals(xy: A) -> A:
    """Unit vector pointing to the left of the direction of travel at each point."""
    d = np.gradient(xy, axis=0)
    length = np.linalg.norm(d, axis=1, keepdims=True)
    d = d / np.maximum(length, 1e-9)
    return np.column_stack([-d[:, 1], d[:, 0]])


def roads_to_json(profiles: list[Profile], node_xy: list[tuple[float, float]], node_z: A) -> dict[str, Any]:
    """Everything later stages need, rounded to 0.1 mm so re-reading gives identical results."""

    def r(a: Any) -> Any:
        return np.round(np.asarray(a, dtype=np.float64), 4).tolist()

    return {
        "version": 1,
        "nodes": [[*r(xy), float(round(float(z), 4))] for xy, z in zip(node_xy, node_z, strict=True)],
        "edges": [
            {
                "id": p.edge.id,
                "a": p.edge.a,
                "b": p.edge.b,
                "cls": p.edge.cls,
                "width": p.edge.width,
                "lanes": p.edge.lanes,
                "oneway": p.edge.oneway,
                "bridge": p.edge.bridge,
                "tunnel": p.edge.tunnel,
                "layer": p.edge.layer,
                "surface": p.edge.surface,
                "name": p.edge.name,
                "maxspeed": p.edge.maxspeed,
                "osm_ids": p.edge.osm_ids,
                "xy": r(p.xy),
                "z": r(p.z),
                "z_ground": r(p.z_ground),
            }
            for p in profiles
        ],  # fmt: skip
    }


def roads_from_json(data: dict[str, Any]) -> tuple[list[Profile], list[tuple[float, float]], A]:
    node_xy = [(float(n[0]), float(n[1])) for n in data["nodes"]]
    node_z = np.array([float(n[2]) for n in data["nodes"]], dtype=np.float64)
    profiles = []
    for d in data["edges"]:
        xy = np.asarray(d["xy"], dtype=np.float64).reshape(-1, 2)
        edge = Edge(
            id=d["id"], a=d["a"], b=d["b"], xy=xy, cls=d["cls"], width=d["width"], lanes=d["lanes"],
            oneway=d["oneway"], bridge=d["bridge"], tunnel=d["tunnel"], layer=d["layer"],
            surface=d["surface"], name=d["name"], maxspeed=d["maxspeed"], osm_ids=list(d["osm_ids"]),
        )  # fmt: skip
        z = np.asarray(d["z"], dtype=np.float64)
        profiles.append(Profile(edge, xy, cumulative(xy), z, np.asarray(d["z_ground"], dtype=np.float64)))
    return profiles, node_xy, node_z
