# Changelog

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
