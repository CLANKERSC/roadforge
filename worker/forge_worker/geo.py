"""Coordinate handling.

Everything inside the pipeline works in a local metric projection: a transverse Mercator
centred on the selection, so the game origin (0, 0) is the area centre and coordinates stay
small. georef.json stores the transform so game positions can be mapped back to lat/lon.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Any

from pyproj import CRS, Geod, Transformer
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shp_transform

WGS84 = CRS.from_epsg(4326)
GEOD = Geod(ellps="WGS84")


@dataclass(frozen=True)
class Georef:
    """Local transverse Mercator centred on (lat0, lon0). x = east, y = north, metres."""

    lat0: float
    lon0: float

    @property
    def proj4(self) -> str:
        return (
            f"+proj=tmerc +lat_0={self.lat0:.8f} +lon_0={self.lon0:.8f} +k=1 +x_0=0 +y_0=0 "
            "+ellps=WGS84 +units=m +no_defs"
        )

    @cached_property
    def crs(self) -> CRS:
        return CRS.from_proj4(self.proj4)

    @cached_property
    def _fwd(self) -> Transformer:
        return Transformer.from_crs(WGS84, self.crs, always_xy=True)

    @cached_property
    def _inv(self) -> Transformer:
        return Transformer.from_crs(self.crs, WGS84, always_xy=True)

    def to_local(self, lon: float, lat: float) -> tuple[float, float]:
        x, y = self._fwd.transform(lon, lat)
        return float(x), float(y)

    def to_lonlat(self, x: float, y: float) -> tuple[float, float]:
        lon, lat = self._inv.transform(x, y)
        return float(lon), float(lat)

    def geom_to_local(self, geom: BaseGeometry) -> BaseGeometry:
        return shp_transform(self._fwd.transform, geom)

    def geom_to_lonlat(self, geom: BaseGeometry) -> BaseGeometry:
        return shp_transform(self._inv.transform, geom)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "projection": "local transverse mercator",
            "origin_lat": self.lat0,
            "origin_lon": self.lon0,
            "proj4": self.proj4,
            "axes": "x=east, y=north, metres; game origin (0,0) = origin_lat/origin_lon",
        }

    def write(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True), encoding="utf-8")

    @classmethod
    def read(cls, path: Path) -> Georef:
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(lat0=float(data["origin_lat"]), lon0=float(data["origin_lon"]))


def line_length_m(coords: list[tuple[float, float]]) -> float:
    """Geodesic length of a lon/lat polyline in metres."""
    if len(coords) < 2:
        return 0.0
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    return float(GEOD.line_length(lons, lats))


def transformer(src: CRS | str | int, dst: CRS | str | int) -> Transformer:
    return Transformer.from_crs(src, dst, always_xy=True)
