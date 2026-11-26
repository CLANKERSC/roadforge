# Data sources

Filled in from M1, when the source adapters arrive. Rules (from the spec):

- Only data we may download, process, turn into 3D and redistribute inside a mod.
- Every adapter declares coverage, resolution, licence, attribution and whether derived
  redistribution is allowed; a test fails if any adapter lacks a licence/attribution.
- No Google / Apple / Bing / Yandex / Mapbox / Esri imagery or tiles in the generator.

| Used for | Source | Licence | Since |
|---|---|---|---|
| Map shown in the app (display only) | OpenFreeMap (OSM data) | ODbL data, free service | M0 |
| Fallback map display | tile.openstreetmap.org | ODbL, OSM tile usage policy | M0 |
