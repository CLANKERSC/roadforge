from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from .conftest import AUTH, wait_for
from .fakes import FakeHttp
from .test_geo_selection import square

PRAGUE_AREA = {"mode": "area", "polygon": square(14.44, 50.08, 1.0), "games": ["ac", "beamng"]}
NZ_AREA = {"mode": "area", "polygon": square(168.66, -45.03, 1.0), "games": ["ac"]}


def build(client: TestClient, selection: dict[str, Any], name: str = "Test map") -> dict[str, Any]:
    body = {"kind": "build", "name": name, "params": {"selection": selection, "name": name}}
    job = client.post("/jobs", json=body, headers=AUTH).json()
    return wait_for(client, job["id"], {"done", "failed", "cancelled"}, timeout=30)


def manifest(data_dir: Path, job_id: str) -> dict[str, Any]:
    return json.loads((data_dir / "builds" / job_id / "fetch_manifest.json").read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def test_plan_reports_sources_sizes_and_ids(client: TestClient) -> None:
    plan = client.post("/plan", json={"selection": PRAGUE_AREA, "name": "Můj okruh"}, headers=AUTH).json()
    assert plan["ok"] and plan["problems"] == []
    assert 0.98 < plan["area_km2"] < 1.02
    assert plan["layers"]["elevation"]["chosen"]["id"] == "cuzk_dmr5g"
    assert plan["layers"]["elevation"]["chosen"]["resolution_m"] == 2.0
    assert plan["layers"]["elevation"]["fallbacks"][0]["id"] == "copernicus_dem_glo30"
    assert plan["ids"] == {"ac": "muj_okruh", "beamng": "muj_okruh"}
    assert plan["est_download_mb"] > 0 and plan["est_minutes"] > 0
    world = client.post("/plan", json={"selection": NZ_AREA}, headers=AUTH).json()
    assert world["layers"]["elevation"]["chosen"]["id"] == "copernicus_dem_glo30"
    assert "less accurate" in world["layers"]["elevation"]["chosen"]["note"]


def test_plan_explains_problems(client: TestClient) -> None:
    too_big = {"mode": "area", "polygon": square(14.44, 50.08, 20.0), "games": ["ac"]}
    plan = client.post("/plan", json={"selection": too_big}, headers=AUTH).json()
    assert not plan["ok"] and "limit" in plan["problems"][0]
    line = {"mode": "area", "polygon": [[14, 50], [14.1, 50]]}
    assert "3 corners" in client.post("/plan", json={"selection": line}, headers=AUTH).json()["problems"][0]


def test_czech_build_downloads_national_data(client: TestClient, db_path: Path) -> None:
    job = build(client, PRAGUE_AREA, "Praha test")
    assert job["state"] == "done", job["message"]
    workdir = db_path / "builds" / job["id"]
    for name in ["selection.geojson", "georef.json", "build.json", "fetch_manifest.json", "CREDITS.txt"]:
        assert (workdir / name).is_file(), name
    layers = manifest(db_path, job["id"])["layers"]
    assert layers["elevation"]["source"] == "cuzk_dmr5g"
    assert layers["imagery"]["source"] == "cuzk_ortofoto"
    assert layers["vectors"]["source"] == "osm_overpass"
    for layer in layers.values():
        assert layer["files"] and all((db_path / "cache" / f["path"]).is_file() for f in layer["files"])
        assert all(len(f["sha256"]) == 64 for f in layer["files"])
    credits = (workdir / "CREDITS.txt").read_text(encoding="utf-8")
    assert "ČÚZK" in credits and "OpenStreetMap contributors" in credits


def test_build_anywhere_uses_global_data(client: TestClient, db_path: Path) -> None:
    job = build(client, NZ_AREA)
    assert job["state"] == "done", job["message"]
    layers = manifest(db_path, job["id"])["layers"]
    assert layers["elevation"]["source"] == "copernicus_dem_glo30"
    assert layers["imagery"]["source"] == "sentinel2_l2a"
    assert layers["imagery"]["files"][0]["meta"]["scene"] == "S2_FAKE_33UVR"


def test_falls_back_to_global_when_national_data_is_empty(
    client: TestClient, db_path: Path, fake_http: FakeHttp
) -> None:
    fake_http.cuzk_has_data = False
    job = build(client, PRAGUE_AREA)
    assert job["state"] == "done", job["message"]
    layers = manifest(db_path, job["id"])["layers"]
    assert layers["elevation"]["source"] == "copernicus_dem_glo30"
    assert "ČÚZK" in layers["elevation"]["warnings"][0]
    assert layers["imagery"]["source"] == "sentinel2_l2a"
    logs = client.get(f"/jobs/{job['id']}/logs", headers=AUTH).json()
    assert any(entry["level"] == "warning" and "not usable here" in entry["message"] for entry in logs)


def test_second_build_reuses_cache_and_is_deterministic(
    client: TestClient, db_path: Path, fake_http: FakeHttp
) -> None:
    first = build(client, PRAGUE_AREA)
    calls_after_first = len(fake_http.calls)
    second = client.post(f"/jobs/{first['id']}/rebuild", headers=AUTH).json()
    second = wait_for(client, second["id"], {"done", "failed"}, timeout=30)
    assert second["state"] == "done"
    assert len(fake_http.calls) == calls_after_first  # nothing downloaded twice
    assert manifest(db_path, first["id"]) == manifest(db_path, second["id"])
    a = (db_path / "builds" / first["id"] / "selection.geojson").read_bytes()
    b = (db_path / "builds" / second["id"] / "selection.geojson").read_bytes()
    assert a == b


def test_failed_download_fails_the_job_with_a_clear_message(client: TestClient, fake_http: FakeHttp) -> None:
    fake_http.offline_hosts = {"overpass"}
    job = build(client, PRAGUE_AREA)
    assert job["state"] == "failed"
    assert "OpenStreetMap servers are busy or out of reach" in job["message"]
    assert "Press Rebuild" in job["message"]
    fake_http.offline_hosts = set()
    again = client.post(f"/jobs/{job['id']}/rebuild", headers=AUTH).json()
    assert wait_for(client, again["id"], {"done", "failed"}, timeout=30)["state"] == "done"


def test_busy_map_server_falls_back_to_a_mirror(client: TestClient, fake_http: FakeHttp) -> None:
    fake_http.busy_hosts = {"overpass-api.de"}  # the main server answers "429 Too Many Requests"
    job = build(client, PRAGUE_AREA)
    assert job["state"] == "done", job["message"]
    logs = client.get(f"/jobs/{job['id']}/logs", headers=AUTH).json()
    assert any("overpass.private.coffee" in entry["message"] for entry in logs)
    assert any("overpass.private.coffee" in url for url in fake_http.calls)


def test_selection_over_limit_fails_in_first_stage(client: TestClient) -> None:
    too_big = {"mode": "area", "polygon": square(14.44, 50.08, 20.0), "games": ["ac"]}
    job = build(client, too_big)
    assert job["state"] == "failed" and "limit" in job["message"]
    assert job["completed_stages"] == []


def test_search_and_route(client: TestClient, fake_http: FakeHttp) -> None:
    places = client.get("/search?q=brno", headers=AUTH).json()["places"]
    assert places[0]["name"].startswith("Brno") and places[0]["bbox"] == [16.4, 49.1, 16.8, 49.3]
    body = {"waypoints": [[14.40, 50.08], [14.42, 50.09]]}
    route = client.post("/route", json=body, headers=AUTH).json()
    assert route["routed"] and len(route["coords"]) == 3 and 1.5 < route["km"] < 2.5
    fake_http.offline_hosts = {"osrm"}
    offline = client.post("/route", json=body, headers=AUTH).json()
    assert not offline["routed"] and "straight lines" in offline["warning"]
    assert len(offline["coords"]) == 2
    fake_http.offline_hosts = {"nominatim"}
    assert client.get("/search?q=brno", headers=AUTH).status_code == 502


def test_cancel_works_while_a_download_hangs(client: TestClient, fake_http: FakeHttp) -> None:
    fake_http.hang_hosts = {"overpass"}  # the map server accepts the request and then goes silent
    body = {"kind": "build", "name": "Hang", "params": {"selection": PRAGUE_AREA, "name": "Hang"}}
    job = client.post("/jobs", json=body, headers=AUTH).json()
    wait_for(client, job["id"], {"running"}, timeout=10)
    time.sleep(1.0)
    client.post(f"/jobs/{job['id']}/cancel", headers=AUTH)
    try:
        assert wait_for(client, job["id"], {"cancelled", "failed", "done"}, timeout=5)["state"] == "cancelled"
    finally:
        fake_http.release.set()
