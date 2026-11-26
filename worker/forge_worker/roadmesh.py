"""Stage 05c/08: road surface mesh. Ribbons along each edge, fan polygons at junctions.

All drivable triangles share one vertex list, so ribbons and junctions are welded: there
is no gap a wheel could fall into. Coordinates are local metres, z up.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray

from forge_worker.roadgeom import (
    APRON_DROP,
    APRON_M,
    CROWN,
    SHOULDER_M,
    Profile,
    cross_section_drop,
    cross_section_offsets,
    normals,
)

A = NDArray[np.float64]
I = NDArray[np.int64]  # noqa: E741
MARK_LIFT = 0.02  # markings float this far above the asphalt
LINE_W = 0.12
BRIDGE_WALL_H = 1.0


@dataclass
class Soup:
    """Triangles of one kind (surface) with their own vertices."""

    surface: str
    vertices: list[tuple[float, float, float]] = field(default_factory=list)
    uvs: list[tuple[float, float]] = field(default_factory=list)
    faces: list[tuple[int, int, int]] = field(default_factory=list)

    def add_quad_strip(self, left: A, right: A, v: A, u0: float, u1: float) -> None:
        """A strip between two 3D polylines of equal length."""
        base = len(self.vertices)
        for i in range(len(left)):
            self.vertices.append((float(left[i, 0]), float(left[i, 1]), float(left[i, 2])))
            self.vertices.append((float(right[i, 0]), float(right[i, 1]), float(right[i, 2])))
            self.uvs.append((u0, float(v[i])))
            self.uvs.append((u1, float(v[i])))
        for i in range(len(left) - 1):
            a, b, c, d = base + 2 * i, base + 2 * i + 1, base + 2 * i + 2, base + 2 * i + 3
            self.faces.append((a, b, d))
            self.faces.append((a, d, c))


@dataclass
class RoadMesh:
    """Welded drivable mesh plus loose extras (markings, bridge walls)."""

    vertices: A  # (n, 3)
    uvs: A  # (n, 2) metres
    faces: I  # (m, 3)
    face_surface: list[str]  # per face: asphalt / concrete / cobble / gravel / shoulder / apron
    face_edge: I  # per face: edge id it belongs to, or -1 for junctions
    extras: list[Soup]
    ribbon_ends: list[tuple[int, int]]  # welded seams: (vertex index left, vertex index right)
    stats: dict[str, int]


@dataclass
class _Arm:
    profile: Profile
    at_start: bool
    angle: float
    half: float  # half width including the verge
    trim: float = 0.0
    row: list[int] = field(default_factory=list)  # vertex ids, left to right in travel direction

    def outward(self) -> list[int]:
        """The end row from the right side to the left side, looking away from the junction."""
        return self.row[::-1] if self.at_start else self.row


def _point_at(p: Profile, s: float) -> tuple[A, float, A]:
    """Position, height and left-normal at distance s along the profile."""
    xy = np.array([np.interp(s, p.s, p.xy[:, 0]), np.interp(s, p.s, p.xy[:, 1])])
    z = float(np.interp(s, p.s, p.z))
    n = normals(p.xy)
    nrm = np.array([np.interp(s, p.s, n[:, 0]), np.interp(s, p.s, n[:, 1])])
    return xy, z, nrm / max(float(np.linalg.norm(nrm)), 1e-9)


def _trim_lengths(arms: list[_Arm]) -> None:
    """How far each road is cut back from the junction point so the ribbons don't overlap."""
    n = len(arms)
    for i, arm in enumerate(arms):
        need = 2.0 if n == 2 else 1.5
        for other in (arms[(i - 1) % n], arms[(i + 1) % n]):
            if other is arm:
                continue
            theta = abs((arm.angle - other.angle + math.pi) % (2 * math.pi) - math.pi)
            theta = min(max(theta, math.radians(25)), math.radians(155))
            need = max(need, (other.half + arm.half * math.cos(theta)) / math.sin(theta) + 0.5)
        arm.trim = min(need, 25.0, 0.45 * arm.profile.length)


