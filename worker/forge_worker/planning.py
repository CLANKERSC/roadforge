"""What the UI shows before a build: size, limits, chosen data sources, estimates."""

from __future__ import annotations

from typing import Any

from forge_worker.naming import ac_track_id, beamng_level_id
from forge_worker.selection import Selection
from forge_worker.sources.base import Source
from forge_worker.sources.osm import cells_for
from forge_worker.sources.registry import choose

ASSUMED_MBIT = 20.0  # download speed used for the rough time estimate


def describe_source(src: Source, fallback: bool) -> dict[str, Any]:
    i = src.info
    return {
        "id": i.id,
        "name": i.name,
        "resolution_m": i.resolution_m,
        "licence": i.licence,
        "licence_url": i.licence_url,
        "attribution": i.attribution,
        "coverage": i.coverage_label,
        "note": i.quality_note,
        "fallback": fallback,
    }


def make_plan(sel: Selection, name: str) -> dict[str, Any]:
    chosen = choose(sel)
    layers: dict[str, Any] = {}
    total_mb = 0.0
    problems = sel.problems()
    for layer, srcs in chosen.items():
        if not srcs:
            problems.append(f"No {layer} data source covers this place.")
            continue
        total_mb += srcs[0].estimate_mb(sel)
        layers[layer] = {
            "chosen": describe_source(srcs[0], fallback=False),
            "fallbacks": [describe_source(s, fallback=True) for s in srcs[1:]],
        }
    seconds = total_mb * 8.0 / ASSUMED_MBIT + 6.0 * len(cells_for(sel)) + 10.0
    w, h = sel.size_km
    return {
        "ok": not problems,
        "problems": problems,
        "mode": sel.mode,
        "area_km2": round(sel.area_km2, 3),
        "route_km": None if sel.route_km is None else round(sel.route_km, 2),
        "size_km": [round(w, 2), round(h, 2)],
        "center": {"lat": sel.georef.lat0, "lon": sel.georef.lon0},
        "layers": layers,
        "est_download_mb": round(total_mb, 1),
        "est_minutes": round(seconds / 60.0, 1),
        "ids": {"ac": ac_track_id(name), "beamng": beamng_level_id(name)},
        "outline": sel.to_geojson(),
    }
