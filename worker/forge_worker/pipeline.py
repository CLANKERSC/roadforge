"""Pipeline definitions: a job kind is an ordered list of named stages.

Each stage is a function taking a StageContext. Stages report progress, write logs and
check for cancellation through the context. Completed stages are remembered per job, so a
resumed job skips them (in later milestones each stage also caches its files on disk).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol


class JobCancelled(Exception):
    """Raised inside a stage when the user pressed Cancel."""


class JobInterrupted(Exception):
    """Raised inside a stage when the worker is shutting down; the job resumes on next start."""


class StageContext(Protocol):
    job_id: str
    params: dict[str, Any]

    def report(self, fraction: float, message: str = "") -> None: ...
    def log(self, message: str, level: str = "info") -> None: ...
    def check_cancelled(self) -> None: ...
    def sleep(self, seconds: float) -> None: ...


StageFn = Callable[[StageContext], None]


@dataclass(frozen=True)
class Stage:
    id: str
    label: str
    run: StageFn


@dataclass(frozen=True)
class Pipeline:
    kind: str
    label: str
    stages: tuple[Stage, ...]

    @property
    def stage_ids(self) -> list[str]:
        return [s.id for s in self.stages]


_REGISTRY: dict[str, Pipeline] = {}


def register(pipeline: Pipeline) -> Pipeline:
    if pipeline.kind in _REGISTRY:
        raise ValueError(f"pipeline {pipeline.kind!r} already registered")
    _REGISTRY[pipeline.kind] = pipeline
    return pipeline


def get_pipeline(kind: str) -> Pipeline:
    try:
        return _REGISTRY[kind]
    except KeyError:
        raise KeyError(f"unknown job kind {kind!r}") from None


def pipelines() -> list[Pipeline]:
    return list(_REGISTRY.values())
