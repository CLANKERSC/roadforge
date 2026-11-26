import { useState } from "react";
import { parseLatLon } from "../lib/coords";
import type { Place } from "../lib/types";
import type { WorkerClient } from "../lib/worker";

interface Props {
  client: WorkerClient | null;
  onPick: (place: Place) => void;
}

export function SearchBox({ client, onPick }: Props) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Place[] | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    const text = query.trim();
    if (!text) return;
    setMessage(null);
    setResults(null);
    const coords = parseLatLon(text);
    if (coords) {
      onPick({ name: text, lat: coords.lat, lon: coords.lon, bbox: null });
      return;
    }
    if (!client) return;
    setBusy(true);
    try {
      const places = await client.search(text);
      if (places.length === 0) setMessage("Nothing found. Try another name, or type coordinates like 50.0755, 14.4378.");
      else if (places.length === 1) onPick(places[0]);
      else setResults(places);
    } catch (err) {
      setMessage(`${err instanceof Error ? err.message : err}. You can still type coordinates like 50.0755, 14.4378.`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="search" onSubmit={onSubmit}>
      <input
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="Search a place, a road, or lat, lon"
        aria-label="Search the map"
      />
      <button type="submit" disabled={busy}>
        {busy ? "…" : "Go"}
      </button>
      {message && <div className="search-hint">{message}</div>}
      {results && (
        <ul className="search-results">
          {results.map((p, i) => (
            <li key={i}>
              <button
                type="button"
                onClick={() => {
                  setResults(null);
                  onPick(p);
                }}
              >
                {p.name}
              </button>
            </li>
          ))}
        </ul>
      )}
    </form>
  );
}
