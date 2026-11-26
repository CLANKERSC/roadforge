"""Small online helpers for the UI: place search and routing along roads.

Both go through the worker so requests carry only the app name as User-Agent and are
rate limited. Neither needs an account.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from forge_worker.geo import line_length_m
from forge_worker.http import Http, HttpError

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
# Public OSRM demo server: fine for occasional single-user routing (see docs/ASSUMPTIONS.md).
OSRM_URL = "https://router.project-osrm.org/route/v1/driving"

RATE_LIMITS = {
    "nominatim.openstreetmap.org": 1.1,  # usage policy: at most 1 request per second
    "router.project-osrm.org": 1.1,
    "overpass-api.de": 0.5,
}

LonLat = tuple[float, float]


@dataclass(frozen=True)
class Place:
    name: str
    lat: float
    lon: float
    bbox: tuple[float, float, float, float] | None  # west, south, east, north

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "lat": self.lat, "lon": self.lon, "bbox": self.bbox}


def search_places(http: Http, query: str, lang: str = "en") -> list[Place]:
    data = http.get_json(
        NOMINATIM_URL,
        params={"q": query, "format": "jsonv2", "limit": 6, "accept-language": lang},
        timeout=(5, 15),
    )
    places = []
    for item in data:
        bb = item.get("boundingbox")  # [south, north, west, east] as strings
        bbox = (float(bb[2]), float(bb[0]), float(bb[3]), float(bb[1])) if bb and len(bb) == 4 else None
        places.append(Place(item.get("display_name", "?"), float(item["lat"]), float(item["lon"]), bbox))
    return places


@dataclass(frozen=True)
class Route:
    coords: list[LonLat]
    km: float
    routed: bool  # False = straight lines, because the routing service was unreachable
    warning: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"coords": self.coords, "km": self.km, "routed": self.routed, "warning": self.warning}


def route_along_roads(http: Http, waypoints: list[LonLat]) -> Route:
    """Follow real roads through the waypoints. Falls back to straight lines if offline."""
    path = ";".join(f"{lon:.6f},{lat:.6f}" for lon, lat in waypoints)
    try:
        data = http.get_json(
            f"{OSRM_URL}/{path}",
            params={"overview": "full", "geometries": "geojson", "continue_straight": "false"},
            timeout=(5, 30),
        )
        if data.get("code") != "Ok" or not data.get("routes"):
            raise HttpError(OSRM_URL, 200, str(data.get("message") or data.get("code")))
        coords = [(float(x), float(y)) for x, y in data["routes"][0]["geometry"]["coordinates"]]
        return Route(coords, line_length_m(coords) / 1000.0, routed=True)
    except HttpError as exc:
        return Route(
            list(waypoints),
            line_length_m(waypoints) / 1000.0,
            routed=False,
            warning=f"Could not follow roads ({exc}). Showing straight lines between your points.",
        )
