"""Runs queued jobs one at a time on a background thread."""

from __future__ import annotations

import threading
import time
import traceback
from typing import Any

from forge_worker.env import WorkerEnv
from forge_worker.events import EventBus
from forge_worker.pipeline import JobCancelled, JobInterrupted, get_pipeline
from forge_worker.store import FINAL_STATES, Job, JobStore


class _Context:
    def __init__(self, runner: JobRunner, job: Job, stage_index: int, stage_count: int) -> None:
        self._runner = runner
        self.job_id = job.id
        self.params: dict[str, Any] = job.params
        self.env = runner.env
        self.workdir = runner.env.build_dir(job.id)
        self._stage_index = stage_index
        self._stage_count = stage_count
        self._last_publish = 0.0

    def report(self, fraction: float, message: str = "") -> None:
        fraction = min(max(fraction, 0.0), 1.0)
        overall = (self._stage_index + fraction) / self._stage_count
        job = self._runner.store.update(
            self.job_id, stage_progress=fraction, progress=overall, message=message
        )
        now = time.monotonic()
        if fraction >= 1.0 or now - self._last_publish > 0.1:  # throttle UI updates
            self._last_publish = now
            self._runner.publish_job(job)

    def log(self, message: str, level: str = "info") -> None:
        self._runner.log(self.job_id, message, level)

    def check_cancelled(self) -> None:
        if self._runner.stopping:
            raise JobInterrupted()
        job = self._runner.store.get(self.job_id)
        if job is None or job.cancel_requested:
            raise JobCancelled()

    def sleep(self, seconds: float) -> None:
        """Sleep in small slices so Cancel reacts quickly."""
        end = time.monotonic() + seconds
        while (left := end - time.monotonic()) > 0:
            self.check_cancelled()
            self._runner.wake.wait(min(left, 0.05))


class JobRunner:
    def __init__(self, store: JobStore, bus: EventBus, env: WorkerEnv) -> None:
        self.env = env
        self.store = store
        self.bus = bus
        self.wake = threading.Event()  # set when a job is queued or cancelled
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        for job_id in self.store.requeue_interrupted():
            self.log(job_id, "Worker restarted: resuming, finished stages are skipped", "warning")
        self._thread = threading.Thread(target=self._loop, name="job-runner", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self.wake.set()
        if self._thread:
            self._thread.join(timeout)

    # -- API used by the HTTP layer ------------------------------------------
    def submit(self, kind: str, name: str, params: dict[str, Any]) -> Job:
        pipeline = get_pipeline(kind)
        job = self.store.create(kind, name, params, pipeline.stage_ids)
        self.log(job.id, f"Queued '{name}' ({pipeline.label})")
        self.publish_job(job)
        self.wake.set()
        return job

    def cancel(self, job_id: str) -> Job:
        job = self.store.get(job_id)
        if job is None:
            raise KeyError(job_id)
        if job.state in FINAL_STATES:
            return job
        if job.state == "queued":
            job = self.store.update(
                job_id,
                state="cancelled",
                cancel_requested=True,
                message="Cancelled",
                finished_at=time.time(),
            )
            self.log(job_id, "Cancelled before it started")
        else:
            job = self.store.update(job_id, cancel_requested=True, message="Cancelling…")
        self.publish_job(job)
        self.wake.set()
        return job

    # -- events --------------------------------------------------------------
    def publish_job(self, job: Job) -> None:
        self.bus.publish({"type": "job", "job": job.to_dict()})

    def log(self, job_id: str, message: str, level: str = "info") -> None:
        entry = self.store.log(job_id, message, level)
        self.bus.publish({"type": "log", "log": entry})

    # -- worker loop ---------------------------------------------------------
    def _loop(self) -> None:
        while not self._stop.is_set():
            job = self.store.next_queued()
            if job is None:
                self.wake.wait(1.0)
                self.wake.clear()
                continue
            self._run(job)

    def _run(self, job: Job) -> None:
        pipeline = get_pipeline(job.kind)
        count = len(pipeline.stages)
        job = self.store.update(
            job.id, state="running", started_at=job.started_at or time.time(), message="Starting"
        )
        self.publish_job(job)
        done = list(job.completed_stages)
        try:
            for index, stage in enumerate(pipeline.stages):
                if stage.id in done:
                    self.log(job.id, f"Stage '{stage.label}' already done, skipping")
                    continue
                job = self.store.update(
                    job.id,
                    stage=stage.id,
                    stage_progress=0.0,
                    progress=index / count,
                    message=f"{stage.label}…",
                )
                self.publish_job(job)
                ctx = _Context(self, job, index, count)
                ctx.check_cancelled()
                stage.run(ctx)
                done.append(stage.id)
                job = self.store.update(job.id, completed_stages=done)
            # the last stage's closing words are the result line the user reads
            last = self.store.get(job.id)
            closing = last.message if last and last.message and not last.message.endswith("…") else "Done"
            job = self.store.update(
                job.id,
                state="done",
                stage=None,
                progress=1.0,
                stage_progress=1.0,
                message=closing,
                finished_at=time.time(),
            )
            self.log(job.id, "Job finished")
        except JobInterrupted:
            # leave state 'running'; requeue_interrupted() picks it up on next start
            self.store.update(job.id, completed_stages=done, message="Interrupted, will resume")
            self.log(job.id, "Worker stopped mid-job; it will resume on next start", "warning")
            return
        except JobCancelled:
            job = self.store.update(job.id, state="cancelled", message="Cancelled", finished_at=time.time())
            self.log(job.id, "Cancelled by user", "warning")
        except Exception as exc:  # noqa: BLE001 - a stage failed: record it, keep the worker alive
            job = self.store.update(
                job.id,
                state="failed",
                message=f"Failed: {exc}",
                error=traceback.format_exc(),
                finished_at=time.time(),
            )
            self.log(job.id, f"Failed: {exc}", "error")
        self.publish_job(job)
