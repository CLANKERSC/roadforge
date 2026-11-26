# Assumptions

Each entry: what we assumed, why, and how to check it.

## Project setup (M0)

- **Target machine is Windows 10/11 x64.** Confirmed by Honza. Development also works on macOS/Linux.
- **Works worldwide** (Honza, 2026-11-26), with the Czech Republic as the first high-detail country.
- **GPU / RAM unknown.** Nothing is tuned for a specific GPU yet.
- **App name "RoadForge" is a placeholder.** Rename in `brand.json` (see README).
- **Base map for display:** OpenFreeMap "liberty" style (free, no key, OSM data). If it can't load,
  the app falls back to OSM's raster tiles, acceptable for light single-user use under the OSM tile
  usage policy. The base map is display-only and never used to generate game content.
- **Worker security:** binds to 127.0.0.1 only; every endpoint except `/health` needs the per-run
  random token; CORS allows only the Tauri webview origins and the dev server on port 1420.
- **Worker lifetime:** the worker exits when its stdin closes, so it dies with the desktop app even if
  the app crashes (PyInstaller one-file builds run as two processes, so killing only the parent
  would leave an orphan).

## Selection and downloads (M1)

- **Limits:** AC route ≤ 100 km, AC area ≤ 10 × 10 km, BeamNG area ≤ 16 × 16 km, measured on the
  selection's bounding box in local metres. One table in `selection.py`.
- **Skirt:** 200 m is added around every selection for terrain and horizon.
- **Routing:** public OSRM demo server (`router.project-osrm.org`), no account. Its policy allows
  light use only; acceptable for a single-user desktop tool. A local router on an OSM extract can
  replace it later behind the same function (`services.route_along_roads`).
- **Place search:** public Nominatim, max 1 request/second, app-name User-Agent, only on Enter.
- **Overpass:** OSM is fetched in fixed 0.1° cells so overlapping builds share the cache.
- **ČÚZK services (checked 2026-11-26 against the live service descriptions):**
  DMR 5G = ArcGIS ImageServer `arcgis2/rest/services/dmr5g`, 2 m pixels, EPSG:5514, float32 via
  `exportImage`; Ortofoto = MapServer `arcgis1/rest/services/ORTOFOTO`, `export` up to 4096 px,
  finest level 12.5 cm (we request 0.5 m).
- **ČÚZK licence:** treated as CC BY 4.0 open data with attribution "© ČÚZK". To re-check before
  any public release of maps: the exact wording ČÚZK requires and whether the view services'
  terms differ from the downloadable open data files.
- **Czech coverage outline** is a rough hand-made polygon (+~10 km). It only decides whether to try
  ČÚZK; if tiles come back empty (< 60 % valid heights, or mostly blank photos) the build falls
  back to the global source and logs a warning.
- **EPSG:5514 accuracy:** pyproj's default S-JTSK transformation without grid files is good to
  about 1 m. Enough for choosing tiles; revisit in M2 when terrain and OSM roads are aligned.
- **Copernicus DEM:** whole 1° tiles (~25 MB) are cached; a missing tile means open sea.
- **Sentinel-2:** least-cloudy scene (< 10 % cloud) of the last 4 years per grid tile via the
  Earth Search STAC API; the pick is cached so rebuilds use the same image. Read in 1024 px blocks
  straight from the cloud-optimised GeoTIFF.
- **Time estimate** assumes a 20 Mbit/s connection.
