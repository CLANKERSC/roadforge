import { useEffect, useRef, type Dispatch } from "react";
import * as maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
// MapLibre 6 loads its web worker from a separate file; let Vite bundle it and pass the URL.
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?url";
import { FALLBACK_STYLE, PRIMARY_STYLE_URL, START_VIEW } from "../lib/mapStyle";
import { boxRing, type Draft, type DraftAction, type LonLat } from "../lib/selection";
import { brand } from "../lib/brand";

maplibregl.setWorkerUrl(workerUrl);

export interface FlyTarget {
  center: LonLat;
  bbox: [number, number, number, number] | null;
  nonce: number;
}

interface Props {
  draft: Draft;
  dispatch: Dispatch<DraftAction>;
  routeCoords: LonLat[] | null;
  outline: GeoJSON.FeatureCollection | null;
  flyTo: FlyTarget | null;
}

const EMPTY: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };

function line(coords: LonLat[], role: string): GeoJSON.Feature {
  return { type: "Feature", properties: { role }, geometry: { type: "LineString", coordinates: coords } };
}

/** Everything the user has clicked so far, plus the rubber band to the mouse. */
export function draftGeoJSON(d: Draft, routeCoords: LonLat[] | null, mouse: LonLat | null): GeoJSON.FeatureCollection {
  const features: GeoJSON.Feature[] = d.points.map((p, i) => ({
    type: "Feature",
    properties: { role: "point", label: d.mode === "route" ? (i === 0 ? "A" : i === d.points.length - 1 ? "B" : "") : "" },
    geometry: { type: "Point", coordinates: p },
  }));
  if (d.mode === "route") {
    if (routeCoords && routeCoords.length >= 2) features.push(line(routeCoords, "route"));
    else if (d.points.length >= 2) features.push(line(d.points, "pending"));
  } else if (d.shape === "box") {
    const second = d.points[1] ?? mouse;
    if (d.points.length >= 1 && second) {
      const r = boxRing(d.points[0], second);
      features.push(line([...r, r[0]], d.points.length === 2 ? "shape" : "pending"));
    }
  } else if (d.points.length >= 1) {
    const pts = d.closed ? [...d.points, d.points[0]] : mouse ? [...d.points, mouse] : d.points;
    if (pts.length >= 2) features.push(line(pts, d.closed ? "shape" : "pending"));
  }
  return { type: "FeatureCollection", features };
}

function ensureLayers(map: maplibregl.Map) {
  if (map.getSource("outline")) return;
  map.addSource("outline", { type: "geojson", data: EMPTY });
  map.addSource("draft", { type: "geojson", data: EMPTY });
  const { accent, accent2 } = brand.colors;
  map.addLayer({
    id: "outline-skirt", type: "line", source: "outline", filter: ["==", ["get", "role"], "skirt"],
    paint: { "line-color": accent2, "line-width": 1.5, "line-dasharray": [3, 3] },
  }); // prettier-ignore
  map.addLayer({
    id: "outline-fill", type: "fill", source: "outline", filter: ["==", ["get", "role"], "selection"],
    paint: { "fill-color": accent, "fill-opacity": 0.18 },
  }); // prettier-ignore
  map.addLayer({
    id: "outline-edge", type: "line", source: "outline", filter: ["==", ["get", "role"], "selection"],
    paint: { "line-color": accent, "line-width": 2.5 },
  }); // prettier-ignore
  map.addLayer({
    id: "draft-pending", type: "line", source: "draft", filter: ["==", ["get", "role"], "pending"],
    paint: { "line-color": accent, "line-width": 2, "line-dasharray": [2, 2] },
  }); // prettier-ignore
  map.addLayer({
    id: "draft-shape", type: "line", source: "draft", filter: ["==", ["get", "role"], "shape"],
    paint: { "line-color": accent, "line-width": 2.5 },
  }); // prettier-ignore
  map.addLayer({
    id: "draft-route-casing", type: "line", source: "draft", filter: ["==", ["get", "role"], "route"],
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": "#0e1116", "line-width": 7 },
  }); // prettier-ignore
  map.addLayer({
    id: "draft-route", type: "line", source: "draft", filter: ["==", ["get", "role"], "route"],
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": accent, "line-width": 4 },
  }); // prettier-ignore
  map.addLayer({
    id: "draft-points", type: "circle", source: "draft", filter: ["==", ["get", "role"], "point"],
    paint: { "circle-radius": 6, "circle-color": "#ffffff", "circle-stroke-color": accent, "circle-stroke-width": 3 },
  }); // prettier-ignore
}

