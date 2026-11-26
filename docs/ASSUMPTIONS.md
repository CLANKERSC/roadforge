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

## Terrain and roads (M2)

- **Grid:** 2 m cells in the build's local projection. If the skirted selection would need more
  than 30 million cells the cell size grows (3, 4, 6, 8 m); beyond that the build is refused.
- **Height datums are not converted.** ČÚZK heights are Bpv, Copernicus heights are EGM2008. A map
  uses one source, so only the absolute sea-level number differs by a few decimetres.
- **Copernicus GLO-30 is a surface model** (tree tops and roofs included). Roads on it are
  smoothed strongly (about 24 m) to get rid of that; LiDAR terrain is smoothed lightly (6 m).
- **Road width** when OSM has no `width`: lanes × lane width by type (motorway 3.5 m ... track
  2.5 m). One-way streets without a `lanes` tag count as one lane. Widths are clamped to 2.5-30 m.
- **Left out:** footways, cycleways, paths, steps, `area=yes`, parking aisles, driveways, private
  service roads and tracks.
- **Slope limits** by type: motorway 10 %, trunk 12 %, primary 15 %, secondary 18 %, tertiary
  22 %, residential/unclassified 25 %, service 28 %, track 35 %. Real roads steeper than this are
  flattened to the limit. Raised in 0.3.2: with the first, stricter limits a real 20 % village
  road near Turkovice was flattened and ended up on a 4 m bank above the ground.
- **Kink limit:** slope may change by at most 6 % between two 2 m steps.
- **Bridges** are straight decks between their two ends (plus an arch when headroom is needed);
  the tunnel flag is kept in the data but tunnels are open cuttings for now.
- **Headroom** where one road passes over another: 5 m, made by arching the bridge and dipping
  the lower road, within their slope limits.
- **Terrain under roads:** 10 cm below the lowest road surface nearby, held for 1.5 m beyond the
  verge, blended back over 6 m.
- **"Max height step < 2 cm"** from the brief is read as: no gap or step between welded road
  pieces (they share vertices, so it is exactly 0) and junction heights agree within 2 mm.
- **Mesh tiles:** 256 m terrain tiles, full detail within 30 m of a road, every 4th sample
  elsewhere, borders matched so there are no cracks. Road meshes are split per tile and surface.
- **Coordinates in files:** x east, y north, z up, metres, origin at the selection centre. Only
  `preview.glb` is y-up (glTF convention).
- **Spawn point:** the start of the chosen road in Road mode; the middle of the longest
  highest-class road in Area mode.

## M4 (BeamNG export)

- Every unverified detail of the BeamNG files is listed in
  [FORMAT_NOTES_BEAMNG.md](FORMAT_NOTES_BEAMNG.md) under "Assumed". Nothing in the sandbox can
  run BeamNG, so the first proof is loading a map in the real game.
- **BeamNG before Assetto Corsa:** the owner asked for BeamNG first, so M4 was built ahead of M3.
  A build with only Assetto Corsa ticked still runs and says that its export is not built yet.
- **Level origin** is the selection centre; heights are real metres above sea level.
- **BeamNG's mods folder** is looked up at `%LOCALAPPDATA%\BeamNG\BeamNG.drive\current\mods`
  (0.36 and newer), then the newest numbered version folder. Not found: the app says where the
  ZIP is so it can be copied by hand.
- **App data on Windows** moved from `%LOCALAPPDATA%\RoadForge` (which is also where the
  installer puts the program) to `%LOCALAPPDATA%\RoadForgeData`. Old data is moved once.
