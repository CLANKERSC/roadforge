"""Run the real worker with a fake internet, for UI tests: python -m tests.e2e.fake_worker <data_dir>."""

from __future__ import annotations

import sys
from pathlib import Path

import uvicorn

from forge_worker.api import create_app
from forge_worker.env import WorkerEnv
from tests.worker.fakes import FakeHttp

PORT = 8765
TOKEN = "e2e-token"


def main() -> None:
    data_dir = Path(sys.argv[1])
    data_dir.mkdir(parents=True, exist_ok=True)
    env = WorkerEnv(data_dir)
    env.__dict__["http"] = FakeHttp(data_dir / "scene.tif")
    uvicorn.run(create_app(data_dir, TOKEN, env), host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
