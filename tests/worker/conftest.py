from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from forge_worker.api import create_app

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
FAST = {"steps": 4, "step_seconds": 0.01}


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "jobs.sqlite3"


@pytest.fixture
def client(db_path: Path) -> Iterator[TestClient]:
    with TestClient(create_app(db_path, TOKEN)) as c:
        yield c


def wait_for(client: TestClient, job_id: str, states: set[str], timeout: float = 10) -> dict[str, Any]:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        job: dict[str, Any] = client.get(f"/jobs/{job_id}", headers=AUTH).json()
        if job["state"] in states:
            return job
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} never reached {states}; last: {job}")