def build_road_mesh(profiles: list[Profile], node_xy: list[tuple[float, float]], node_z: A) -> RoadMesh:
    verts: list[tuple[float, float, float]] = []
    uvs: list[tuple[float, float]] = []
    faces: list[tuple[int, int, int]] = []
    face_surface: list[str] = []
    face_edge: list[int] = []
    markings = Soup("marking")
    walls = Soup("wall")
    ribbon_ends: list[tuple[int, int]] = []
    stats: dict[str, int] = defaultdict(int)

    # arms per node, sorted counter-clockwise
    at_node: dict[int, list[_Arm]] = defaultdict(list)
    for p in profiles:
        if p.length < 0.5:
            continue
        d0 = p.xy[min(2, len(p.xy) - 1)] - p.xy[0]
        d1 = p.xy[max(-3, -len(p.xy))] - p.xy[-1]
        half = p.edge.width / 2 + SHOULDER_M
        at_node[p.edge.a].append(_Arm(p, True, math.atan2(d0[1], d0[0]), half))
        at_node[p.edge.b].append(_Arm(p, False, math.atan2(d1[1], d1[0]), half))
    arm_of: dict[tuple[int, bool], _Arm] = {}
    for arms in at_node.values():
        arms.sort(key=lambda a: (a.angle, a.profile.edge.id, a.at_start))
        if len(arms) >= 2:
            _trim_lengths(arms)
        for arm in arms:
            arm_of[(arm.profile.edge.id, arm.at_start)] = arm

    # ribbons
    for p in profiles:
        if p.length < 0.5:
            continue
        e = p.edge
        start, end = arm_of[(e.id, True)], arm_of[(e.id, False)]
        s0, s1 = start.trim, p.length - end.trim
        inner = p.s[(p.s > s0 + 0.3) & (p.s < s1 - 0.3)]
        stations = np.concatenate([[s0], inner, [s1]])
        offs, drop = cross_section_offsets(e.width), cross_section_drop(e.width)
        rows: list[list[int]] = []
        centre = np.zeros((len(stations), 3))
        nrm_all = np.zeros((len(stations), 2))
        for k, s in enumerate(stations):
            xy, z, nrm = _point_at(p, float(s))
            centre[k] = (xy[0], xy[1], z)
            nrm_all[k] = nrm
            row = []
            for off, dz in zip(offs, drop, strict=True):
                row.append(len(verts))
                # nrm points left, offsets run from negative to positive: subtract to start on the left
                verts.append((float(xy[0] - nrm[0] * off), float(xy[1] - nrm[1] * off), z - float(dz)))
                uvs.append((float(off - offs[0]), float(s)))
            rows.append(row)
        for r0, r1 in zip(rows[:-1], rows[1:], strict=True):
            for c in range(8):
                surf = "apron" if c in (0, 7) else "shoulder" if c in (1, 6) else e.surface
                # left->right columns, forward rows: counter-clockwise seen from above
                faces.append((r0[c], r0[c + 1], r1[c + 1]))
                faces.append((r0[c], r1[c + 1], r1[c]))
                face_surface += [surf, surf]
                face_edge += [e.id, e.id]
        start.row, end.row = rows[0], rows[-1]
        if start.trim > 0:
            ribbon_ends.append((rows[0][1], rows[0][-2]))
        if end.trim > 0:
            ribbon_ends.append((rows[-1][1], rows[-1][-2]))
        stats["ribbon_metres"] += int(s1 - s0)
        _add_markings(markings, e, stations, centre, nrm_all)
        if e.bridge:
            _add_bridge_walls(walls, e.width, stations, centre, nrm_all)
            stats["bridges"] += 1

    # junction fans
    for n, arms in sorted(at_node.items()):
        if len(arms) < 2:
            continue
        boundary: list[int] = []
        inner_rows = [arm.outward()[1:-1] for arm in arms]
        for inner_row in inner_rows:
            boundary += inner_row
        centre_id = len(verts)
        verts.append((node_xy[n][0], node_xy[n][1], float(node_z[n])))
        uvs.append((0.0, 0.0))
        row_of = {v: (k, i) for k, inner in enumerate(inner_rows) for i, v in enumerate(inner)}
        for i in range(len(boundary)):
            a, b = boundary[i], boundary[(i + 1) % len(boundary)]
            if a == b:
                continue
            pa, pb, pc = np.asarray(verts[a][:2]), np.asarray(verts[b][:2]), np.asarray(node_xy[n])
            if (pa[0] - pc[0]) * (pb[1] - pc[1]) - (pa[1] - pc[1]) * (pb[0] - pc[0]) <= 1e-6:
                # two roads leave at such a flat angle that their ends overlap: the overlap is
                # already paved by both roads, a triangle here would only fold over
                stats["junction_slivers_dropped"] += 1
                continue
            faces.append((centre_id, a, b))
            (ka, ia), (kb, ib) = row_of[a], row_of[b]
            verge = ka == kb and {ia, ib} in ({0, 1}, {5, 6})
            face_surface.append("shoulder" if verge else arms[0].profile.edge.surface)
            face_edge.append(-1)
        # the sloping apron continues around each corner between two neighbouring roads
        for k, arm in enumerate(arms):
            nxt = arms[(k + 1) % len(arms)]
            if nxt is arm:
                continue
            l_in, l_ap = arm.outward()[-2], arm.outward()[-1]
            r_in, r_ap = nxt.outward()[1], nxt.outward()[0]
            mid = (np.asarray(verts[l_in]) + np.asarray(verts[r_in])) / 2.0
            out = mid[:2] - np.asarray(node_xy[n])
            out = out / max(float(np.linalg.norm(out)), 1e-6) * APRON_M
            corner = len(verts)
            verts.append((float(mid[0] + out[0]), float(mid[1] + out[1]), float(mid[2]) - APRON_DROP))
            uvs.append((0.0, 0.0))
            for tri in ((r_in, l_in, corner), (l_in, l_ap, corner), (r_ap, r_in, corner)):
                p0, p1, p2 = (np.asarray(verts[t][:2]) for t in tri)
                turn = (p1[0] - p0[0]) * (p2[1] - p0[1]) - (p1[1] - p0[1]) * (p2[0] - p0[0])
                if turn > 1e-6:  # skip slivers that would fold over where two aprons overlap
                    faces.append(tri)
                    face_surface.append("apron")
                    face_edge.append(-1)
        stats["junctions"] += 1

    return RoadMesh(
        vertices=np.asarray(verts, dtype=np.float64).reshape(-1, 3),
        uvs=np.asarray(uvs, dtype=np.float64).reshape(-1, 2),
        faces=np.asarray(faces, dtype=np.int64).reshape(-1, 3),
        face_surface=face_surface,
        face_edge=np.asarray(face_edge, dtype=np.int64),
        extras=[s for s in (markings, walls) if s.faces],
        ribbon_ends=ribbon_ends,
        stats=dict(stats),
    )


