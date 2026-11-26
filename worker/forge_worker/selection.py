"""Stage 01 logic: turn what the user drew into a validated selection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator
from shapely.geometry import LineString, Polygon, mapping
from shapely.geometry.base import BaseGeometry

from forge_worker.geo import Georef, line_length_m

Game = Literal["ac", "beamng"]

SKIRT_M = 200.0
CORRIDOR_MIN_M, CORRIDOR_MAX_M, CORRIDOR_DEFAULT_M = 50.0, 1000.0, 300.0

# Size limits (configurable here in one place).
LIMITS: dict[str, dict[str, float]] = {
    "ac": {"route_km": 100.0, "side_km": 10.0},
    "beamng": {"route_km": 100.0, "side_km": 16.0},
}

LonLat = tuple[float, float]


class SelectionRequest(BaseModel):
    mode: Literal["route", "area"]
    # area mode: polygon ring as [lon, lat] pairs (closing point optional)
    polygon: list[LonLat] | None = None
    # route mode: the routed line along roads as [lon, lat] pairs
    route: list[LonLat] | None = None
    corridor_m: float = Field(default=CORRIDOR_DEFAULT_M, ge=CORRIDOR_MIN_M, le=CORRIDOR_MAX_M)
    games: list[Game] = Field(default_factory=lambda: list[Game](["ac"]), min_length=1)

    @field_validator("polygon", "route")
    @classmethod
    def _valid_coords(cls, value: list[LonLat] | None) -> list[LonLat] | None:
        for lon, lat in value or []:
            if not (-180 <= lon <= 180 and -85 <= lat <= 85):
                raise ValueError(f"coordinate out of range: {lon}, {lat}")
        return value


class SelectionError(ValueError):
    """The selection can't be built; the message is shown to the user as-is."""


@dataclass(frozen=True)
class Selection:
    mode: str
    games: tuple[str, ...]
    corridor_m: float
    georef: Georef
    core_local: BaseGeometry  # what the user asked for, local metres
    skirt_local: BaseGeometry  # core + 200 m, used for terrain and horizon
    route_local: LineString | None
    route_km: float | None

    @property
    def area_km2(self) -> float:
        return float(self.core_local.area) / 1e6

    @property
    def size_km(self) -> tuple[float, float]:
        minx, miny, maxx, maxy = self.core_local.bounds
        return (maxx - minx) / 1000.0, (maxy - miny) / 1000.0

    @property
    def core_lonlat(self) -> BaseGeometry:
        return self.georef.geom_to_lonlat(self.core_local)

    @property
    def skirt_lonlat(self) -> BaseGeometry:
        return self.georef.geom_to_lonlat(self.skirt_local)

    @property
    def bbox_lonlat(self) -> tuple[float, float, float, float]:
        """(west, south, east, north) of the skirted selection."""
        b = self.skirt_lonlat.bounds
        return (b[0], b[1], b[2], b[3])

    def problems(self) -> list[str]:
        """Limit violations per chosen game, as plain sentences."""
        out: list[str] = []
        w, h = self.size_km
        for game in self.games:
            lim = LIMITS[game]
            label = "Assetto Corsa" if game == "ac" else "BeamNG"
            if self.mode == "route":
                if self.route_km is not None and self.route_km > lim["route_km"]:
                    out.append(
                        f"{label}: the road is {self.route_km:.1f} km long, "
                        f"the limit is {lim['route_km']:.0f} km."
                    )
            elif max(w, h) > lim["side_km"]:
                out.append(
                    f"{label}: the area is {w:.1f} × {h:.1f} km, "
                    f"the limit is {lim['side_km']:.0f} × {lim['side_km']:.0f} km."
                )
        return out

    def to_geojson(self) -> dict[str, Any]:
        def feat(geom: BaseGeometry, role: str) -> dict[str, Any]:
            return {
                "type": "Feature",
                "properties": {"role": role},
                "geometry": mapping(self.georef.geom_to_lonlat(geom)),
            }

        features = [feat(self.core_local, "selection"), feat(self.skirt_local, "skirt")]
        if self.route_local is not None:
            features.append(feat(self.route_local, "route"))
        return {
            "type": "FeatureCollection",
            "properties": {
                "mode": self.mode,
                "games": list(self.games),
                "corridor_m": self.corridor_m,
                "area_km2": round(self.area_km2, 4),
                "route_km": None if self.route_km is None else round(self.route_km, 3),
            },
            "features": features,
        }


def build_selection(req: SelectionRequest) -> Selection:
    if req.mode == "area":
        ring = list(req.polygon or [])
        if len(ring) >= 2 and ring[0] == ring[-1]:
            ring = ring[:-1]
        if len(ring) < 3:
            raise SelectionError("Draw an area with at least 3 corners.")
        poly_ll = Polygon(ring)
        if not poly_ll.is_valid or poly_ll.area == 0:
            raise SelectionError("The area crosses itself. Please draw it again without crossing lines.")
        c = poly_ll.centroid
        georef = Georef(lat0=round(c.y, 6), lon0=round(c.x, 6))
        core = georef.geom_to_local(poly_ll)
        route_local: LineString | None = None
        route_km: float | None = None
    else:
        line = list(req.route or [])
        if len(line) < 2:
            raise SelectionError("Pick at least a start and an end point for the road.")
        route_km = line_length_m(line) / 1000.0
        if route_km < 0.05:
            raise SelectionError("The road is too short. Pick points further apart.")
        line_ll = LineString(line)
        c = line_ll.envelope.centroid
        georef = Georef(lat0=round(c.y, 6), lon0=round(c.x, 6))
        projected = georef.geom_to_local(line_ll)
        assert isinstance(projected, LineString)
        route_local = projected
        core = route_local.simplify(2.0).buffer(req.corridor_m, quad_segs=8)
    skirt = core.buffer(SKIRT_M, quad_segs=4, join_style="mitre", mitre_limit=2.0)
    return Selection(
        mode=req.mode,
        games=tuple(dict.fromkeys(req.games)),
        corridor_m=req.corridor_m,
        georef=georef,
        core_local=core,
        skirt_local=skirt,
        route_local=route_local,
        route_km=route_km,
    )
