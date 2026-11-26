/** What the user is drawing on the map. Pure functions, so they are easy to test. */

export type LonLat = [number, number];
export type Mode = "route" | "area";
export type AreaShape = "box" | "polygon";
export type Game = "ac" | "beamng";

export interface Draft {
  mode: Mode;
  shape: AreaShape;
  /** route: waypoints. box: 1-2 opposite corners. polygon: corners in order. */
  points: LonLat[];
  /** polygon only: true once the user closed the shape */
  closed: boolean;
  corridorM: number;
}

export const CORRIDOR = { min: 50, max: 1000, step: 10, default: 300 };

export const emptyDraft: Draft = { mode: "area", shape: "box", points: [], closed: false, corridorM: CORRIDOR.default };

export type DraftAction =
  | { type: "setMode"; mode: Mode }
  | { type: "setShape"; shape: AreaShape }
  | { type: "click"; point: LonLat }
  | { type: "close" }
  | { type: "undo" }
  | { type: "reverse" }
  | { type: "clear" }
  | { type: "setCorridor"; metres: number };

export function draftReducer(d: Draft, a: DraftAction): Draft {
  switch (a.type) {
    case "setMode":
      return a.mode === d.mode ? d : { ...d, mode: a.mode, points: [], closed: false };
    case "setShape":
      return a.shape === d.shape ? d : { ...d, shape: a.shape, points: [], closed: false };
    case "click":
      if (d.mode === "area" && d.shape === "box") {
        // third click starts a new box
        return { ...d, points: d.points.length >= 2 ? [a.point] : [...d.points, a.point] };
      }
      if (d.mode === "area" && d.closed) return { ...d, points: [a.point], closed: false };
      return { ...d, points: [...d.points, a.point] };
    case "close":
      return d.mode === "area" && d.shape === "polygon" && d.points.length >= 3 ? { ...d, closed: true } : d;
    case "undo":
      if (d.closed) return { ...d, closed: false };
      return { ...d, points: d.points.slice(0, -1) };
    case "reverse":
      return d.mode === "route" ? { ...d, points: [...d.points].reverse() } : d;
    case "clear":
      return { ...d, points: [], closed: false };
    case "setCorridor":
      return { ...d, corridorM: Math.min(CORRIDOR.max, Math.max(CORRIDOR.min, a.metres)) };
  }
}

/** The four corners of a box given two opposite corners. */
export function boxRing(a: LonLat, b: LonLat): LonLat[] {
  const [w, e] = [Math.min(a[0], b[0]), Math.max(a[0], b[0])];
  const [s, n] = [Math.min(a[1], b[1]), Math.max(a[1], b[1])];
  return [
    [w, s],
    [e, s],
    [e, n],
    [w, n],
  ];
}

/** The finished area polygon, or null while the user is still drawing. */
export function areaRing(d: Draft): LonLat[] | null {
  if (d.mode !== "area") return null;
  if (d.shape === "box") {
    if (d.points.length < 2) return null;
    const ring = boxRing(d.points[0], d.points[1]);
    return ring[0][0] === ring[1][0] || ring[0][1] === ring[3][1] ? null : ring;
  }
  return d.closed && d.points.length >= 3 ? d.points : null;
}

export interface SelectionPayload {
  mode: Mode;
  polygon?: LonLat[];
  route?: LonLat[];
  corridor_m: number;
  games: Game[];
}

/** What gets sent to the engine, or null if there is nothing complete to send yet. */
export function toSelection(d: Draft, routeCoords: LonLat[] | null, games: Game[]): SelectionPayload | null {
  if (games.length === 0) return null;
  if (d.mode === "area") {
    const ring = areaRing(d);
    return ring ? { mode: "area", polygon: ring, corridor_m: d.corridorM, games } : null;
  }
  if (!routeCoords || routeCoords.length < 2) return null;
  return { mode: "route", route: routeCoords, corridor_m: d.corridorM, games };
}

export function hintFor(d: Draft): string {
  if (d.mode === "route") {
    if (d.points.length === 0) return "Click the map where your road starts.";
    if (d.points.length === 1) return "Now click where it ends. Add more clicks to steer it through places.";
    return "Click to extend the road, or go on to step 2.";
  }
  if (d.shape === "box") {
    if (d.points.length === 0) return "Click one corner of your area.";
    if (d.points.length === 1) return "Now click the opposite corner.";
    return "Area set. Click again to start a new box.";
  }
  if (d.closed) return "Area set. Click the map to start a new shape.";
  if (d.points.length < 3) return "Click the corners of your area, one by one.";
  return "Keep clicking corners. Double-click or press Enter to finish.";
}
