# Changelog

## 0.3.2 – Buildings (2026-11-26)

- Buildings from OpenStreetMap stand in the BeamNG map: real outline and position, real height
  where the map has it (otherwise a guess by type and size), plastered walls with windows in
  four colours, pitched tile roofs on house-shaped footprints, flat roofs on halls and blocks.
  They are solid. A building the map draws on top of a road is left out so the road stays open.
- Slope limits raised (tertiary 22 %, residential 25 %, track 35 %, motorway 10 %). With the
  stricter limits a real 20 % village road was flattened and ended on a 4 m bank; in the real
  Turkovice data 92 % of all road points now lie within 0.5 m of the measured ground.
- Not yet: trees, street-level detail (real facade colours, signs, fences).

## 0.3.1 – First fixes from the real game (2026-11-26)

The 0.3.0 mod was loaded in BeamNG.drive 0.40.6: it appears in Freeroam, loads, and the car
spawns on the road and drives. These fixes come from that test.

- Rainbow noise on the ground: the roughness, ambient-occlusion and height maps were RGB; the
  game expects 8-bit grey, as in its own levels. All ground textures now follow the stock
  formats and sizes.
- The aerial (or satellite) photo of the place is laid over the ground, so fields, yards and
  forests show where they really are. Close up it is mixed with a grass texture.
- Roads have real surface textures (asphalt, concrete, cobbles, gravel), made by the app from
  noise: no photo or game asset is copied. The banks beside the road are grass.
- Lane lines on concrete roads too, lane dividers on multi-lane one-way roads, and motorway
  carriageways get their paved shoulders (2 lanes: 10 m instead of 7 m) when the map gives no width.
- A download can no longer hang for ever: every request has a time limit for the whole answer
  (5 minutes for map data). Cancel now works while a download is stuck.
- The drawn selection could vanish from the map when the base map changed its style: fixed.
- The panel text no longer says game maps come later.

## M4 – BeamNG.drive export (2026-11-26), version 0.3.0

Built ahead of M3 (Assetto Corsa) because BeamNG was asked for first.

- New last build stage: 07 export. With BeamNG.drive ticked, the build writes a ready mod,
  `export/<name>.zip`: terrain (`.ter`), the drivable road meshes with collision, invisible
  roads for AI traffic and the map, sky, a spawn point on the road, a preview picture, and
  credits for the data sources.
- App: **Install to BeamNG** on a finished build copies the mod into BeamNG's mods folder.
  BeamNG.drive is now the game ticked by default.
- Terrains larger than BeamNG allows (8192 x 8192 cells) are made coarser automatically.
- The same build always gives a byte-identical ZIP.
- Windows: the job list, download cache and builds now live in `%LOCALAPPDATA%\RoadForgeData`
  instead of the program's own folder. Existing data is moved over on first start.
- Busy OpenStreetMap server ("429 Too Many Requests", found by a real 60 km² build): the
  download now moves on to two mirror servers and, when all are busy, waits and tries again
  (up to about 2.5 minutes) before failing. A failed build says to press Rebuild; everything
  already downloaded is kept and not fetched again.
- Not verified in the real game yet. What is confirmed and what is assumed about BeamNG's files
  is in [docs/FORMAT_NOTES_BEAMNG.md](docs/FORMAT_NOTES_BEAMNG.md).
- Known limits: plain colours instead of textures, no satellite photo on the ground, no
  buildings or trees, tunnels are open cuttings. Assetto Corsa export is not built yet.

## M2 – Terrain + roads (2026-11-26), version 0.2.0

- New build stages after the downloads: 03 terrain (`dtm.tif`), 04 roads (`roads.json`,
  `dtm_conformed.tif`), 05 scene (`scene.json`, `preview.glb`), 06 check (`drivability.json`).
  Each stage is cached and skipped on a rebuild when its inputs did not change.
- Terrain: all height tiles are joined into one grid in the map's own metric projection (2 m
  cells; 3 to 8 m for very large selections). Holes are filled, LiDAR spikes removed, coarse 30 m
  data is smoothed so it has no stair steps.
- Roads from OpenStreetMap: type, lanes, width (`width` tag or lanes × lane width per type),
  surface, one-way, bridges, tunnels, layers. Ways are split at junctions, clipped to the
  selection, and joined back into long stretches.
- Road shape: smooth centreline every 2 m, 2 % crown, 0.5 m verge, a sloping apron that dips into
  the terrain, lane markings, low walls on bridges. Junctions are welded polygons: no gaps.
- **Always drivable:** crossings that are not connected on the map become junctions; corners
  tighter than the road is wide are opened; slopes are limited per road type (8 % motorway to
  30 % track); sudden kinks in height are eased; all roads arrive at a junction at one height;
  a road passing over another gets 5 m headroom even when the height data shows none.
- Mountain switchbacks: the ground between two stacked legs is cut so it never hangs over the
  lower leg, and in a hairpin tighter than the road is wide the inside edge is pulled in instead
  of folding over. (Both found by the live Stelvio build, now also covered by offline tests.)
- ČÚZK height tiles are read with the projection the adapter declares; the projection written
  inside the files can't be transformed by PROJ (found by the live Prague build).
- Terrain is pressed 10 cm below every road and blended back to natural ground over a few metres.
  Under bridges the ground is only lowered if it would touch the deck.
- Drivability check at the end of every build: welded seams, no folded surface, terrain never
  through the road, slope and kink limits, junction heights, mesh size for Assetto Corsa
  (< 65,000 points). The report lists every check and everything that was repaired.
- App: **View in 3D** on a finished build (three.js, loaded only when opened) with the report
  next to it. The build card ends with the verdict instead of just "Done".
- Deterministic: building the same selection twice gives byte-identical `scene.json`,
  `roads.json`, `drivability.json` and `preview.glb` (tested).
- Tests: 62 worker tests (a fake town with bridges, unconnected crossings, hairpins, noisy LiDAR
  with holes and spikes), live builds of Prague and the Stelvio pass that must pass every
  drivability check, the click-through test now opens the 3D preview, and the packaged Windows
  worker's self-test builds a small 3D scene.

### Known issues

- Still no game export: the result is the engine-neutral 3D scene. Assetto Corsa export is M3.
- Tunnels are built as open cuttings for now (drivable, but there is no roof).
- No buildings, trees, textures or satellite image on the terrain yet (plain colours).
- Banking in corners is not taken from the terrain yet; every road has a plain 2 % crown.
- Roundabouts and very complex junctions are built from the same simple junction shape; they are
  drivable but can look rough.
- A road selection whose bounding box is larger than about 44 x 44 km is refused (terrain is
  held in memory as one grid). Long roads that run mostly straight are fine.
- A 6 x 6 km town takes a few minutes and around 2 GB of memory to build.
- The 3D preview of very large maps shows simplified terrain.
- The installer has still never been run on a real Windows PC by a person.

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
