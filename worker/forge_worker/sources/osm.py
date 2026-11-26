"""OpenStreetMap vectors via the Overpass API (roads, buildings, trees, poles, landuse...)."""

from __future__ import annotations

import json
import math
from pathlib import Path

from shapely.geometry import box

from forge_worker.selection import Selection
from forge_worker.sources.base import FetchContext, FetchedFile, Source, SourceInfo, fetch_parallel

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
CELL_DEG = 0.1  # fixed grid so overlapping builds share cached cells

_QUERY = """[out:json][timeout:180][maxsize:536870912];
(
  way["highway"]({b});
  node["highway"]({b});
  way["building"]({b});
  relation["building"]({b});
  way["barrier"]({b});
  node["barrier"]({b});
  way["power"]({b});
  node["power"]({b});
  node["natural"="tree"]({b});
  way["natural"]({b});
  relation["natural"]({b});
  way["landuse"]({b});
  relation["landuse"]({b});
  way["man_made"]({b});
  node["man_made"]({b});
  way["waterway"]({b});
  way["railway"]({b});
);
out body;
>;
out skel qt;
"""


def cells_for(sel: Selection) -> list[tuple[int, int]]:
    """Grid cells (ix, iy) of CELL_DEG degrees that touch the skirted selection."""
    geom = sel.skirt_lonlat
    w, s, e, n = geom.bounds
    out = []
    for ix in range(math.floor(w / CELL_DEG), math.floor(e / CELL_DEG) + 1):
        for iy in range(math.floor(s / CELL_DEG), math.floor(n / CELL_DEG) + 1):
            cell = box(ix * CELL_DEG, iy * CELL_DEG, (ix + 1) * CELL_DEG, (iy + 1) * CELL_DEG)
            if cell.intersects(geom):
                out.append((ix, iy))
    return out


def cell_bbox(ix: int, iy: int) -> tuple[float, float, float, float]:
    """(south, west, north, east), rounded so the query text is stable."""
    return (
        round(iy * CELL_DEG, 4),
        round(ix * CELL_DEG, 4),
        round((iy + 1) * CELL_DEG, 4),
        round((ix + 1) * CELL_DEG, 4),
    )


class OsmOverpass(Source):
    info = SourceInfo(
        id="osm_overpass",
        layer="vectors",
        name="OpenStreetMap",
        resolution_m=None,
        licence="ODbL 1.0",
        licence_url="https://www.openstreetmap.org/copyright",
        attribution="© OpenStreetMap contributors, ODbL",
        redistributable_derived=True,
        coverage=None,
        coverage_label="Whole world",
        est_mb_per_km2=0.15,
    )

    def fetch(self, sel: Selection, ctx: FetchContext) -> list[FetchedFile]:
        def one(cell: tuple[int, int]) -> FetchedFile:
            s, w, n, e = cell_bbox(*cell)

            def download(tmp: Path) -> None:
                query = _QUERY.format(b=f"{s},{w},{n},{e}")
                data = ctx.http.post_json(OVERPASS_URL, data={"data": query}, timeout=(10, 240))
                if "elements" not in data:
                    raise RuntimeError("Overpass returned no 'elements'")
                tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")

            cached = ctx.cache.get_or_fetch(self.info.id, f"cell_{cell[0]}_{cell[1]}", ".json", download)
            return FetchedFile(cached, {"bbox_swne": [s, w, n, e]})

        # Overpass allows about two requests at a time per user
        return fetch_parallel(cells_for(sel), one, ctx, "Map data", workers=2)
