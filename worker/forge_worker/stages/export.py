"""Stage 07: write the game files. BeamNG first; Assetto Corsa follows in its own milestone."""

from __future__ import annotations

import os
import shutil
import sys
import zlib
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray
from rasterio.crs import CRS
from shapely.geometry import LineString, box
from shapely.geometry.base import BaseGeometry

from forge_worker.buildings import Building, Soup, building_meshes, load_buildings
from forge_worker.exporters.beamng.level import (
    LevelInputs,
    default_ground_photo,
    shrink_for_beamng,
    write_level,
    zip_level,
)
from forge_worker.imagery import ground_photo
from forge_worker.pipeline import StageContext
from forge_worker.roadgeom import Profile
from forge_worker.roadnet import load_osm
from forge_worker.scene import road_meshes
from forge_worker.stages.common import (
    hash_of,
    mark_stage,
    read_json,
    selection_from_params,
    stage_inputs,
    stage_is_fresh,
    write_json,
)
from forge_worker.stages.world import CODE_VERSION, load_road_model
from forge_worker.terrain import Dtm
from forge_worker.vegetation import Plant, load_areas, place_plants

EXPORT_VERSION = 4


def run_export(ctx: StageContext) -> None:
    build = read_json(ctx.workdir / "build.json")
    games: list[str] = list(build["games"])
    inputs = hash_of(
        {
            "v": [CODE_VERSION, EXPORT_VERSION],
            "check": stage_inputs(ctx.workdir, "06_check"),
            "games": games,
            "name": build["name"],
        }
    )
    outputs = ["export.json"]
    verdict = _verdict(read_json(ctx.workdir / "drivability.json"))
    if stage_is_fresh(ctx.workdir, "07_export", inputs, outputs):
        old = read_json(ctx.workdir / "export.json")
        if all((ctx.workdir / g["zip"]).is_file() for g in old["games"].values()):
            ctx.log("Game files unchanged, reusing them")
            ctx.report(1.0, _closing(verdict, old))
            return
    out_dir = ctx.workdir / "export"
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True)
    result: dict[str, Any] = {"version": 1, "games": {}}
    if "beamng" in games:
        ctx.report(0.1, "Writing the BeamNG level")
        level_id = build["ids"]["beamng"]
        dtm = Dtm.read(ctx.workdir / "dtm_conformed.tif")
        profiles, _, _, mesh, _ = load_road_model(ctx.workdir)
        tree = out_dir / "beamng_tree"
        ctx.report(0.2, "Laying the aerial photo over the ground")
        photo = _ground_photo(ctx, dtm)
        ctx.report(0.4, "Putting up the buildings")
        blocks, footprints = _buildings(ctx, dtm, profiles)
        ctx.report(0.5, "Planting trees")
        plants = _plants(ctx, dtm, profiles, footprints, level_id)
        summary = write_level(
            tree,
            LevelInputs(
                level_id=level_id,
                title=str(build["name"]),
                dtm=dtm,
                road_meshes=road_meshes(mesh, dtm),
                profiles=profiles,
                spawn=read_json(ctx.workdir / "scene.json")["spawn"],
                credits=(ctx.workdir / "CREDITS.txt").read_text(encoding="utf-8"),
                ground_photo=photo,
                buildings=blocks,
                plants=plants,
            ),
        )
        ctx.check_cancelled()
        ctx.report(0.7, "Packing the BeamNG mod")
        zip_rel = f"export/{level_id}.zip"
        size = zip_level(tree, ctx.workdir / zip_rel)
        shutil.rmtree(tree, ignore_errors=True)
        result["games"]["beamng"] = {"zip": zip_rel, "bytes": size, **summary}
        ctx.log(
            f"BeamNG mod written: {level_id}.zip, {size / 1e6:.1f} MB, terrain {summary['terrain_size']} x "
            f"{summary['terrain_size']} at {summary['square_size_m']:g} m, {summary['ai_roads']} AI roads"
        )
    if "ac" in games:
        ctx.log("Assetto Corsa export is not built yet (next milestone); no AC files were written", "warning")
    write_json(ctx.workdir / "export.json", result)
    mark_stage(ctx.workdir, "07_export", inputs, outputs)
    ctx.report(1.0, _closing(verdict, result))


def _verdict(report: dict[str, Any]) -> str:
    s = report["summary"]
    failed = sum(1 for c in report["checks"] if not c["ok"])
    if failed:
        return f"Built, but {failed} drivability checks need a look"
    return f"Drivable: {s['road_km']:.1f} km of road, {s['junctions']} junctions, all checks passed"


def _buildings(
    ctx: StageContext, dtm: Dtm, profiles: list[Profile]
) -> tuple[dict[str, dict[str, Soup]], list[BaseGeometry]]:
    """Buildings from the map, grouped into 512 m blocks so the game only draws the near ones."""
    vectors = read_json(ctx.workdir / "fetch_manifest.json")["layers"]["vectors"]
    sel = selection_from_params(ctx.params)
    osm_nodes, ways = load_osm([ctx.env.cache.root / f["path"] for f in vectors["files"]])
    roads: list[BaseGeometry] = [
        LineString(p.xy).buffer(p.edge.width / 2 + 1.0, cap_style="flat") for p in profiles if len(p.xy) >= 2
    ]
    found, stats = load_buildings(
        osm_nodes, ways, sel.georef, sel.skirt_local.intersection(dtm_box(dtm)), roads
    )
    groups: dict[str, list[Building]] = {}
    for b in found:
        c = b.footprint.centroid
        groups.setdefault(f"buildings_{int(c.x // 512) + 500:03d}_{int(c.y // 512) + 500:03d}", []).append(b)
    blocks = {name: building_meshes(items, dtm) for name, items in sorted(groups.items())}
    if stats["buildings_on_road_left_out"]:
        ctx.log(
            f"Left out {stats['buildings_on_road_left_out']} buildings that the map draws on top of a road"
        )
    ctx.log(f"Buildings: {len(found)} from the map, with their real outline and height where the map has it")
    return blocks, [b.footprint for b in found]


