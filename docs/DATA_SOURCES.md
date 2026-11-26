# Data sources

Rules (from the spec):

- Only data we may download, process, turn into 3D and redistribute inside a mod.
- Every adapter declares coverage, resolution, licence, attribution and whether derived
  redistribution is allowed; `tests/worker/test_sources.py` fails if any adapter lacks them.
- No Google / Apple / Bing / Yandex / Mapbox / Esri imagery or tiles in the generator.
- Sharpest covering source wins; global sources are the fallback.

## Used by the generator

| Layer | Source (adapter id) | Coverage | Resolution | Licence | Attribution |
|---|---|---|---|---|---|
| Elevation | ČÚZK DMR 5G (`cuzk_dmr5g`) | Czech Republic | 2 m (LiDAR terrain) | CC BY 4.0 | © ČÚZK |
| Elevation | Copernicus DEM GLO-30 (`copernicus_dem_glo30`) | World | 30 m | Copernicus DEM licence | © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018 provided under COPERNICUS by the European Union and ESA; all rights reserved |
| Imagery | ČÚZK Ortofoto ČR (`cuzk_ortofoto`) | Czech Republic | 0.5 m (12.5 cm available) | CC BY 4.0 | © ČÚZK |
| Imagery | Sentinel-2 L2A (`sentinel2_l2a`) | World | 10 m | Copernicus Sentinel terms | Contains modified Copernicus Sentinel data [year] |
| Vectors | OpenStreetMap via Overpass (`osm_overpass`) | World | – | ODbL 1.0 | © OpenStreetMap contributors, ODbL |

Planned adapters: USGS 3DEP + NAIP (US), swisstopo, IGN (FR), AHN + PDOK (NL), UK EA LiDAR,
Overture / Microsoft building footprints, Mapillary / KartaView / Panoramax (street level, M6).

## Used only for display or lookup (never fed into the generator)

| Used for | Service | Notes |
|---|---|---|
| Map shown in the app | OpenFreeMap (OSM data) | free, no key |
| Fallback map display | tile.openstreetmap.org | OSM tile usage policy, light use |
| Place search | Nominatim | max 1 request/s |
| Following roads between clicks | OSRM public demo server | light use; result is OSM geometry (ODbL) |
