"""Join the downloaded aerial or satellite photo tiles into one picture of the map area."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from affine import Affine
from numpy.typing import NDArray
from rasterio.crs import CRS
from rasterio.errors import RasterioError
from rasterio.transform import from_bounds
from rasterio.warp import Resampling, reproject, transform_bounds


def ground_photo(
    files: list[dict[str, Any]],
    cache_root: Path,
    dst_crs: CRS,
    west: float,
    north: float,
    extent_m: float,
    pixels: int,
) -> tuple[NDArray[np.uint8], NDArray[np.bool_]]:
    """Photo of the square [west, west+extent] x [north-extent, north], north up.

    Returns (rgb (3, n, n), covered (n, n)). Tiles that can't be read are skipped: a map with a
    hole in its photo is better than no map.
    """
    px = extent_m / pixels
    rgb = np.zeros((3, pixels, pixels), dtype=np.uint8)
    covered = np.zeros((pixels, pixels), dtype=bool)
    for entry in sorted(files, key=lambda e: e["key"]):
        meta = entry.get("meta", {})
        if meta.get("blank"):
            continue
        try:
            with rasterio.open(cache_root / entry["path"]) as ds:
                bbox = meta.get("bbox_5514")
                # plain JPEG tiles carry no position: the adapter recorded where they belong
                src_transform = from_bounds(*bbox, ds.width, ds.height) if bbox else ds.transform
                src_crs = CRS.from_user_input(meta["crs"]) if meta.get("crs") else ds.crs
                bounds = tuple(bbox) if bbox else tuple(ds.bounds)
                left, bottom, right, top = transform_bounds(src_crs, dst_crs, *bounds, densify_pts=21)
                c0 = max(0, int(math.floor((left - west) / px)) - 1)
                c1 = min(pixels, int(math.ceil((right - west) / px)) + 1)
                r0 = max(0, int(math.floor((north - top) / px)) - 1)
                r1 = min(pixels, int(math.ceil((north - bottom) / px)) + 1)
                if c0 >= c1 or r0 >= r1:
                    continue
                bands = [1, 2, 3] if ds.count >= 3 else [1, 1, 1]
                src = ds.read(bands)
                if src.dtype != np.uint8:
                    src = np.clip(src, 0, 255).astype(np.uint8)
                # a fourth band of ones tells us which target pixels this tile really reached
                src = np.concatenate([src, np.full((1, *src.shape[1:]), 255, dtype=np.uint8)])
                part = np.zeros((4, r1 - r0, c1 - c0), dtype=np.uint8)
                reproject(
                    source=src,
                    destination=part,
                    src_transform=src_transform,
                    src_crs=src_crs,
                    dst_transform=Affine(px, 0.0, west + c0 * px, 0.0, -px, north - r0 * px),
                    dst_crs=dst_crs,
                    resampling=Resampling.average if px > 2 * abs(src_transform.a) else Resampling.bilinear,
                )
        except (RasterioError, OSError, ValueError):
            continue
        use = (part[3] > 200) & ~covered[r0:r1, c0:c1]
        rgb[:, r0:r1, c0:c1][:, use] = part[:3][:, use]
        covered[r0:r1, c0:c1] |= use
    return rgb, covered