def _plants(
    ctx: StageContext, dtm: Dtm, profiles: list[Profile], footprints: list[BaseGeometry], level_id: str
) -> list[Plant]:
    """Trees and bushes where the map has forests, orchards, scrub, tree rows and single trees."""
    vectors = read_json(ctx.workdir / "fetch_manifest.json")["layers"]["vectors"]
    sel = selection_from_params(ctx.params)
    areas, points, rows = load_areas([ctx.env.cache.root / f["path"] for f in vectors["files"]], sel.georef)
    # nothing grows on a road or within 3 m of its edge, nor inside or against a building
    clear: list[BaseGeometry] = [
        LineString(p.xy).buffer(p.edge.width / 2 + 3.0) for p in profiles if len(p.xy) >= 2
    ]
    clear += [f.buffer(2.0) for f in footprints]
    area = sel.skirt_local.intersection(dtm_box(dtm))
    plants, stats = place_plants(areas, points, rows, area, clear, dtm, zlib.crc32(level_id.encode()))
    if plants:
        ctx.log(
            f"Trees and bushes: {len(plants)} ({stats['forest']} in forests, {stats['scrub']} scrub, "
            f"{stats['orchard']} orchard, {stats['row']} in tree rows, {stats['single']} single trees)"
        )
    else:
        ctx.log("The map shows no forests or trees in this area")
    return plants


def dtm_box(dtm: Dtm) -> BaseGeometry:
    rows, cols = dtm.shape
    return box(dtm.west + 4, dtm.north - rows * dtm.res + 4, dtm.west + cols * dtm.res - 4, dtm.north - 4)


def _ground_photo(ctx: StageContext, dtm: Dtm) -> NDArray[np.uint8] | None:
    """The downloaded photo of the place, cut to the square BeamNG's terrain covers."""
    layer = read_json(ctx.workdir / "fetch_manifest.json")["layers"].get("imagery")
    if not layer or not layer.get("files"):
        ctx.log("No aerial photo was downloaded for this place: the ground is plain green", "warning")
        return None
    z, res = shrink_for_beamng(dtm)
    cells = 1 << (max(*z.shape, 2) - 1).bit_length()  # same padding as the terrain file
    extent = cells * res
    pixels = 2048 if extent <= 3000 else 4096
    south = dtm.north - z.shape[0] * res
    rgb, covered = ground_photo(
        layer["files"],
        ctx.env.cache.root,
        CRS.from_proj4(dtm.proj4),
        dtm.west + res / 2,  # the terrain's first height sample sits in the middle of the first cell
        south + res / 2 + extent,
        extent,
        pixels,
    )
    share = float(covered.mean())
    if share < 0.02:
        ctx.log("The aerial photo could not be read: the ground is plain green", "warning")
        return None
    filler = default_ground_photo(pixels)
    rgb[:, ~covered] = filler[:, ~covered]
    ctx.log(
        f"Ground photo: {layer['name']}, {extent / pixels:.1f} m per pixel, "
        f"covers {share * 100:.0f} % of the terrain square"
    )
    return rgb


def _closing(verdict: str, result: dict[str, Any]) -> str:
    return verdict + (". BeamNG mod ready" if "beamng" in result["games"] else "")


def beamng_mods_dir() -> Path | None:
    """BeamNG's user mods folder, or None if BeamNG's user folder can't be found.

    FORGE_BEAMNG_MODS overrides it (also used by tests). On Windows the user folder is
    %LOCALAPPDATA%/BeamNG/BeamNG.drive (newer versions also %LOCALAPPDATA%/BeamNG) with a
    'current' folder or one folder per game version.
    """
    override = os.environ.get("FORGE_BEAMNG_MODS")
    if override:
        return Path(override)
    if sys.platform != "win32":
        return None
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    for base in (local / "BeamNG" / "BeamNG.drive", local / "BeamNG"):
        if not base.is_dir():
            continue
        if (base / "current").is_dir():
            return base / "current" / "mods"
        versions = sorted(
            (p for p in base.iterdir() if p.is_dir() and p.name[:1].isdigit()),
            key=lambda p: [int(x) if x.isdigit() else 0 for x in p.name.split(".")],
        )
        if versions:
            return versions[-1] / "mods"
    return None


def install_beamng(workdir: Path) -> Path:
    """Copy the built mod ZIP, still zipped, into BeamNG's mods folder. Returns where it went."""
    export = workdir / "export.json"
    if not export.is_file() or "beamng" not in read_json(export)["games"]:
        raise FileNotFoundError("This build has no BeamNG mod. Tick BeamNG.drive and build again.")
    zip_path = workdir / str(read_json(export)["games"]["beamng"]["zip"])
    mods = beamng_mods_dir()
    if mods is None:
        raise FileNotFoundError(
            f"BeamNG's mods folder was not found on this computer. The mod is here: {zip_path}. "
            "Copy it, still zipped, into BeamNG's mods folder."
        )
    mods.mkdir(parents=True, exist_ok=True)
    target = mods / zip_path.name
    shutil.copyfile(zip_path, target)
    return target
