"""Stages 03-06: terrain, roads, the 3D scene and the drivability check.

Everything here is engine-neutral. The game exporters (later milestones) read roads.json,
dtm_conformed.tif and scene.json and never go back to the raw downloads.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from forge_worker.conform import conform_terrain, road_height_raster
from forge_worker.geo import Georef
from forge_worker.pipeline import StageContext
from forge_worker.roadgeom import Profile, build_profiles, roads_from_json, roads_to_json
from forge_worker.roadmesh import RoadMesh, build_road_mesh
from forge_worker.roadnet import build_graph, load_osm
from forge_worker.scene import (
    iter_terrain_meshes,
    mesh_summary,
    preview_terrain,
    road_meshes,
    scene_manifest,
    write_glb,
    write_scene_json,
)
from forge_worker.selection import Selection
from forge_worker.stages.common import (
    hash_of,
    mark_stage,
    read_json,
    selection_from_params,
    stage_inputs,
    stage_is_fresh,
    write_json,
)
from forge_worker.terrain import DTM_RES, Dtm, build_dtm
from forge_worker.validate import check_drivability, clearance

CODE_VERSION = 2  # bump when a stage's output changes, so old cached stages are rebuilt
MAX_DTM_CELLS = 30_000_000
DTM_STEPS = (DTM_RES, 3.0, 4.0, 6.0, 8.0)


class BuildError(RuntimeError):
    """The build can't continue; the message is shown to the user as-is."""


def pick_resolution(bounds: tuple[float, float, float, float]) -> float:
    """2 m cells where they fit in memory, coarser cells for very large selections."""
    w, h = bounds[2] - bounds[0], bounds[3] - bounds[1]
    for res in DTM_STEPS:
        if (w / res) * (h / res) <= MAX_DTM_CELLS:
            return res
    raise BuildError(
        f"This selection spans {w / 1000:.0f} x {h / 1000:.0f} km, which is more terrain than this version "
        "can hold in memory. Pick a shorter road, or split it into two maps."
    )


# -- 03 terrain ----------------------------------------------------------------
def run_terrain(ctx: StageContext) -> None:
    elevation = read_json(ctx.workdir / "fetch_manifest.json")["layers"]["elevation"]
    sel = selection_from_params(ctx.params)
    bounds = sel.skirt_local.bounds
    inputs = hash_of(
        {
            "v": CODE_VERSION,
            "source": elevation["source"],
            "files": [[f["key"], f["sha256"]] for f in elevation["files"]],
            "georef": sel.georef.to_dict(),
            "bounds": [round(b, 3) for b in bounds],
        }
    )
    outputs = ["dtm.tif", "terrain.json"]
    if stage_is_fresh(ctx.workdir, "03_terrain", inputs, outputs):
        ctx.log("Terrain unchanged, reusing it")
        ctx.report(1.0, "Terrain ready")
        return
    res = pick_resolution(bounds)
    ctx.report(0.05, "Joining the height tiles")
    dtm, stats = build_dtm(
        elevation["files"], ctx.env.cache.root, sel.georef, bounds, float(elevation["resolution_m"]), res
    )
    ctx.check_cancelled()
    ctx.report(0.8, "Saving the terrain")
    dtm.write(ctx.workdir / "dtm.tif")
    write_json(ctx.workdir / "terrain.json", {**stats, "source": elevation["source"]})
    mark_stage(ctx.workdir, "03_terrain", inputs, outputs)
    ctx.log(
        f"Terrain: {stats['cols']} x {stats['rows']} cells of {res:g} m, heights "
        f"{stats['min_m']:.0f} to {stats['max_m']:.0f} m, from {elevation['name']}"
    )
    if res > DTM_RES:
        ctx.log(f"Large selection: terrain uses {res:g} m cells instead of {DTM_RES:g} m", "warning")
    if stats["gap_cells_filled"] or stats["spike_cells_fixed"]:
        ctx.log(
            f"Repaired the height data: {stats['gap_cells_filled']} missing samples filled, "
            f"{stats['spike_cells_fixed']} spikes removed"
        )
    ctx.report(1.0, "Terrain ready")


