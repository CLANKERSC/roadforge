"""Source adapter interface.

Every data source (elevation, imagery, vectors, street level) is an adapter that declares
what it covers, how sharp it is and under which licence, and knows how to download the
pieces a selection needs into the shared download cache.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Literal, TypeVar

from shapely.geometry.base import BaseGeometry

from forge_worker.cache import CachedFile, DownloadCache
from forge_worker.http import Http
from forge_worker.selection import Selection

Layer = Literal["elevation", "imagery", "vectors", "streetlevel"]
LAYERS: tuple[Layer, ...] = ("elevation", "imagery", "vectors", "streetlevel")


@dataclass(frozen=True)
class SourceInfo:
    id: str
    layer: Layer
    name: str
    resolution_m: float | None  # ground resolution; None for vector data
    licence: str
    licence_url: str
    attribution: str  # exact line that goes into CREDITS.txt
    redistributable_derived: bool  # may derived data (meshes, textures) ship inside a mod?
    coverage: BaseGeometry | None  # lon/lat polygon; None = whole world
    coverage_label: str
    est_mb_per_km2: float
    quality_note: str = ""  # shown to the user, e.g. a warning for coarse fallbacks


@dataclass(frozen=True)
class FetchedFile:
    file: CachedFile
    meta: dict[str, Any] = field(default_factory=dict)


class SourceUnavailable(RuntimeError):
    """This source can't serve the selection (no data there, service down). Try the next one."""


@dataclass
class FetchContext:
    cache: DownloadCache
    http: Http
    report: Callable[[float, str], None]
    log: Callable[[str, str], None]
    check_cancelled: Callable[[], None]


class Source(ABC):
    info: SourceInfo

    def covers(self, sel: Selection) -> bool:
        cov = self.info.coverage
        return cov is None or bool(cov.contains(sel.skirt_lonlat))

    def estimate_mb(self, sel: Selection) -> float:
        return self.info.est_mb_per_km2 * float(sel.skirt_local.area) / 1e6

    @abstractmethod
    def fetch(self, sel: Selection, ctx: FetchContext) -> list[FetchedFile]:
        """Download (or reuse from cache) everything the selection needs."""


T = TypeVar("T")
R = TypeVar("R")


def fetch_parallel(
    items: Sequence[T], work: Callable[[T], R], ctx: FetchContext, label: str, workers: int = 4
) -> list[R]:
    """Run `work` over items on a few threads, reporting progress and honouring Cancel."""
    results: dict[int, R] = {}
    if not items:
        return []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(work, item): i for i, item in enumerate(items)}
        try:
            for done, fut in enumerate(as_completed(futures), start=1):
                ctx.check_cancelled()
                results[futures[fut]] = fut.result()
                ctx.report(done / len(items), f"{label}: {done} of {len(items)}")
        except BaseException:
            for f in futures:
                f.cancel()
            raise
    return [results[i] for i in range(len(items))]


def unique(items: Iterable[T]) -> list[T]:
    return list(dict.fromkeys(items))
