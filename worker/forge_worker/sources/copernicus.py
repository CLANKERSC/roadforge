"""Global fallbacks from the EU Copernicus programme (AWS open data, no account needed).

- Copernicus DEM GLO-30: 30 m elevation, one GeoTIFF per 1 x 1 degree.
- Sentinel-2 L2A: 10 m true-colour imagery, picked per scene through the Earth Search STAC API.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import rasterio
from rasterio.windows import Window
from shapely.geometry import box, mapping

from forge_worker.geo import transformer
from forge_worker.http import HttpError
from forge_worker.selection import Selection
from forge_worker.sources.base import (
    FetchContext,
    FetchedFile,
    Source,
    SourceInfo,
    SourceUnavailable,
    fetch_parallel,
)
from forge_worker.sources.raster_io import remote_env

DEM_BASE = "https://copernicus-dem-30m.s3.amazonaws.com"
STAC_SEARCH = "https://earth-search.aws.element84.com/v1/search"
S2_BLOCK_PX = 1024  # 10.24 km blocks on the scene's own pixel grid


def dem_tile_name(lat: int, lon: int) -> str:
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    return f"Copernicus_DSM_COG_10_{ns}{abs(lat):02d}_00_{ew}{abs(lon):03d}_00_DEM"


def dem_tiles_for(sel: Selection) -> list[tuple[int, int]]:
    w, s, e, n = sel.bbox_lonlat
    geom = sel.skirt_lonlat
    return [
        (lat, lon)
        for lat in range(math.floor(s), math.floor(n) + 1)
        for lon in range(math.floor(w), math.floor(e) + 1)
        if box(lon, lat, lon + 1, lat + 1).intersects(geom)
    ]


class CopernicusDem30(Source):
    info = SourceInfo(
        id="copernicus_dem_glo30",
        layer="elevation",
        name="Copernicus DEM GLO-30",
        resolution_m=30.0,
        licence="Copernicus DEM licence (free use, attribution required)",
        licence_url="https://dataspace.copernicus.eu/explore-data/data-collections/copernicus-contributing-missions/collections-description/COP-DEM",
        attribution=(
            "© DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018 provided under "
            "COPERNICUS by the European Union and ESA; all rights reserved"
        ),
        redistributable_derived=True,
        coverage=None,
        coverage_label="Whole world",
        est_mb_per_km2=0.004,
        quality_note="Coarse 30 m elevation: hills are right, but roads will be less accurate.",
    )

    def estimate_mb(self, sel: Selection) -> float:
        return 25.0 * len(dem_tiles_for(sel))  # whole 1-degree tiles are downloaded

    def fetch(self, sel: Selection, ctx: FetchContext) -> list[FetchedFile]:
        def one(tile: tuple[int, int]) -> FetchedFile | None:
            name = dem_tile_name(*tile)
            url = f"{DEM_BASE}/{name}/{name}.tif"

            def download(tmp: Path) -> None:
                with ctx.http.get(url, stream=True) as resp, tmp.open("wb") as f:
                    for chunk in resp.iter_content(1 << 20):
                        ctx.check_cancelled()
                        f.write(chunk)

            try:
                cached = ctx.cache.get_or_fetch(self.info.id, name, ".tif", download)
            except HttpError as exc:
                if exc.status in (403, 404):  # no tile = open sea
                    ctx.log(f"No elevation tile {name} (open water), treated as sea level", "info")
                    return None
                raise
            return FetchedFile(cached, {"lat": tile[0], "lon": tile[1], "crs": "EPSG:4326"})

        got = fetch_parallel(dem_tiles_for(sel), one, ctx, "Elevation (30 m)", workers=2)
        files = [f for f in got if f is not None]
        if not files:
            raise SourceUnavailable("No Copernicus elevation tiles exist for this area (open sea?).")
        return files


class Sentinel2(Source):
    info = SourceInfo(
        id="sentinel2_l2a",
        layer="imagery",
        name="Sentinel-2 L2A (true colour)",
        resolution_m=10.0,
        licence="Copernicus Sentinel data terms (free, full and open)",
        licence_url="https://sentinels.copernicus.eu/documents/247904/690755/Sentinel_Data_Legal_Notice",
        attribution="Contains modified Copernicus Sentinel data {year}",
        redistributable_derived=True,
        coverage=None,
        coverage_label="Whole world (land)",
        est_mb_per_km2=0.0006,
        quality_note="10 m satellite imagery: good for ground colour only, never used as road texture.",
    )

    def estimate_mb(self, sel: Selection) -> float:
        return max(3.0, super().estimate_mb(sel) + 3.0)

    def _pick_scenes(self, sel: Selection, ctx: FetchContext) -> list[dict[str, Any]]:
        """Least-cloudy recent scene per Sentinel-2 grid tile that touches the selection."""
        now = datetime.now(UTC)
        body = {
            "collections": ["sentinel-2-l2a"],
            "intersects": mapping(sel.skirt_lonlat.envelope),
            "datetime": f"{now.year - 4}-01-01T00:00:00Z/{now.strftime('%Y-%m-%dT%H:%M:%SZ')}",
            "query": {"eo:cloud_cover": {"lt": 10}},
            "sortby": [{"field": "properties.eo:cloud_cover", "direction": "asc"}],
            "limit": 100,
        }
        try:
            result = ctx.http.post_json(STAC_SEARCH, json=body)
        except HttpError as exc:
            raise SourceUnavailable(f"Sentinel-2 catalogue not reachable ({exc})") from exc
        best: dict[str, dict[str, Any]] = {}
        for item in result.get("features", []):
            props = item.get("properties", {})
            href = item.get("assets", {}).get("visual", {}).get("href")
            grid = props.get("grid:code") or (
                f"MGRS-{props.get('mgrs:utm_zone')}{props.get('mgrs:latitude_band')}{props.get('mgrs:grid_square')}"
            )
            if not href or props.get("s2:nodata_pixel_percentage", 0) > 20:
                continue
            if grid not in best:  # results are sorted by cloud cover, first wins
                best[grid] = {
                    "id": item["id"],
                    "grid": grid,
                    "href": href,
                    "datetime": props.get("datetime", ""),
                    "cloud_cover": props.get("eo:cloud_cover"),
                }
        if not best:
            raise SourceUnavailable("No cloud-free Sentinel-2 scene found for this area.")
        return [best[k] for k in sorted(best)]

    def _scenes(self, sel: Selection, ctx: FetchContext) -> list[dict[str, Any]]:
        """Scene picks are cached per selection footprint so a rebuild uses the same images."""
        w, s, e, n = (round(v, 3) for v in sel.bbox_lonlat)
        key = f"pick_{w}_{s}_{e}_{n}"

        def download(tmp: Path) -> None:
            tmp.write_text(json.dumps(self._pick_scenes(sel, ctx), sort_keys=True), encoding="utf-8")

        cached = ctx.cache.get_or_fetch(self.info.id, key, ".json", download)
        scenes: list[dict[str, Any]] = json.loads(cached.path.read_text(encoding="utf-8"))
        return scenes

    def fetch(self, sel: Selection, ctx: FetchContext) -> list[FetchedFile]:
        jobs: list[tuple[dict[str, Any], int, int]] = []
        with remote_env():
            for scene in self._scenes(sel, ctx):
                ctx.check_cancelled()
                try:
                    with rasterio.open(scene["href"]) as ds:
                        to_scene = transformer(4326, ds.crs)
                        w, s, e, n = sel.bbox_lonlat
                        xs, ys = to_scene.transform([w, w, e, e], [s, n, s, n])
                        r0, c0 = ds.index(min(xs), max(ys))
                        r1, c1 = ds.index(max(xs), min(ys))
                        c0, c1 = max(0, c0), min(ds.width - 1, c1)
                        r0, r1 = max(0, r0), min(ds.height - 1, r1)
                        if c0 > c1 or r0 > r1:
                            continue  # scene only touches the bounding box corner
                        for by in range(r0 // S2_BLOCK_PX, r1 // S2_BLOCK_PX + 1):
                            for bx in range(c0 // S2_BLOCK_PX, c1 // S2_BLOCK_PX + 1):
                                jobs.append((scene, bx, by))
                except rasterio.errors.RasterioIOError as exc:
                    raise SourceUnavailable(f"Sentinel-2 scene not readable ({exc})") from exc

            def one(job: tuple[dict[str, Any], int, int]) -> FetchedFile:
                scene, bx, by = job

                def download(tmp: Path) -> None:
                    # GDAL options are per thread, so set them again inside the worker thread
                    with remote_env(), rasterio.open(scene["href"]) as ds:
                        win = Window(
                            bx * S2_BLOCK_PX,
                            by * S2_BLOCK_PX,
                            min(S2_BLOCK_PX, ds.width - bx * S2_BLOCK_PX),
                            min(S2_BLOCK_PX, ds.height - by * S2_BLOCK_PX),
                        )
                        data = ds.read(window=win)
                        profile = {
                            "driver": "GTiff",
                            "width": int(win.width),
                            "height": int(win.height),
                            "count": ds.count,
                            "dtype": data.dtype,
                            "crs": ds.crs,
                            "transform": ds.window_transform(win),
                            "compress": "deflate",
                        }
                        with rasterio.open(tmp, "w", **profile) as out:
                            out.write(data)

                cached = ctx.cache.get_or_fetch(self.info.id, f"{scene['id']}_b{bx}_{by}", ".tif", download)
                return FetchedFile(
                    cached,
                    {
                        "scene": scene["id"],
                        "datetime": scene["datetime"],
                        "cloud_cover": scene["cloud_cover"],
                    },
                )

            files = fetch_parallel(jobs, one, ctx, "Satellite imagery (10 m)", workers=3)
        if not files:
            raise SourceUnavailable("Sentinel-2 scenes found, but none overlap the selection.")
        return files