# -- 04 roads ------------------------------------------------------------------
def run_roads(ctx: StageContext) -> None:
    manifest = read_json(ctx.workdir / "fetch_manifest.json")
    vectors = manifest["layers"]["vectors"]
    inputs = hash_of(
        {
            "v": CODE_VERSION,
            "terrain": stage_inputs(ctx.workdir, "03_terrain"),
            "files": [[f["key"], f["sha256"]] for f in vectors["files"]],
        }
    )
    outputs = ["roads.json", "dtm_conformed.tif"]
    if stage_is_fresh(ctx.workdir, "04_roads", inputs, outputs):
        ctx.log("Roads unchanged, reusing them")
        ctx.report(1.0, "Roads ready")
        return
    sel = selection_from_params(ctx.params)
    terrain_stats = read_json(ctx.workdir / "terrain.json")
    ctx.report(0.05, "Reading the road map")
    osm_nodes, ways = load_osm([ctx.env.cache.root / f["path"] for f in vectors["files"]])
    graph = build_graph(osm_nodes, ways, sel.georef, sel.core_local)
    if not graph.edges:
        raise BuildError(
            "There are no drivable roads inside this selection. Move or enlarge it so it covers a road."
        )
    ctx.log(f"Roads: {len(graph.edges)} stretches, {sum(e.length for e in graph.edges) / 1000:.1f} km")
    ctx.check_cancelled()
    ctx.report(0.25, "Fitting the roads to the hills")
    dtm = Dtm.read(ctx.workdir / "dtm.tif")
    profiles, node_z, repairs = build_profiles(graph, dtm, float(terrain_stats["source_res_m"]))
    ctx.check_cancelled()
    ctx.report(0.5, "Shaping the terrain around the roads")
    mesh = build_road_mesh(profiles, graph.nodes, node_z)
    bridges = {p.edge.id for p in profiles if p.edge.bridge}
    shaped, conform_stats = conform_terrain(dtm, mesh, bridges)
    passes = 0
    while clearance(shaped, mesh, bridges)[1] > 0 and passes < 4:
        passes += 1  # rare: very steep cross slopes need the ground pressed a little further
        shaped, conform_stats = conform_terrain(dtm, mesh, bridges, extra_clearance=0.15 * passes)
    ctx.check_cancelled()
    ctx.report(0.85, "Saving the roads")
    all_repairs = {
        **graph.repairs,
        **repairs,
        "junction_slivers_dropped": mesh.stats.get("junction_slivers_dropped", 0),
        "hairpins_narrowed_inside": mesh.stats.get("hairpins_narrowed_inside", 0),
        "terrain_cells_filled": terrain_stats["gap_cells_filled"],
        "terrain_spikes_removed": terrain_stats["spike_cells_fixed"],
        "terrain_extra_passes": passes,
    }
    data = roads_to_json(profiles, graph.nodes, node_z)
    data["repairs"] = {k: int(v) for k, v in sorted(all_repairs.items())}
    data["conform"] = conform_stats
    (ctx.workdir / "roads.json").write_text(_compact_json(data), encoding="utf-8")
    shaped.write(ctx.workdir / "dtm_conformed.tif")
    mark_stage(ctx.workdir, "04_roads", inputs, outputs)
    ctx.log(
        f"Terrain shaped under the roads: deepest cut {conform_stats['max_cut_m']:.1f} m, "
        f"highest fill {conform_stats['max_fill_m']:.1f} m"
    )
    ctx.report(1.0, "Roads ready")


def _compact_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def load_road_model(
    workdir: Path,
) -> tuple[list[Profile], list[tuple[float, float]], Any, RoadMesh, dict[str, int]]:
    """Roads as saved by stage 04, with the road mesh rebuilt from them (it is deterministic)."""
    data = read_json(workdir / "roads.json")
    profiles, node_xy, node_z = roads_from_json(data)
    return profiles, node_xy, node_z, build_road_mesh(profiles, node_xy, node_z), dict(data["repairs"])


