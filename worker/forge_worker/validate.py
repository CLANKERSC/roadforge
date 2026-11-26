"""Stage 09: the drivability check. Every build ends here.

It measures the finished road and terrain the way a car would meet them and writes a plain
report: what was checked, what the numbers are, and what was repaired on the way.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np
from numpy.typing import NDArray

from forge_worker.conform import face_masks, sample_triangles
from forge_worker.roadgeom import MAX_KINK, Profile
from forge_worker.roadmesh import RoadMesh, open_seams
from forge_worker.roadnet import ROAD_CLASSES
from forge_worker.scene import MAX_VERTS
from forge_worker.terrain import Dtm

A = NDArray[np.float64]
MIN_CLEARANCE_M = 0.02  # terrain must stay at least this far below the asphalt
GRADE_TOLERANCE = 1.08  # measured slope may pass the class limit by this factor (rounding)

# repair counters -> sentence for the report
REPAIR_TEXT = {
    "unconnected_crossings_joined": "Crossings that were not connected on the map, turned into junctions",
    "tiny_stubs_removed": "Tiny dead-end stubs removed",
    "tight_corner_points_opened": "Points in corners too tight to drive, opened up",
    "junction_heights_evened": "Junctions whose heights were evened out so no road arrives too steep",
    "grade_limited_points": "Points lowered or raised to keep the slope drivable",
    "bumpy_metres_smoothed": "Metres of road smoothed where the height data was bumpy",
    "height_kinks_eased": "Points eased where the road height changed too suddenly",
    "junction_slivers_dropped": "Overlapping slivers removed where roads meet at a very flat angle",
    "overpasses_given_headroom": "Places where one road passes over another, given headroom",
    "overpasses_with_low_headroom": "Overpasses still under 5 m headroom (roads too short to ramp)",
    "terrain_cells_filled": "Missing height samples filled in",
    "terrain_spikes_removed": "Single-point spikes removed from the height data",
    "terrain_extra_passes": "Extra passes needed to press the terrain under the road",
}


def clearance(dtm: Dtm, mesh: RoadMesh, bridge_edges: set[int]) -> tuple[float, int, float]:
    """(smallest gap road-above-terrain on normal roads, points below the limit, same for bridge decks)."""
    ground_faces, bridge_faces = face_masks(mesh, bridge_edges)
    pts = sample_triangles(mesh.vertices, mesh.faces[ground_faces], spacing=1.0)
    gap = pts[:, 2] - dtm.sample_surface(pts[:, 0], pts[:, 1]) if len(pts) else np.array([1.0])
    deck = sample_triangles(mesh.vertices, mesh.faces[bridge_faces], spacing=1.0)
    deck_gap = deck[:, 2] - dtm.sample_surface(deck[:, 0], deck[:, 1]) if len(deck) else np.array([1.0])
    return float(np.min(gap)), int((gap < MIN_CLEARANCE_M).sum()), float(np.min(deck_gap))


def upside_down_faces(mesh: RoadMesh) -> int:
    """Drivable triangles that face down or are folded over (a wheel would catch on them)."""
    solid = np.asarray(mesh.face_surface) != "apron"
    tri = mesh.vertices[mesh.faces[solid]]
    u, v = tri[:, 1, :2] - tri[:, 0, :2], tri[:, 2, :2] - tri[:, 0, :2]
    normal_z = u[:, 0] * v[:, 1] - u[:, 1] * v[:, 0]
    return int((normal_z < -1e-6).sum())


def _components(profiles: list[Profile], n_nodes: int) -> int:
    parent = list(range(n_nodes))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for p in profiles:
        parent[find(p.edge.a)] = find(p.edge.b)
    return len({find(n) for p in profiles for n in (p.edge.a, p.edge.b)})


def check_drivability(
    profiles: list[Profile],
    node_z: A,
    mesh: RoadMesh,
    dtm: Dtm,
    mesh_summaries: list[dict[str, Any]],
    repairs: dict[str, int],
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def add(check_id: str, label: str, ok: bool, value: Any, limit: Any, unit: str = "") -> None:
        checks.append(
            {"id": check_id, "label": label, "ok": bool(ok), "value": value, "limit": limit, "unit": unit}
        )

    seams = open_seams(mesh)
    add("seams", "Road pieces are welded together at every junction", seams == 0, seams, 0, "gaps")

    flipped = upside_down_faces(mesh)
    add("folds", "No folded or upside-down road surface", flipped == 0, flipped, 0, "triangles")

    bridges = {p.edge.id for p in profiles if p.edge.bridge}
    low, bad, deck_low = clearance(dtm, mesh, bridges)
    add(
        "terrain_under_road",
        "Terrain never pokes through the road",
        bad == 0,
        round(low, 3),
        MIN_CLEARANCE_M,
        "m",
    )
    add(
        "terrain_under_bridges",
        "Ground stays below every bridge deck",
        deck_low > 0.0,
        round(deck_low, 3),
        0.0,
        "m",
    )

    worst_grade, worst_ratio, worst_kink, end_gap = 0.0, 0.0, 0.0, 0.0
    by_class: dict[str, float] = defaultdict(float)
    for p in profiles:
        if len(p.s) < 2:
            continue
        grade = np.diff(p.z) / np.maximum(np.diff(p.s), 1e-6)
        g = float(np.abs(grade).max())
        by_class[p.edge.cls] = max(by_class[p.edge.cls], g)
        worst_grade = max(worst_grade, g)
        worst_ratio = max(worst_ratio, g / ROAD_CLASSES[p.edge.cls][2])
        if len(grade) > 1:
            worst_kink = max(worst_kink, float(np.abs(np.diff(grade)).max()))
        end_gap = max(end_gap, abs(p.z[0] - node_z[p.edge.a]), abs(p.z[-1] - node_z[p.edge.b]))
    add("slope", "No road is steeper than its type allows", worst_ratio <= GRADE_TOLERANCE,
        round(worst_grade * 100, 1), "per road type", "%")  # fmt: skip
    add("kinks", "No sharp kink in the road height", worst_kink <= MAX_KINK, round(worst_kink * 100, 1),
        MAX_KINK * 100, "% per 2 m")  # fmt: skip
    add("junction_heights", "Roads meet at the same height at junctions", end_gap < 0.002,
        round(end_gap * 1000, 2), 2.0, "mm")  # fmt: skip

    biggest = max((m["vertices"] for m in mesh_summaries), default=0)
    add(
        "mesh_size",
        "Every mesh is small enough for Assetto Corsa",
        biggest <= MAX_VERTS,
        biggest,
        MAX_VERTS,
        "points",
    )

    parts = _components(profiles, len(node_z))
    done = [
        {"id": key, "count": int(count), "text": f"{REPAIR_TEXT.get(key, key)}: {int(count)}"}
        for key, count in sorted(repairs.items())
        if count
    ]
    notes: list[str] = []
    if parts > 1:
        notes.append(f"The roads form {parts} separate networks that are not connected inside the selection.")
    if repairs.get("overpasses_with_low_headroom"):
        notes.append("Some overpasses have less than 5 m headroom.")
    return {
        "version": 1,
        "ok": all(c["ok"] for c in checks),
        "checks": checks,
        "repairs": done,
        "notes": notes,
        "summary": {
            "road_km": round(sum(p.length for p in profiles) / 1000.0, 3),
            "roads": len(profiles),
            "junctions": int(mesh.stats.get("junctions", 0)),
            "bridges": int(mesh.stats.get("bridges", 0)),
            "steepest_percent_by_type": {k: round(v * 100, 1) for k, v in sorted(by_class.items())},
            "separate_networks": parts,
        },
    }
