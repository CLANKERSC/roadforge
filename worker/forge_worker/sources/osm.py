"""OpenStreetMap vectors via the Overpass API (roads, buildings, trees, poles, landuse...)."""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any

from shapely.geometry import box

from forge_worker.http import RETRY_STATUS, HttpError
from forge_worker.selection import Selection
from forge_worker.sources.base import FetchContext, FetchedFile, Source, SourceInfo, fetch_parallel

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
# Public Overpass servers, tried in this order. The main one is often full ("429 Too Many
# Requests"); the mirrors serve the same OpenStreetMap data, free and without an account.
OVERPASS_URLS = [
    OVERPASS_URL,
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
# Seconds to wait before each round over all servers. A busy server usually frees up in a minute.
ROUND_WAITS: tuple[float, ...] = (0, 15, 45, 90)


def query_overpass(ctx: FetchContext, query: str) -> dict[str, Any]:
    """Ask each server in turn; when all are busy, wait and go round again before giving up."""
    last: Exception = RuntimeError("no Overpass server was tried")
    for round_no, wait in enumerate(ROUND_WAITS):
        if wait:
            ctx.log(f"All map servers are busy, trying again in {wait:.0f} s", "warning")
            for _ in range(int(wait)):
                ctx.check_cancelled()
                time.sleep(1)
        for url in OVERPASS_URLS:
            ctx.check_cancelled()
            try:
                data = ctx.http.post_json(url, data={"data": query}, timeout=(10, 240))
            except HttpError as exc:
                if exc.status is not None and exc.status not in RETRY_STATUS:
                    raise  # our own mistake (bad query): another server would say the same
                last = exc
            except ValueError as exc:  # a full server sometimes answers with an HTML page
                last = RuntimeError(f"{url.split('/')[2]}: answer was not map data ({type(exc).__name__})")
            else:
                if isinstance(data, dict) and "elements" in data:
                    if round_no or url != OVERPASS_URL:
                        ctx.log(f"Map data came from {url.split('/')[2]} (the main server was busy)", "info")
                    return data
                last = RuntimeError(f"{url.split('/')[2]}: answer had no map data")
    raise RuntimeError(
        f"The OpenStreetMap servers are busy or out of reach ({len(OVERPASS_URLS)} servers, "
        f"{len(ROUND_WAITS)} tries each). Last answer: {last}. Press Rebuild in a few minutes: "
        "everything already downloaded is kept."
    ) from last


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
                data = query_overpass(ctx, query)
                tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")

            cached = ctx.cache.get_or_fetch(self.info.id, f"cell_{cell[0]}_{cell[1]}", ".json", download)
            return FetchedFile(cached, {"bbox_swne": [s, w, n, e]})

        # Overpass allows about two requests at a time per user
        return fetch_parallel(cells_for(sel), one, ctx, "Map data", workers=2)
