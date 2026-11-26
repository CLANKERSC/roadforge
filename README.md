# RoadForge

Turn a real place into a drivable map for **Assetto Corsa** (first) and **BeamNG.drive** (second).
You draw a road or an area on a world map; the app builds terrain, roads and the world from
open data and exports a ready-to-install ZIP for each game.

> Maps made with this app are approximations for games, **not for navigation**.

"RoadForge" is a working name. To rename the app, see [Renaming](#renaming).

## Status

**Milestone M0 – skeleton** (this version): desktop window with a world map, a local engine
("worker") and a build queue that runs a pretend 3-stage build with live progress, cancel,
rebuild and logs. No real map building yet; that starts in M1. See [CHANGELOG.md](CHANGELOG.md).

## Install on Windows (the normal way)

1. Open the repository on GitHub → **Actions** → the latest green **CI** run.
2. At the bottom, under **Artifacts**, download **RoadForge-windows-installer** and unzip it.
3. Double-click `RoadForge_…_x64-setup.exe` (or the `.msi`). Windows may show
   "Windows protected your PC" because the app isn't code-signed yet: click **More info → Run anyway**.
4. Start **RoadForge** from the Start menu.

## Try it on a Mac (preview in your web browser)

You need Python 3.11+ (https://www.python.org/downloads/) and Node.js LTS (https://nodejs.org/).
Then in Terminal, inside this folder:

```bash
python3 scripts/preview.py
```

The first run takes a couple of minutes to install packages. Your browser opens the app at
http://localhost:1420. Press Ctrl+C in Terminal to stop.

## For developers

```
app/            Tauri (Rust shell) + React + TypeScript + Vite + MapLibre UI
  src-tauri/    desktop shell; starts the worker and hands its port+token to the UI
worker/         Python pipeline (FastAPI on 127.0.0.1, SQLite job queue, SSE progress)
  forge_worker/ sources/ stages/ exporters/ (filled from M1 on)
  models/ assets/
tests/          Python tests
docs/           ASSUMPTIONS, DATA_SOURCES, FORMAT_NOTES_AC, FORMAT_NOTES_BEAMNG
scripts/        preview.py, dev_worker.py, build_sidecar.py
```

Run the desktop app in dev mode (needs Rust too, https://rustup.rs):

```bash
cd worker && python3 -m venv .venv && .venv/bin/pip install -e ".[dev]" && cd ..
cd app && npm ci && npx tauri dev
```

Tests and checks:

```bash
pytest -q                                  # worker tests (from repo root, venv active)
ruff check . && (cd worker && mypy forge_worker)
cd app && npm run typecheck && npm test
```

Build the Windows installer locally (on Windows): `python scripts/build_sidecar.py`, then
`cd app && npx tauri build --config src-tauri/tauri.bundle.conf.json`. CI does the same.

### How the pieces talk

The desktop shell starts the worker (`forge-worker.exe`, bundled with PyInstaller). The worker
picks a random free port on 127.0.0.1, makes a random token and prints `{"port","token"}` as
its first line. The UI calls the worker with `Authorization: Bearer <token>` and follows live
progress over Server-Sent Events (`/events?token=…`). The worker exits when the app closes.

### Dependencies (and why)

| Dependency | Why |
|---|---|
| FastAPI + uvicorn | small typed local HTTP API between UI and worker |
| SQLite (stdlib) | job queue and history that survive restarts, no server |
| pytest, httpx, ruff, mypy | tests, lint, type checks |
| PyInstaller | ships the worker as one .exe inside the installer |
| Tauri 2 | desktop window with a small installer (.msi / .exe) |
| React + TypeScript + Vite | UI framework, types, fast build |
| MapLibre GL JS | open-source interactive map |
| Vitest + jsdom | UI unit tests |

## Renaming

The name and colours live in `brand.json` (used by the UI and worker). Three packaging fields
can't read it and must be changed by hand: `productName`, window `title` and `identifier` in
`app/src-tauri/tauri.conf.json`, plus the artifact name in `.github/workflows/ci.yml`.

## Data and licences

Only open data is used for building maps (see [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md)).
Google Maps / Street View are never scraped or used for generation.
