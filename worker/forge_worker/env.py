"""Things every job shares: data folder, download cache, HTTP client."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from forge_worker.cache import DownloadCache
from forge_worker.http import Http
from forge_worker.services import RATE_LIMITS


@dataclass
class WorkerEnv:
    data_dir: Path

    @cached_property
    def cache(self) -> DownloadCache:
        return DownloadCache(self.data_dir / "cache")

    @cached_property
    def http(self) -> Http:
        return Http(min_interval=RATE_LIMITS)

    def build_dir(self, job_id: str) -> Path:
        path = self.data_dir / "builds" / job_id
        path.mkdir(parents=True, exist_ok=True)
        return path