# -- 05 scene ------------------------------------------------------------------
def pick_spawn(profiles: list[Profile], sel: Selection) -> dict[str, Any]:
    """Where a car is put down: the start of the chosen route, or the middle of the main road."""

    def at(p: Profile, i: int) -> dict[str, Any]:
        j = min(i + 1, len(p.xy) - 1)
        d = p.xy[j] - p.xy[max(j - 1, 0)]
        heading = (math.degrees(math.atan2(d[0], d[1])) + 360.0) % 360.0  # compass: 0 = north
        return {
            "xyz": [round(float(p.xy[i, 0]), 3), round(float(p.xy[i, 1]), 3), round(float(p.z[i]) + 0.5, 3)],
            "heading_deg": round(heading, 1),
            "edge": p.edge.id,
        }

    if sel.route_local is not None:
        start = np.asarray(sel.route_local.coords[0][:2])
        best = min(
            ((float(np.min(np.linalg.norm(p.xy - start, axis=1))), p.edge.id, p) for p in profiles),
            key=lambda t: t[:2],
        )[2]
        return at(best, int(np.argmin(np.linalg.norm(best.xy - start, axis=1))))
    order = ["motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential"]

    def rank(p: Profile) -> tuple[int, float, int]:
        cls = order.index(p.edge.cls) if p.edge.cls in order else len(order)
        return (cls if not (p.edge.bridge or p.edge.tunnel) else 99, -p.length, p.edge.id)

    best = min(profiles, key=rank)
    return at(best, len(best.xy) // 2)


def run_scene(ctx: StageContext) -> None:
    inputs = hash_of({"v": CODE_VERSION, "roads": stage_inputs(ctx.workdir, "04_roads")})
    outputs = ["scene.json", "preview.glb"]
    if stage_is_fresh(ctx.workdir, "05_scene", inputs, outputs):
        ctx.log("3D model unchanged, reusing it")
        ctx.report(1.0, "3D model ready")
        return
    sel = selection_from_params(ctx.params)
    dtm = Dtm.read(ctx.workdir / "dtm_conformed.tif")
    profiles, node_xy, node_z, mesh, _ = load_road_model(ctx.workdir)
    ctx.report(0.2, "Building the road surface")
    roads = road_meshes(mesh, dtm)
    summaries = [mesh_summary(m) for m in roads]
    ctx.check_cancelled()
    ctx.report(0.4, "Cutting the terrain into tiles")
    solid = np.asarray(mesh.face_surface) != "apron"
    road_cells = np.isfinite(road_height_raster(dtm, mesh, solid))
    for tile in iter_terrain_meshes(dtm, road_cells, sel.skirt_local):
        summaries.append(mesh_summary(tile))
        ctx.check_cancelled()
    ctx.report(0.75, "Writing the 3D preview")
    write_glb([*roads, preview_terrain(dtm, sel.skirt_local)], ctx.workdir / "preview.glb")
    georef = Georef.read(ctx.workdir / "georef.json").to_dict()
    manifest = scene_manifest(summaries, profiles, node_xy, node_z, georef, dtm, pick_spawn(profiles, sel))
    write_scene_json(manifest, ctx.workdir / "scene.json")
    mark_stage(ctx.workdir, "05_scene", inputs, outputs)
    triangles = sum(m["triangles"] for m in summaries)
    ctx.log(f"3D model: {len(summaries)} meshes, {triangles / 1e6:.2f} million triangles")
    ctx.report(1.0, "3D model ready")


# -- 06 check ------------------------------------------------------------------
def run_check(ctx: StageContext) -> None:
    inputs = hash_of({"v": CODE_VERSION, "scene": stage_inputs(ctx.workdir, "05_scene")})
    outputs = ["drivability.json"]
    if stage_is_fresh(ctx.workdir, "06_check", inputs, outputs):
        report = read_json(ctx.workdir / "drivability.json")
    else:
        dtm = Dtm.read(ctx.workdir / "dtm_conformed.tif")
        profiles, _, node_z, mesh, repairs = load_road_model(ctx.workdir)
        ctx.report(0.3, "Driving every road on paper")
        summaries = read_json(ctx.workdir / "scene.json")["meshes"]
        report = check_drivability(profiles, node_z, mesh, dtm, summaries, repairs)
        write_json(ctx.workdir / "drivability.json", report)
        mark_stage(ctx.workdir, "06_check", inputs, outputs)
    for check in report["checks"]:
        mark = "OK" if check["ok"] else "PROBLEM"
        ctx.log(
            f"{mark}: {check['label']} ({check['value']} {check['unit']})".replace("  ", " "),
            "info" if check["ok"] else "warning",
        )
    for repair in report["repairs"]:
        ctx.log(f"Repaired: {repair['text']}")
    for note in report["notes"]:
        ctx.log(note, "warning")
    failed = [c["label"] for c in report["checks"] if not c["ok"]]
    s = report["summary"]
    if failed:
        ctx.report(1.0, f"Built, but {len(failed)} drivability checks need a look")
    else:
        ctx.report(
            1.0, f"Drivable: {s['road_km']:.1f} km of road, {s['junctions']} junctions, all checks passed"
        )
