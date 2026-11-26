# BeamNG.drive format notes

What the BeamNG export writes, and how sure we are of each part. **Confirmed** means it is in
BeamNG's public documentation or proven by our own round-trip test. **Assumed** means it follows
what stock levels look like but has not been checked inside the game yet. **Seen in the game**
means it worked in BeamNG.drive 0.40.6 on 2026-11-26.

Seen in the game so far: the mod is listed in Freeroam, the level loads, the terrain is in
the right place and the right way round, the road mesh is solid, the car spawns on the road,
`beamng.log` shows no errors for the level.

## The mod

One ZIP, copied still zipped into `<BeamNG user folder>/current/mods/`. Its only top folder is
`levels/<level_id>/`. The ZIP is deterministic: same build, same bytes.

```
levels/<id>/info.json                 title, description, previews, spawn points, size
levels/<id>/preview.png               top-down picture for the level list
levels/<id>/CREDITS.txt               data sources and licences
levels/<id>/theTerrain.ter            height grid (binary)
levels/<id>/theTerrain.terrain.json   describes the .ter (size, material names)
levels/<id>/main/items.level.json                 the MissionGroup
levels/<id>/main/MissionGroup/items.level.json    LevelInfo, TimeOfDay, ScatterSky, TerrainBlock, SpawnSphere
levels/<id>/main/MissionGroup/RoadMeshes/items.level.json   one TSStatic per road mesh file
levels/<id>/main/MissionGroup/Roads/items.level.json        one invisible DecalRoad per road, for AI and the map
levels/<id>/art/terrains/             ground textures, TerrainMaterial, TerrainMaterialTextureSet
levels/<id>/art/shapes/roads/         roads_XX_YY.dae (about 1 km each) and their materials
levels/<id>/art/road/                 the invisible material of the AI roads
```

`items.level.json` files hold one JSON object per line (confirmed).

## Terrain (`.ter`, version 9)

Little-endian: `u8 version (9)`, `u32 size`, `u16 height[size*size]`, `u8 layer[size*size]`
(index into the material list, 255 = hole), `u32 materialCount`, then the material names.
Confirmed by the docs. Our reader and writer are tested as a round trip.

- **Assumed:** each material name is written as one length byte followed by the text.
- **Assumed:** the first row is the southern edge, the first column the western edge.
- Size is a power of two, at most 8192. Bigger terrains are halved (keeping the lowest of each
  2 x 2 block, so the ground never rises above a road) until they fit. Padding repeats the edge.
- Height = value x maxHeight / 65535, plus the TerrainBlock's z position. maxHeight is the
  height range rounded up, at least 16 m. With a 500 m range one step is under 1 cm.
- TerrainBlock `position` is the south-west corner; `squareSize` is the cell size in metres.

## Roads you drive on

The road surface is the same welded mesh the drivability check passed, written as COLLADA 1.4.1
(`Z_UP`, metres) and placed as `TSStatic` objects at the level origin.

- **Assumed:** `collisionType` and `decalType` `"Visible Mesh Final"` make the mesh itself solid.
- Each surface (asphalt, concrete, gravel, dirt, markings ...) is its own material (version 1.5,
  plain colour, no textures yet) with a `groundType` so grip and sound match. **Assumed:** the
  ground type names (`ASPHALT`, `GRAVEL`, `DIRT` ...).
- The terrain sits 10 cm under the road everywhere, so the wheels only ever touch the mesh.

## Roads for AI traffic and the map

Every road is also a `DecalRoad` with an invisible material: nodes `[x, y, z, width]`,
`drivability` by road type, `lanesLeft`/`lanesRight`, `oneWay`, `overObjects: true`,
`improvedSpline: true`. Field names are confirmed by the docs; that AI follows them over a
TSStatic mesh is assumed.

## Scene objects

`LevelInfo`, `TimeOfDay`, `ScatterSky` (**assumed** field sets, kept minimal), `TerrainBlock`,
and a `SpawnSphere` named `spawns_default`, which `info.json` lists as the default spawn
(confirmed). **Assumed:** the order of the 9 numbers in `rotationMatrix`.

## Textures (checked against the stock level small_island)

- Terrain textures are plain `.png`: base colour and normal 8-bit RGB, roughness / ao / height
  8-bit **grey**. RGB data maps render as rainbow noise (seen in the game). Detail and macro maps
  are 1024 px, base maps 2048 px (4096 for terrains wider than 3 km); `TerrainMaterialTextureSet`
  states those sizes.
- The base maps cover the whole terrain once (`...BaseTexSize` = terrain width in metres). The
  base colour is the aerial photo, north up. **Assumed:** north-up is the orientation the game
  expects; if the photo comes out mirrored against the roads, flip it here.
- Road materials (version 1.5) name their files `_b.color.png`, `_nm.normal.png`, `_r.data.png`,
  `_ao.data.png` as stock materials do. Texture coordinates are the map position in metres
  divided by the tile size, so there is no seam at junctions.
- The game notes a missing `theTerrain.ter.depth.png` and "No minimap data in info.json". Both
  are harmless and not written yet.

## Buildings

`art/shapes/buildings/buildings_XXX_YYY.dae`, one file per 512 m block, each a `TSStatic` with
mesh collision under `main/MissionGroup/Buildings`. Six materials: four tinted plaster walls
sharing one window texture, clay tiles, flat-roof sheeting. **Assumed:** `annotation: BUILDINGS`.

## Trees and bushes

As stock levels do it: `art/forest/managedItemData.json` (one `TSForestItemData` per model),
`forest/<model>.forest4.json` (one plant per line: `pos`, `rotationMatrix`, `scale`, `type`) and a
`Forest` object named `theForest`. **Assumed:** a level may point `shapeFile` at the shared
library `/assets/meshes/foliage/trees_library/...` (present in `content/assets/meshes.zip`).

## Not done yet

 Water, tunnels (open
cuttings for now).
