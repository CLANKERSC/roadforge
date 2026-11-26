"""Local HTTP API for the desktop UI. Binds to 127.0.0.1 only and requires a per-run token."""

from __future__ import annotations

import asyncio
import hmac
import json
import queue
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from forge_worker import __version__
from forge_worker import stages as _stages  # noqa: F401  (registers pipelines)
from forge_worker.brand import brand
from forge_worker.events import EventBus
from forge_worker.pipeline import pipelines
from forge_worker.runner import JobRunner
from forge_worker.store import JobStore

# Origins of the Tauri webview (Windows/Linux/macOS) and the Vite dev server.
ALLOWED_ORIGINS = [
    "tauri://localhost",
    "http://tauri.localhost",
    "https://tauri.localhost",
    "http://localhost:1420",
    "http://127.0.0.1:1420",
]


class CreateJob(BaseModel):
    kind: str = Field(default="demo", max_length=64)
    name: str = Field(default="Demo build", min_length=1, max_length=120)
    params: dict[str, Any] = Field(default_factory=dict)


def create_app(db_path: Path, token: str) -> FastAPI:
    bus = EventBus()
    store = JobStore(db_path)
    runner = JobRunner(store, bus)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        runner.start()
        yield
        runner.stop()
        store.close()

    app = FastAPI(title=f"{brand()['name']} worker", version=__version__, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )
    app.state.runner = runner
    app.state.store = store

    def require_token(request: Request) -> None:
        header = request.headers.get("authorization", "")
        supplied = header.removeprefix("Bearer ").strip() if header.startswith("Bearer ") else ""
        # EventSource cannot send headers, so the SSE endpoint also accepts ?token=
        supplied = supplied or request.query_params.get("token", "")
        if not hmac.compare_digest(supplied.encode(), token.encode()):
            raise HTTPException(status_code=401, detail="missing or wrong token")

    auth = [Depends(require_token)]

    @app.get("/health")
    def health() -> dict[str, Any]:
        """Unauthenticated liveness probe. Reveals nothing beyond name and version."""
        return {"status": "ok", "app": brand()["name"], "version": __version__}

    @app.get("/pipelines", dependencies=auth)
    def list_pipelines() -> list[dict[str, Any]]:
        return [
            {"kind": p.kind, "label": p.label, "stages": [{"id": s.id, "label": s.label} for s in p.stages]}
            for p in pipelines()
        ]

    @app.post("/jobs", dependencies=auth, status_code=201)
    def create_job(body: CreateJob) -> dict[str, Any]:
        try:
            return runner.submit(body.kind, body.name, body.params).to_dict()
        except KeyError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @app.get("/jobs", dependencies=auth)
    def list_jobs(limit: int = 100) -> list[dict[str, Any]]:
        return [j.to_dict() for j in store.list_jobs(min(max(limit, 1), 500))]

    def _get(job_id: str) -> dict[str, Any]:
        job = store.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return job.to_dict()

    @app.get("/jobs/{job_id}", dependencies=auth)
    def get_job(job_id: str) -> dict[str, Any]:
        return _get(job_id)

    @app.get("/jobs/{job_id}/logs", dependencies=auth)
    def get_logs(job_id: str, after: int = 0) -> list[dict[str, Any]]:
        _get(job_id)
        return store.logs(job_id, after)

    @app.post("/jobs/{job_id}/cancel", dependencies=auth)
    def cancel_job(job_id: str) -> dict[str, Any]:
        _get(job_id)
        return runner.cancel(job_id).to_dict()

    @app.post("/jobs/{job_id}/rebuild", dependencies=auth, status_code=201)
    def rebuild_job(job_id: str) -> dict[str, Any]:
        old = _get(job_id)
        return runner.submit(old["kind"], old["name"], old["params"]).to_dict()

    @app.get("/events", dependencies=auth)
    async def events(request: Request) -> StreamingResponse:
        sub = bus.subscribe()

        async def stream() -> AsyncIterator[str]:
            try:
                yield "retry: 2000\n\n"
                idle = 0
                while not await request.is_disconnected():
                    try:
                        # short timeout so shutdown and disconnects are noticed quickly
                        event = await asyncio.to_thread(sub.get, True, 0.5)
                    except queue.Empty:
                        idle += 1
                        if idle >= 30:
                            idle = 0
                            yield ": keep-alive\n\n"
                        continue
                    idle = 0
                    yield f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"
            finally:
                bus.unsubscribe(sub)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app
