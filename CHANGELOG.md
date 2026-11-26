# Changelog

## M1 – Selection + fetch (2026-11-26), version 0.1.0

- Draw screen: Area (box or free shape) and Road A to B (waypoints, width each side 50–1000 m,
  default 300 m), undo / reverse / clear, keyboard shortcuts (Ctrl+Z, Esc, Enter).
- Search by place name (Nominatim) or "lat, lon". Roads follow real streets (OSRM); if routing is
  unreachable the app says so and uses straight lines.
- Live readout: road length, area, estimated download size and time, data quality per layer with
  licence badges; size limits per game (AC 100 km road / 10 × 10 km, BeamNG 16 × 16 km).
- Works worldwide: OpenStreetMap (Overpass), Copernicus DEM GLO-30, Sentinel-2. Czech Republic:
  ČÚZK DMR 5G (2 m LiDAR terrain) and Ortofoto ČR (0.5 m). Automatic fallback to the global source
  if a national source has no data for the area.
- Source adapters declare coverage, resolution, licence and attribution; a test fails if any
  adapter lacks them. CREDITS.txt is written for every build.
- Build pipeline stages 01 select (selection.geojson, georef.json) and 02 fetch (cached,
  checksummed, resumable downloads on fixed tile grids, parallel with polite rate limits).
- Local projection centred on the selection; round-trip error under 1 cm (tested on 5 continents).
- Tests: 40+ worker tests on a fake internet, live download tests against the real services,
  a Playwright click-through test, and a self-test of the packaged Windows worker in CI.

### Known issues

- Game export doesn't exist yet: "Build it" ends after downloading.
- The packaged worker is now ~90 MB and takes several seconds to start (one-file bundle unpacks on
  every start). Will switch to a folder bundle in the installer milestone.
- Routing uses the public OSRM demo server and search uses public Nominatim: fine for one person,
  but both can be slow or briefly unavailable.
- Very large areas use many small Overpass requests; Geofabrik extracts come later.
- Corner points can't be dragged yet; use Undo and click again.
- Base map tiles and the installer still need a first real-PC check by Honza.

## M0 – Skeleton (2026-11-26)

- Python worker: `/health`, token-protected job API, SQLite job queue
  (queued → running(stage) → done | failed | cancelled), live progress over SSE,
  cancel, rebuild with same settings, per-job logs, resume after restart (finished stages skipped).
- Demo pipeline: fake 3-stage job (fetch, process, package).
- Desktop shell (Tauri 2): starts the worker on a random port with a random token, stops it on exit.
- UI: dark map screen (MapLibre, OpenFreeMap base map with OSM raster fallback), "lat, lon" jump,
  build panel with stage chips, progress bars, ETA, cancel, rebuild, log viewer, engine status.
- CI (GitHub Actions): worker + UI tests on Windows and Linux; Windows installer (.exe + .msi) built
  and attached to every green run on `main`.

### Known issues

- Place-name search isn't there yet (only "lat, lon"); comes in M1.
- The installer isn't code-signed, so Windows SmartScreen warns on first install.
- The worker .exe is a PyInstaller one-file build: the first start after install takes a few seconds
  while it unpacks ("Starting engine…").
- The Windows installer has only been built by CI, not yet tried on a real Windows PC.
