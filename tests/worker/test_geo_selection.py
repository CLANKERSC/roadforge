from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from forge_worker.geo import Georef, line_length_m
from forge_worker.naming import ac_track_id, beamng_level_id
from forge_worker.selection import SKIRT_M, SelectionError, SelectionRequest, build_selection


def square(lon: float, lat: float, km: float) -> list[tuple[float, float]]:
    dlat = km / 111.32 / 2
    dlon = dlat / math.cos(math.radians(lat))
    return [
        (lon - dlon, lat - dlat),
        (lon + dlon, lat - dlat),
        (lon + dlon, lat + dlat),
        (lon - dlon, lat + dlat),
    ]


@pytest.mark.parametrize(
    ("lat", "lon"), [(50.0755, 14.4378), (-45.03, 168.66), (64.15, -21.94), (0.0, 0.0), (35.68, 139.69)]
)
def test_projection_round_trip_under_1cm(lat: float, lon: float) -> None:
    g = Georef(lat0=lat, lon0=lon)
    for dx, dy in [(0, 0), (9000, 9000), (-9000, 7000), (5000, -9000)]:
        lon2, lat2 = g.to_lonlat(dx, dy)
        x, y = g.to_local(lon2, lat2)
        assert math.hypot(x - dx, y - dy) < 0.01
    assert math.hypot(*g.to_local(lon, lat)) < 0.01  # origin is the centre


def test_georef_file_round_trip(tmp_path) -> None:  # type: ignore[no-untyped-def]
    g = Georef(lat0=49.5, lon0=17.25)
    g.write(tmp_path / "georef.json")
    assert Georef.read(tmp_path / "georef.json") == g


def test_names_are_game_safe() -> None:
    assert ac_track_id("Žďár nad Sázavou – okruh #1!") == "zdar_nad_sazavou_okruh_1"
    assert len(ac_track_id("x" * 80)) == 32
    assert ac_track_id("!!!") == "track"
    assert beamng_level_id("2 Fast") == "level_2_fast"


def test_area_selection_size_and_skirt() -> None:
    sel = build_selection(SelectionRequest(mode="area", polygon=square(14.44, 50.08, 2.0)))
    assert sel.area_km2 == pytest.approx(4.0, rel=0.01)
    assert sel.problems() == []
    minx, _, maxx, _ = sel.core_local.bounds
    sminx, _, smaxx, _ = sel.skirt_local.bounds
    assert sminx == pytest.approx(minx - SKIRT_M, abs=1) and smaxx == pytest.approx(maxx + SKIRT_M, abs=1)
    roles = [f["properties"]["role"] for f in sel.to_geojson()["features"]]
    assert roles == ["selection", "skirt"]


def test_area_limits_per_game() -> None:
    poly = square(14.44, 50.08, 12.0)
    assert build_selection(SelectionRequest(mode="area", polygon=poly, games=["beamng"])).problems() == []
    problems = build_selection(SelectionRequest(mode="area", polygon=poly, games=["ac", "beamng"])).problems()
    assert len(problems) == 1 and "Assetto Corsa" in problems[0] and "10 × 10" in problems[0]


def test_route_selection_corridor() -> None:
    route = [(14.40, 50.08), (14.43, 50.08)]
    sel = build_selection(SelectionRequest(mode="route", route=route, corridor_m=300))
    km = line_length_m(route) / 1000
    assert sel.route_km == pytest.approx(km)
    # corridor = length x 600 m plus two half-circle caps
    assert sel.area_km2 == pytest.approx(km * 0.6 + math.pi * 0.3**2, rel=0.02)


def test_route_too_long_for_ac() -> None:
    sel = build_selection(SelectionRequest(mode="route", route=[(14.0, 50.0), (16.0, 50.0)]))
    assert "100 km" in sel.problems()[0]


def test_bad_selections_are_explained() -> None:
    with pytest.raises(SelectionError, match="at least 3"):
        build_selection(SelectionRequest(mode="area", polygon=[(14, 50), (14.1, 50)]))
    bow_tie = [(14.0, 50.0), (14.1, 50.1), (14.1, 50.0), (14.0, 50.1)]
    with pytest.raises(SelectionError, match="crosses itself"):
        build_selection(SelectionRequest(mode="area", polygon=bow_tie))
    with pytest.raises(SelectionError, match="start and an end"):
        build_selection(SelectionRequest(mode="route", route=[(14, 50)]))
    with pytest.raises(ValidationError):
        SelectionRequest(mode="route", route=[(14, 50), (15, 50)], corridor_m=5000)
    with pytest.raises(ValidationError):
        SelectionRequest(mode="area", polygon=[(500, 50), (14, 50), (14, 51)])
