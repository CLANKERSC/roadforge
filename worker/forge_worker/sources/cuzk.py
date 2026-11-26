"""Czech Republic: ČÚZK open data (State Administration of Land Surveying and Cadastre).

- DMR 5G: bare-earth terrain model from airborne LiDAR, served on a 2 m grid.
- Ortofoto ČR: aerial orthophoto, down to 12.5 cm; we fetch 0.5 m.

Both come from ČÚZK's public ArcGIS services in the national grid S-JTSK / Krovak East North
(EPSG:5514). Tiles are requested on a fixed grid so the cache is shared between builds.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import rasterio
from shapely.geometry import Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shp_transform

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

DMR5G_URL = "https://ags.cuzk.gov.cz/arcgis2/rest/services/dmr5g/ImageServer/exportImage"
ORTHO_URL = "https://ags.cuzk.gov.cz/arcgis1/rest/services/ORTOFOTO/MapServer/export"
SJTSK = 5514
NODATA = -9999.0

# Rough outline of the Czech Republic (lon, lat), padded by ~10 km. It only decides whether
# to *try* ČÚZK; tiles that come back empty make the build fall back to the global source.
_CZ_OUTLINE = Polygon(
    [
        (12.09, 50.25), (12.95, 50.42), (13.85, 50.73), (14.32, 51.06), (15.0, 51.0), (15.37, 50.78),
        (16.35, 50.66), (16.2, 50.42), (16.7, 50.1), (17.0, 50.43), (17.72, 50.32), (17.6, 50.0),
        (18.05, 50.05), (18.86, 49.5), (18.4, 49.3), (18.1, 49.07), (17.65, 48.85), (17.2, 48.87),
        (16.95, 48.6), (16.1, 48.74), (15.0, 49.0), (14.7, 48.58), (14.3, 48.55), (13.84, 48.77),
        (13.0, 49.3), (12.4, 49.75),
    ]
).buffer(0.1)  # fmt: skip

_ATTRIBUTION = "© ČÚZK (Český úřad zeměměřický a katastrální), www.cuzk.cz"


def grid_tiles(sel: Selection, tile_m: float) -> list[tuple[int, int]]:
    """Fixed-grid tile indices in EPSG:5514 that touch the skirted selection."""
    to_sjtsk = transformer(4326, SJTSK)
    geom: BaseGeometry = shp_transform(to_sjtsk.transform, sel.skirt_lonlat)
    minx, miny, maxx, maxy = geom.bounds
    out = []
    for ix in range(math.floor(minx / tile_m), math.floor(maxx / tile_m) + 1):
        for iy in range(math.floor(miny / tile_m), math.floor(maxy / tile_m) + 1):
            if box(ix * tile_m, iy * tile_m, (ix + 1) * tile_m, (iy + 1) * tile_m).intersects(geom):
                out.append((ix, iy))
    return out


def tile_bbox(ix: int, iy: int, tile_m: float) -> tuple[float, float, float, float]:
    return (ix * tile_m, iy * tile_m, (ix + 1) * tile_m, (iy + 1) * tile_m)


class CuzkDmr5g(Source):
    PX = 1024
    RES = 2.0
    TILE_M = PX * RES

    info = SourceInfo(
        id="cuzk_dmr5g",
        layer="elevation",
        name="ČÚZK DMR 5G (LiDAR terrain)",
        resolution_m=RES,
        licence="CC BY 4.0",
        licence_url="https://geoportal.cuzk.cz/",
        attribution=_ATTRIBUTION,
        redistributable_derived=True,
        coverage=_CZ_OUTLINE,
        coverage_label="Czech Republic",
        est_mb_per_km2=1.0,
    )

    def fetch(self, sel: Selection, ctx: FetchContext) -> list[FetchedFile]:
        def one(tile: tuple[int, int]) -> FetchedFile:
            bbox = tile_bbox(*tile, self.TILE_M)

            def download(tmp: Path) -> None:
                params = {
                    "bbox": ",".join(f"{v:.2f}" for v in bbox),
                    "bboxSR": SJTSK,
                    "imageSR": SJTSK,
                    "size": f"{self.PX},{self.PX}",
                    "format": "tiff",
                    "pixelType": "F32",
                    "noData": NODATA,
                    "interpolation": "RSP_BilinearInterpolation",
                    "f": "image",
                }
                resp = ctx.http.get(DMR5G_URL, params=params)
                if "tiff" not in resp.headers.get("Content-Type", ""):
                    raise SourceUnavailable(f"ČÚZK DMR 5G did not return an image: {resp.text[:120]}")
                tmp.write_bytes(resp.content)

            cached = ctx.cache.get_or_fetch(self.info.id, f"t_{tile[0]}_{tile[1]}", ".tif", download)
            with rasterio.open(cached.path) as ds:
                data = ds.read(1)
            valid = float(np.mean((data > -1000) & np.isfinite(data)))
            return FetchedFile(cached, {"bbox_5514": list(bbox), "crs": "EPSG:5514", "valid_fraction": valid})

        try:
            files = fetch_parallel(grid_tiles(sel, self.TILE_M), one, ctx, "Elevation (LiDAR 2 m)")
        except HttpError as exc:
            raise SourceUnavailable(f"ČÚZK elevation service not reachable ({exc})") from exc
        valid = float(np.mean([f.meta["valid_fraction"] for f in files])) if files else 0.0
        if valid < 0.6:
            raise SourceUnavailable(
                f"ČÚZK elevation covers only {valid:.0%} of this area (outside the Czech Republic?)."
            )
        return files


class CuzkOrtofoto(Source):
    PX = 2048
    RES = 0.5
    TILE_M = PX * RES

    info = SourceInfo(
        id="cuzk_ortofoto",
        layer="imagery",
        name="ČÚZK Ortofoto ČR (aerial photo)",
        resolution_m=RES,
        licence="CC BY 4.0",
        licence_url="https://geoportal.cuzk.cz/",
        attribution=_ATTRIBUTION,
        redistributable_derived=True,
        coverage=_CZ_OUTLINE,
        coverage_label="Czech Republic",
        est_mb_per_km2=1.2,
    )

    def fetch(self, sel: Selection, ctx: FetchContext) -> list[FetchedFile]:
        def one(tile: tuple[int, int]) -> FetchedFile:
            bbox = tile_bbox(*tile, self.TILE_M)

            def download(tmp: Path) -> None:
                params = {
                    "bbox": ",".join(f"{v:.2f}" for v in bbox),
                    "bboxSR": SJTSK,
                    "imageSR": SJTSK,
                    "size": f"{self.PX},{self.PX}",
                    "format": "jpg",
                    "f": "image",
                }
                resp = ctx.http.get(ORTHO_URL, params=params)
                if "image" not in resp.headers.get("Content-Type", ""):
                    raise SourceUnavailable(f"ČÚZK Ortofoto did not return an image: {resp.text[:120]}")
                tmp.write_bytes(resp.content)

            cached = ctx.cache.get_or_fetch(self.info.id, f"t_{tile[0]}_{tile[1]}", ".jpg", download)
            # a blank tile (outside the country) compresses to almost nothing
            return FetchedFile(
                cached, {"bbox_5514": list(bbox), "crs": "EPSG:5514", "blank": cached.bytes < 12_000}
            )

        try:
            files = fetch_parallel(grid_tiles(sel, self.TILE_M), one, ctx, "Aerial photo (0.5 m)")
        except HttpError as exc:
            raise SourceUnavailable(f"ČÚZK photo service not reachable ({exc})") from exc
        blank = sum(1 for f in files if f.meta["blank"])
        if files and blank / len(files) > 0.4:
            raise SourceUnavailable("ČÚZK aerial photo is empty for most of this area.")
        return files
