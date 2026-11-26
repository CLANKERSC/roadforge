"""Download cache: files on disk plus a SQLite index with checksums.

Keys are stable per source (for example a tile's grid position), so two overlapping builds
reuse each other's downloads and a failed build resumes where it stopped.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    source TEXT NOT NULL,
    key TEXT NOT NULL,
    path TEXT NOT NULL,
    bytes INTEGER NOT NULL CHECK (bytes >= 0),
    sha256 TEXT NOT NULL,
    fetched_at REAL NOT NULL,
    PRIMARY KEY (source, key)
);
"""


@dataclass(frozen=True)
class CachedFile:
    source: str
    key: str
    path: Path
    bytes: int
    sha256: str
    from_cache: bool


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe(part: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in part)


class DownloadCache:
    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(root / "index.sqlite3"), check_same_thread=False)
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def lookup(self, source: str, key: str) -> CachedFile | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT path, bytes, sha256 FROM files WHERE source = ? AND key = ?", (source, key)
            ).fetchone()
        if not row:
            return None
        path = self.root / row[0]
        # cheap integrity check: file exists with the recorded size
        if not path.is_file() or path.stat().st_size != row[1]:
            return None
        return CachedFile(source, key, path, row[1], row[2], from_cache=True)

    def get_or_fetch(self, source: str, key: str, ext: str, fetch: Callable[[Path], None]) -> CachedFile:
        """Return the cached file, or call `fetch(tmp_path)` to create it, then index it."""
        hit = self.lookup(source, key)
        if hit:
            return hit
        rel = Path(_safe(source)) / f"{_safe(key)}{ext}"
        dest = self.root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + f".part{threading.get_ident()}")
        try:
            fetch(tmp)
            digest = sha256_file(tmp)
            size = tmp.stat().st_size
            os.replace(tmp, dest)  # atomic: a crash never leaves a half file under the final name
        finally:
            tmp.unlink(missing_ok=True)
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO files (source, key, path, bytes, sha256, fetched_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (source, key, rel.as_posix(), size, digest, time.time()),
            )
            self._conn.commit()
        return CachedFile(source, key, dest, size, digest, from_cache=False)

    def total_bytes(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COALESCE(SUM(bytes), 0) FROM files").fetchone()
        return int(row[0])