export function MapView({ draft, dispatch, routeCoords, outline, flyTo }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const mouse = useRef<LonLat | null>(null);
  const latest = useRef({ draft, routeCoords, outline });
  latest.current = { draft, routeCoords, outline };

  function redraw() {
    const map = mapRef.current;
    if (!map || !map.getSource("draft")) return;
    const { draft: d, routeCoords: r, outline: o } = latest.current;
    (map.getSource("draft") as maplibregl.GeoJSONSource).setData(draftGeoJSON(d, r, mouse.current));
    (map.getSource("outline") as maplibregl.GeoJSONSource).setData(o ?? EMPTY);
  }

  useEffect(() => {
    if (!container.current) return;
    const map = new maplibregl.Map({
      container: container.current,
      style: PRIMARY_STYLE_URL,
      center: START_VIEW.center,
      zoom: START_VIEW.zoom,
      attributionControl: { compact: false },
      doubleClickZoom: false, // double-click finishes a polygon instead
    });
    let fellBack = false;
    let styleReady = false;
    map.on("error", (e: maplibregl.ErrorEvent) => {
      // If the vector style itself can't load (offline, blocked), switch to plain OSM raster once.
      // A single failed tile or icon after the style is up must not throw the whole map away.
      if (!fellBack && !styleReady) {
        fellBack = true;
        console.warn("Base map style failed, using OSM raster fallback", e.error);
        map.setStyle(FALLBACK_STYLE, { diff: false });
      }
    });
    const drawOurLayers = () => {
      try {
        ensureLayers(map);
        redraw();
      } catch {
        // the style is still being put together; the next event tries again
      }
    };
    map.on("style.load", () => {
      styleReady = true;
      drawOurLayers();
    });
    // A style swap can drop our layers without a new "style.load": put them back whenever
    // the style changes, so the drawn selection can never go missing.
    map.on("styledata", drawOurLayers);
    map.on("click", (e) => dispatch({ type: "click", point: [e.lngLat.lng, e.lngLat.lat] }));
    map.on("dblclick", () => {
      dispatch({ type: "undo" }); // the second click of the double-click added a duplicate corner
      dispatch({ type: "close" });
    });
    map.on("mousemove", (e) => {
      mouse.current = [e.lngLat.lng, e.lngLat.lat];
      const d = latest.current.draft;
      const rubberBand = d.mode === "area" && !d.closed && d.points.length > 0 && !(d.shape === "box" && d.points.length >= 2);
      if (rubberBand) redraw();
    });
    map.getCanvas().style.cursor = "crosshair";
    map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "bottom-right");
    map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-left");
    mapRef.current = map;
    (window as unknown as { __map?: maplibregl.Map }).__map = map; // handy for debugging and UI tests
    return () => map.remove();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(redraw, [draft, routeCoords, outline]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !flyTo) return;
    if (flyTo.bbox) {
      const [w, s, e, n] = flyTo.bbox;
      map.fitBounds([[w, s], [e, n]], { padding: 60, maxZoom: 15, duration: 1200 }); // prettier-ignore
    } else {
      map.flyTo({ center: flyTo.center, zoom: 14 });
    }
  }, [flyTo]);

  return <div ref={container} className="map" data-testid="map" />;
}
