# RoadForge

Turn a real place into a drivable map for **Assetto Corsa** (first) and **BeamNG.drive** (second).
You draw a road or an area on a world map; the app builds terrain, roads and the world from
open data and exports a ready-to-install ZIP for each game.

> Maps made with this app are approximations for games, **not for navigation**.

"RoadForge" is a working name. To rename the app, see [Renaming](#renaming).

## Status

**Milestone M4 – BeamNG.drive export** (this version). You can search a place anywhere in the
world, draw an **Area** (box or free shape) or a **Road A to B** with a width on each side, name
it and press **Build it**. The build downloads terrain heights, imagery and OpenStreetMap data,
builds the roads and the terrain in 3D (real road widths, real hills, junctions, bridges, lane
markings), runs an automatic **drivability check**, and writes a **BeamNG.drive mod**.
**View in 3D** shows the result; **Install to BeamNG** copies the mod into the game, where the
map appears in Freeroam under the name you gave it. Assetto Corsa export comes next. The ground
shows the aerial photo of the place and the roads have real surfaces and lane lines; buildings
and trees are not built yet. See [CHANGELOG.md](CHANGELOG.md).

**The quality rule:** roads follow the real world as closely as the data allows, but they always
come out drivable. Where the map or the height data is wrong or too rough (a bump, a cliff, a
crossing that isn't connected, a corner tighter than a car can turn, ground poking through the
asphalt), the build repairs it and says so in the report.

Data quality depends on the place: everywhere on Earth gets OpenStreetMap + 30 m elevation +
10 m satellite imagery; the Czech Republic gets 2 m LiDAR terrain and 0.5 m aerial photos.
More countries are added as adapters (see [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md)).

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
FORGE_LIVE=1 pytest -q -s tests/live       # small real downloads from the live services
node tests/e2e/e2e.mjs                     # click-through test: real UI + engine, fake internet
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
| requests + tenacity | downloads with retries and backoff |
| pyproj, shapely, numpy | projections, polygon maths, arrays |
| rasterio (GDAL) | read/crop GeoTIFFs, also remote cloud-optimised ones |
| scipy | smoothing filters, distance maps and curve fitting for terrain and roads |
| trimesh | writes the 3D preview file (GLB) |
| three.js | shows the 3D preview in the app |
| Playwright (dev only) | click-through test of the UI |
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
