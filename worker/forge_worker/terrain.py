"""Stage 03: fuse the downloaded elevation tiles into one clean height grid (DTM).

The grid lives in the build's local metric projection: x = east, y = north, row 0 = north.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from numpy.typing import NDArray
from rasterio.transform import Affine
from rasterio.warp import Resampling, reproject, transform_bounds
from scipy import ndimage

from forge_worker.geo import Georef

F32 = NDArray[np.float32]
DTM_RES = 2.0  # metres per cell (Standard preset)


@dataclass
class Dtm:
    """Height grid. `z[row, col]` is the height at the cell centre."""

    z: F32
    west: float  # x of the left edge of column 0
    north: float  # y of the top edge of row 0
    res: float
    proj4: str

    @property
    def shape(self) -> tuple[int, int]:
        return (int(self.z.shape[0]), int(self.z.shape[1]))

    @property
    def transform(self) -> Affine:
        return Affine(self.res, 0.0, self.west, 0.0, -self.res, self.north)

    def cell_xy(self) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
        """x of every column centre and y of every row centre."""
        rows, cols = self.shape
        xs = self.west + (np.arange(cols) + 0.5) * self.res
        ys = self.north - (np.arange(rows) + 0.5) * self.res
        return np.asarray(xs, dtype=np.float64), np.asarray(ys, dtype=np.float64)

    def to_cell(self, x: Any, y: Any) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
        """Fractional (row, col) of points, where integer values are cell centres."""
        col = (np.asarray(x, dtype=np.float64) - self.west) / self.res - 0.5
        row = (self.north - np.asarray(y, dtype=np.float64)) / self.res - 0.5
        return row, col

    def sample(self, x: Any, y: Any) -> NDArray[np.float64]:
        """Bilinear height at points (clamped at the grid edge)."""
        row, col = self.to_cell(x, y)
        out = ndimage.map_coordinates(self.z, [row, col], order=1, mode="nearest")
        return np.asarray(out, dtype=np.float64)

    def sample_surface(self, x: Any, y: Any) -> NDArray[np.float64]:
        """Height of the triangulated terrain surface (as the mesh is built) at points."""
        row, col = self.to_cell(x, y)
        rows, cols = self.shape
        row, col = np.clip(row, 0, rows - 1), np.clip(col, 0, cols - 1)
        r0 = np.minimum(np.floor(row).astype(int), rows - 2)
        c0 = np.minimum(np.floor(col).astype(int), cols - 2)
        fr, fc = row - r0, col - c0
        z = self.z.astype(np.float64)
        a, b, d, e = z[r0, c0], z[r0, c0 + 1], z[r0 + 1, c0], z[r0 + 1, c0 + 1]
        upper = a + (b - a) * fc + (d - a) * fr
        lower = e + (d - e) * (1.0 - fc) + (b - e) * (1.0 - fr)
        return np.asarray(np.where(fc + fr <= 1.0, upper, lower), dtype=np.float64)

    def write(self, path: Path) -> None:
        profile = {
            "driver": "GTiff",
            "width": self.shape[1],
            "height": self.shape[0],
            "count": 1,
            "dtype": "float32",
            "crs": self.proj4,
            "transform": self.transform,
            "compress": "deflate",
            "predictor": 3,
            "tiled": True,
        }
        with rasterio.open(path, "w", **profile) as ds:
            ds.write(self.z, 1)

    @classmethod
    def read(cls, path: Path) -> Dtm:
        with rasterio.open(path) as ds:
            t = ds.transform
            return cls(z=ds.read(1).astype(np.float32), west=t.c, north=t.f, res=t.a, proj4=ds.crs.to_proj4())


def fill_gaps(z: F32) -> tuple[F32, int]:
    """Fill missing cells from the nearest valid cell. Returns (grid, cells filled)."""
    missing = ~np.isfinite(z)
    count = int(missing.sum())
    if count == 0:
        return z, 0
    if count == z.size:
        raise ValueError("The elevation data is empty for this area.")
    idx = ndimage.distance_transform_edt(missing, return_distances=False, return_indices=True)
    filled = z[tuple(idx)]
    # soften the nearest-neighbour steps inside the filled patches only
    blurred = ndimage.gaussian_filter(filled, 2.0)
    filled[missing] = blurred[missing]
    return filled.astype(np.float32), count


def remove_spikes(z: F32, threshold_m: float) -> tuple[F32, int]:
    """Replace single-cell spikes/pits (LiDAR noise) by the local median."""
    med = ndimage.median_filter(z, size=3, mode="nearest")
    bad = np.abs(z - med) > threshold_m
    out = z.copy()
    out[bad] = med[bad]
    return out, int(bad.sum())


def build_dtm(
    files: list[dict[str, Any]],
    cache_root: Path,
    georef: Georef,
    bounds: tuple[float, float, float, float],
    source_res: float,
    res: float = DTM_RES,
) -> tuple[Dtm, dict[str, Any]]:
    """Reproject every source tile into the local grid and clean the result."""
    minx, miny, maxx, maxy = bounds
    west = math.floor(minx / res) * res
    north = math.ceil(maxy / res) * res
    cols = int(math.ceil((maxx - west) / res))
    rows = int(math.ceil((north - miny) / res))
    z = np.full((rows, cols), np.nan, dtype=np.float32)
    dst_crs = georef.crs
    coarse = source_res > 2 * res
    for entry in sorted(files, key=lambda e: e["key"]):
        with rasterio.open(cache_root / entry["path"]) as ds:
            # where does this tile land in our grid?
            left, b, r, t = transform_bounds(ds.crs, dst_crs, *ds.bounds, densify_pts=21)
            c0 = max(0, int(math.floor((left - west) / res)) - 1)
            c1 = min(cols, int(math.ceil((r - west) / res)) + 1)
            r0 = max(0, int(math.floor((north - t) / res)) - 1)
            r1 = min(rows, int(math.ceil((north - b) / res)) + 1)
            if c0 >= c1 or r0 >= r1:
                continue
            part = np.full((r1 - r0, c1 - c0), np.nan, dtype=np.float32)
            nodata = ds.nodata if ds.nodata is not None else -9999.0
            reproject(
                source=rasterio.band(ds, 1),
                destination=part,
                src_nodata=nodata,
                dst_transform=Affine(res, 0.0, west + c0 * res, 0.0, -res, north - r0 * res),
                dst_crs=dst_crs,
                dst_nodata=np.nan,
                resampling=Resampling.cubic if coarse else Resampling.bilinear,
            )
            part[part < -1000] = np.nan
            target = z[r0:r1, c0:c1]
            use = np.isnan(target) & np.isfinite(part)
            target[use] = part[use]
    z, filled = fill_gaps(z)
    if coarse:
        # a 30 m source upsampled to 2 m shows stair steps; blur them away
        z = ndimage.gaussian_filter(z, sigma=source_res / res / 2.5).astype(np.float32)
        spikes = 0
    else:
        z, spikes = remove_spikes(z, threshold_m=1.5)
    dtm = Dtm(z=z, west=west, north=north, res=res, proj4=georef.proj4)
    stats = {
        "rows": rows,
        "cols": cols,
        "res_m": res,
        "source_res_m": source_res,
        "min_m": round(float(np.min(z)), 2),
        "max_m": round(float(np.max(z)), 2),
        "gap_cells_filled": filled,
        "spike_cells_fixed": spikes,
    }
    return dtm, stats
