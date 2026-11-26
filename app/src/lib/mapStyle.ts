import type { StyleSpecification } from "maplibre-gl";

/**
 * Base map shown in the app (display only, never fed into the generator).
 * OpenFreeMap: free, no account or key, OSM data, terms allow use in apps.
 * Fallback: OSM's own raster tiles (fine for light single-user use, per the OSM tile policy).
 */
export const PRIMARY_STYLE_URL = "https://tiles.openfreemap.org/styles/liberty";

export const FALLBACK_STYLE: StyleSpecification = {
  version: 8,
  sources: {
    osm: {
      type: "raster",
      tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
      tileSize: 256,
      maxzoom: 19,
      attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    },
  },
  layers: [{ id: "osm", type: "raster", source: "osm" }],
};

/** Start over the Czech Republic (first test country). */
export const START_VIEW = { center: [15.5, 49.8] as [number, number], zoom: 6.8 };
