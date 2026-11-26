import { useEffect, useRef, useState } from "react";
import * as maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
// MapLibre 6 loads its web worker from a separate file; let Vite bundle it and pass the URL.
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?url";
import { FALLBACK_STYLE, PRIMARY_STYLE_URL, START_VIEW } from "../lib/mapStyle";
import { parseLatLon } from "../lib/coords";

maplibregl.setWorkerUrl(workerUrl);

export function MapView() {
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const [query, setQuery] = useState("");
  const [hint, setHint] = useState<string | null>(null);

  useEffect(() => {
    if (!container.current) return;
    const map = new maplibregl.Map({
      container: container.current,
      style: PRIMARY_STYLE_URL,
      center: START_VIEW.center,
      zoom: START_VIEW.zoom,
      attributionControl: { compact: false },
    });
    let fellBack = false;
    map.on("error", (e: maplibregl.ErrorEvent) => {
      // If the vector style can't load (offline, blocked), switch to plain OSM raster once.
      if (!fellBack && !map.isStyleLoaded()) {
        fellBack = true;
        console.warn("Base map style failed, using OSM raster fallback", e.error);
        map.setStyle(FALLBACK_STYLE);
      }
    });
    map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "bottom-right");
    map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-left");
    mapRef.current = map;
    return () => map.remove();
  }, []);

  function onSearch(e: React.FormEvent) {
    e.preventDefault();
    const coords = parseLatLon(query);
    if (coords && mapRef.current) {
      mapRef.current.flyTo({ center: [coords.lon, coords.lat], zoom: 15 });
      setHint(null);
    } else {
      setHint('For now, type coordinates like "50.0755, 14.4378". Place search comes in M1.');
    }
  }

  return (
    <div className="map-wrap">
      <div ref={container} className="map" data-testid="map" />
      <form className="search" onSubmit={onSearch}>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search: lat, lon"
          aria-label="Search the map"
        />
        <button type="submit">Go</button>
        {hint && <div className="search-hint">{hint}</div>}
      </form>
    </div>
  );
}
