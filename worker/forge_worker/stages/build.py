"""The real build pipeline. M1 has the first two stages; later milestones append the rest.

01 select -> selection.geojson, georef.json
02 fetch  -> fetch_manifest.json (which files from which source, with licences)
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from forge_worker import licences
from forge_worker.geo import Georef
from forge_worker.naming import ac_track_id, beamng_level_id
from forge_worker.pipeline import Pipeline, Stage, StageContext, register
from forge_worker.selection import Selection, SelectionError, SelectionRequest, build_selection
from forge_worker.sources.base import FetchContext, FetchedFile, Source, SourceInfo, SourceUnavailable
from forge_worker.sources.registry import ALL_SOURCES, choose


def _hash(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def _write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, indent=2, sort_keys=True), encoding="utf-8")


def stage_is_fresh(workdir: Path, stage: str, inputs_hash: str, outputs: list[str]) -> bool:
    """True if this stage already ran with the same inputs and its files are still there."""
    marker = workdir / f"{stage}.stage.json"
    if not marker.is_file():
        return False
    data = json.loads(marker.read_text(encoding="utf-8"))
    return data.get("inputs") == inputs_hash and all((workdir / o).is_file() for o in outputs)


def mark_stage(workdir: Path, stage: str, inputs_hash: str, outputs: list[str]) -> None:
    _write_json(workdir / f"{stage}.stage.json", {"stage": stage, "inputs": inputs_hash, "outputs": outputs})


def selection_from_params(params: dict[str, Any]) -> Selection:
    return build_selection(SelectionRequest.model_validate(params["selection"]))


# -- 01 select -----------------------------------------------------------------
def run_select(ctx: StageContext) -> None:
    outputs = ["selection.geojson", "georef.json", "build.json"]
    inputs = _hash({"selection": ctx.params.get("selection"), "name": ctx.params.get("name")})
    if stage_is_fresh(ctx.workdir, "01_select", inputs, outputs):
        ctx.log("Selection unchanged, reusing it")
        ctx.report(1.0, "Selection ready")
        return
    sel = selection_from_params(ctx.params)
    problems = sel.problems()
    if problems:
        raise SelectionError(" ".join(problems))
    name = str(ctx.params.get("name") or "My map")
    _write_json(ctx.workdir / "selection.geojson", sel.to_geojson())
    sel.georef.write(ctx.workdir / "georef.json")
    _write_json(
        ctx.workdir / "build.json",
        {
            "name": name,
            "ids": {"ac": ac_track_id(name), "beamng": beamng_level_id(name)},
            "games": list(sel.games),
        },
    )
    mark_stage(ctx.workdir, "01_select", inputs, outputs)
    what = f"{sel.route_km:.1f} km road" if sel.route_km is not None else f"{sel.area_km2:.2f} km² area"
    ctx.log(f"Selection: {what}, centre {sel.georef.lat0:.5f}, {sel.georef.lon0:.5f}")
    ctx.report(1.0, "Selection ready")


# -- 02 fetch ------------------------------------------------------------------
def _fetch_layer(
    layer: str, sources: list[Source], sel: Selection, ctx: StageContext, lo: float, hi: float
) -> tuple[Source, list[FetchedFile], list[str]]:
    """Try sources sharpest-first; fall back to the next one if a source has no data here."""
    warnings: list[str] = []
    for src in sources:
        bad = licences.problems(src.info)
        if bad:
            raise RuntimeError(f"Source {src.info.id} is not usable: {', '.join(bad)}")

        def report(fraction: float, msg: str) -> None:
            ctx.report(lo + (hi - lo) * fraction, msg)

        fctx = FetchContext(
            cache=ctx.env.cache,
            http=ctx.env.http,
            report=report,
            log=ctx.log,
            check_cancelled=ctx.check_cancelled,
        )
        ctx.log(f"{layer}: using {src.info.name}")
        try:
            return src, src.fetch(sel, fctx), warnings
        except SourceUnavailable as exc:
            msg = f"{src.info.name} not usable here: {exc}"
            ctx.log(msg, "warning")
            warnings.append(msg)
    raise RuntimeError(f"No {layer} data could be downloaded for this place. " + " ".join(warnings))


def _file_entry(f: FetchedFile, cache_root: Path) -> dict[str, Any]:
    return {
        "path": f.file.path.relative_to(cache_root).as_posix(),
        "key": f.file.key,
        "bytes": f.file.bytes,
        "sha256": f.file.sha256,
        "meta": f.meta,
    }


def run_fetch(ctx: StageContext) -> None:
    sel_path = ctx.workdir / "selection.geojson"
    inputs = _hash(
        {"selection": sel_path.read_text(encoding="utf-8"), "sources": [s.info.id for s in ALL_SOURCES]}
    )
    if stage_is_fresh(ctx.workdir, "02_fetch", inputs, ["fetch_manifest.json"]):
        manifest = json.loads((ctx.workdir / "fetch_manifest.json").read_text(encoding="utf-8"))
        cache_root = ctx.env.cache.root
        if all(
            (cache_root / f["path"]).is_file()
            for layer in manifest["layers"].values()
            for f in layer["files"]
        ):
            ctx.log("Downloads unchanged and still in the cache, skipping")
            ctx.report(1.0, "Downloads ready")
            return
    sel = selection_from_params(ctx.params)
    assert Georef.read(ctx.workdir / "georef.json") == sel.georef
    chosen = choose(sel)
    layers: dict[str, Any] = {}
    used: list[SourceInfo] = []
    new_bytes = cached_bytes = 0
    for i, (layer, sources) in enumerate(chosen.items()):
        ctx.check_cancelled()
        src, files, warnings = _fetch_layer(layer, sources, sel, ctx, i / len(chosen), (i + 1) / len(chosen))
        used.append(src.info)
        for f in files:
            if f.file.from_cache:
                cached_bytes += f.file.bytes
            else:
                new_bytes += f.file.bytes
        layers[layer] = {
            "source": src.info.id,
            "name": src.info.name,
            "resolution_m": src.info.resolution_m,
            "licence": src.info.licence,
            "attribution": src.info.attribution,
            "note": src.info.quality_note,
            "warnings": warnings,
            "files": sorted((_file_entry(f, ctx.env.cache.root) for f in files), key=lambda e: e["key"]),
        }
        ctx.log(f"{layer}: {len(files)} files from {src.info.name}")
    _write_json(ctx.workdir / "fetch_manifest.json", {"version": 1, "layers": layers})
    (ctx.workdir / "CREDITS.txt").write_text(
        licences.credits_text(used, datetime.now(UTC).year), encoding="utf-8"
    )
    mark_stage(ctx.workdir, "02_fetch", inputs, ["fetch_manifest.json"])
    ctx.log(f"Downloaded {new_bytes / 1e6:.1f} MB, reused {cached_bytes / 1e6:.1f} MB from the cache")
    ctx.report(1.0, "Downloads ready")


BUILD = register(
    Pipeline(
        kind="build",
        label="Build map",
        stages=(
            Stage(id="select", label="Check selection", run=run_select),
            Stage(id="fetch", label="Download data", run=run_fetch),
        ),
    )
)
