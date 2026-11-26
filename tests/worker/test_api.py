from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient

from forge_worker.api import create_app

from .conftest import AUTH, FAST, TOKEN, wait_for


def test_health_needs_no_token(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_everything_else_needs_token(client: TestClient) -> None:
    assert client.get("/jobs").status_code == 401
    assert client.get("/jobs", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/jobs", headers=AUTH).status_code == 200
    assert client.get(f"/jobs?token={TOKEN}").status_code == 200


def test_demo_job_runs_three_stages(client: TestClient) -> None:
    job = client.post("/jobs", json={"kind": "demo", "name": "t", "params": FAST}, headers=AUTH).json()
    assert job["state"] == "queued"
    assert job["stages"] == ["fetch", "process", "package"]
    done = wait_for(client, job["id"], {"done", "failed"})
    assert done["state"] == "done"
    assert done["progress"] == 1.0
    assert done["completed_stages"] == ["fetch", "process", "package"]
    logs = client.get(f"/jobs/{job['id']}/logs", headers=AUTH).json()
    assert any("Job finished" in entry["message"] for entry in logs)


def test_unknown_kind_is_400(client: TestClient) -> None:
    r = client.post("/jobs", json={"kind": "nope"}, headers=AUTH)
    assert r.status_code == 400


def test_failure_is_recorded_and_worker_survives(client: TestClient) -> None:
    params = {**FAST, "fail_at_stage": "process"}
    bad = client.post("/jobs", json={"params": params}, headers=AUTH).json()
    failed = wait_for(client, bad["id"], {"done", "failed"})
    assert failed["state"] == "failed"
    assert failed["completed_stages"] == ["fetch"]
    assert "Simulated failure" in failed["message"]
    good = client.post("/jobs", json={"params": FAST}, headers=AUTH).json()
    assert wait_for(client, good["id"], {"done", "failed"})["state"] == "done"


def test_cancel_running_job(client: TestClient) -> None:
    slow = {"steps": 100, "step_seconds": 0.02}
    job = client.post("/jobs", json={"params": slow}, headers=AUTH).json()
    wait_for(client, job["id"], {"running"})
    client.post(f"/jobs/{job['id']}/cancel", headers=AUTH)
    assert wait_for(client, job["id"], {"cancelled", "done"}, timeout=3)["state"] == "cancelled"


def test_cancel_queued_job(client: TestClient) -> None:
    slow = {"steps": 100, "step_seconds": 0.02}
    first = client.post("/jobs", json={"params": slow}, headers=AUTH).json()
    second = client.post("/jobs", json={"params": FAST}, headers=AUTH).json()
    r = client.post(f"/jobs/{second['id']}/cancel", headers=AUTH).json()
    assert r["state"] == "cancelled"
    client.post(f"/jobs/{first['id']}/cancel", headers=AUTH)


def test_rebuild_reuses_params(client: TestClient) -> None:
    job = client.post("/jobs", json={"name": "again", "params": FAST}, headers=AUTH).json()
    wait_for(client, job["id"], {"done"})
    new = client.post(f"/jobs/{job['id']}/rebuild", headers=AUTH).json()
    assert new["id"] != job["id"]
    assert new["params"] == FAST and new["name"] == "again"
    assert wait_for(client, new["id"], {"done"})["state"] == "done"


def test_interrupted_job_resumes_and_skips_finished_stages(db_path: Path) -> None:
    params = {"steps": 5, "step_seconds": 0.05}
    with TestClient(create_app(db_path, TOKEN)) as c:
        job = c.post("/jobs", json={"params": params}, headers=AUTH).json()
        # wait until the first stage is finished, then close the worker mid-job
        while "fetch" not in c.get(f"/jobs/{job['id']}", headers=AUTH).json()["completed_stages"]:
            time.sleep(0.01)
    with TestClient(create_app(db_path, TOKEN)) as c:
        done = wait_for(c, job["id"], {"done", "failed", "cancelled"})
        assert done["state"] == "done"
        msgs = [e["message"] for e in c.get(f"/jobs/{job['id']}/logs", headers=AUTH).json()]
        assert any("resume on next start" in m for m in msgs)
        assert any("'Fetch' already done, skipping" in m for m in msgs)


def test_events_stream_delivers_job_updates(client: TestClient) -> None:
    received: list[dict[str, object]] = []

    def submit_later() -> None:
        client.post("/jobs", json={"params": FAST}, headers=AUTH)

    with client.stream("GET", f"/events?token={TOKEN}") as stream:
        threading.Timer(0.2, submit_later).start()
        for line in stream.iter_lines():
            if line.startswith("data: "):
                event = json.loads(line[6:])
                received.append(event)
                if event["type"] == "job" and event["job"]["state"] == "done":
                    break
    assert any(e["type"] == "log" for e in received)
    assert received[-1]["job"]["state"] == "done"  # type: ignore[index]