def _offset_line(centre: A, nrm: A, off: float, lift: float) -> A:
    out = centre.copy()
    out[:, 0] += nrm[:, 0] * off
    out[:, 1] += nrm[:, 1] * off
    out[:, 2] += lift - CROWN * abs(off)
    return out


def _add_markings(soup: Soup, e, stations: A, centre: A, nrm: A) -> None:  # type: ignore[no-untyped-def]
    if e.surface != "asphalt" or e.cls in {"track", "service", "living_street"} or len(stations) < 2:
        return
    hw = e.width / 2
    if e.cls in {"motorway", "trunk", "primary", "secondary", "tertiary"} and e.width >= 5.5:
        for side in (-1.0, 1.0):
            off = side * (hw - 0.25)
            soup.add_quad_strip(
                _offset_line(centre, nrm, off + LINE_W / 2, MARK_LIFT),
                _offset_line(centre, nrm, off - LINE_W / 2, MARK_LIFT),
                stations, 0.0, 1.0,
            )  # fmt: skip
    if e.oneway or e.width < 5.5:
        return
    # dashed centre line: 3 m paint, 6 m gap, phase tied to distance so it is stable
    left = _offset_line(centre, nrm, LINE_W / 2, MARK_LIFT)
    right = _offset_line(centre, nrm, -LINE_W / 2, MARK_LIFT)
    dash, period = 3.0, 9.0
    k = math.ceil(stations[0] / period)
    while k * period + dash <= stations[-1]:
        a, b = k * period, k * period + dash
        inside = stations[(stations > a) & (stations < b)]
        sv = np.concatenate([[a], inside, [b]])
        seg_l = np.column_stack([np.interp(sv, stations, left[:, i]) for i in range(3)])
        seg_r = np.column_stack([np.interp(sv, stations, right[:, i]) for i in range(3)])
        soup.add_quad_strip(seg_l, seg_r, sv, 0.0, 1.0)
        k += 1


def _add_bridge_walls(soup: Soup, width: float, stations: A, centre: A, nrm: A) -> None:
    """Low walls along a bridge so a car can't drop off the side."""
    off = width / 2 + SHOULDER_M
    for side in (-1.0, 1.0):
        foot = _offset_line(centre, nrm, side * off, -0.3)
        top = foot.copy()
        top[:, 2] += BRIDGE_WALL_H + 0.3
        soup.add_quad_strip(foot, top, stations, 0.0, 1.0)
        soup.add_quad_strip(top, foot, stations, 0.0, 1.0)  # visible from both sides


def open_seams(mesh: RoadMesh) -> int:
    """Count ribbon-end edges that are not shared with a junction (should be 0)."""
    count: dict[tuple[int, int], int] = defaultdict(int)
    for a, b, c in mesh.faces.tolist():
        for u, v in ((a, b), (b, c), (c, a)):
            count[(min(u, v), max(u, v))] += 1
    # collect the boundary edges that touch a ribbon end row vertex pair chain
    boundary = {k for k, v in count.items() if v == 1}
    end_vertices: set[int] = set()
    for left, right in mesh.ribbon_ends:
        end_vertices.update(range(min(left, right), max(left, right) + 1))
    return sum(1 for u, v in boundary if u in end_vertices and v in end_vertices and abs(u - v) == 1)
