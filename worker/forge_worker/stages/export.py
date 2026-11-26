"""Stage 07: write the game files. BeamNG first; Assetto Corsa follows in its own milestone."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Any

from forge_worker.exporters.beamng.level import LevelInputs, write_level, zip_level
from forge_worker.pipeline import StageContext
from forge_worker.scene import road_meshes
from forge_worker.stages.common import (
    hash_of,
    mark_stage,
    read_json,
    stage_inputs,
    stage_is_fresh,
    write_json,
)
from forge_worker.stages.world import CODE_VERSION, load_road_model
from forge_worker.terrain import Dtm

EXPORT_VERSION = 1


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
