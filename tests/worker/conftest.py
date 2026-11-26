from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from forge_worker.api import create_app
from forge_worker.env import WorkerEnv

from .fakes import FakeHttp, make_scene

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
FAST = {"steps": 4, "step_seconds": 0.01}


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests never sit out the real pauses between tries on busy map servers."""
    from forge_worker.sources import osm

    monkeypatch.setattr(osm, "ROUND_WAITS", (0, 0))


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "data"


@pytest.fixture
def fake_http(tmp_path: Path) -> FakeHttp:
    scene = tmp_path / "scene.tif"
    make_scene(scene)
    return FakeHttp(scene)


@pytest.fixture
def env(db_path: Path, fake_http: FakeHttp) -> WorkerEnv:
    env = WorkerEnv(db_path)
    env.__dict__["http"] = fake_http  # no test ever touches the real internet
    return env


@pytest.fixture
def client(db_path: Path, env: WorkerEnv) -> Iterator[TestClient]:
    with TestClient(create_app(db_path, TOKEN, env)) as c:
        yield c


def wait_for(client: TestClient, job_id: str, states: set[str], timeout: float = 10) -> dict[str, Any]:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        job: dict[str, Any] = client.get(f"/jobs/{job_id}", headers=AUTH).json()
        if job["state"] in states:
            return job
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} never reached {states}; last: {job}")
