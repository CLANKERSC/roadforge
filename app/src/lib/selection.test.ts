import { describe, expect, it } from "vitest";
import { areaRing, boxRing, draftReducer, emptyDraft, hintFor, toSelection, type Draft, type LonLat } from "./selection";

const A: LonLat = [14.4, 50.0];
const B: LonLat = [14.5, 50.1];
const C: LonLat = [14.6, 50.0];

function run(d: Draft, ...points: LonLat[]): Draft {
  return points.reduce((acc, point) => draftReducer(acc, { type: "click", point }), d);
}

describe("box drawing", () => {
  it("needs two clicks and normalises corners", () => {
    expect(areaRing(run(emptyDraft, B))).toBeNull();
    expect(areaRing(run(emptyDraft, B, A))).toEqual(boxRing(A, B));
    expect(boxRing(B, A)[0]).toEqual([14.4, 50.0]);
  });
  it("starts over on the third click", () => {
    expect(run(emptyDraft, A, B, C).points).toEqual([C]);
  });
  it("ignores a zero-size box", () => {
    expect(areaRing(run(emptyDraft, A, A))).toBeNull();
  });
});

describe("polygon drawing", () => {
  const poly = draftReducer(emptyDraft, { type: "setShape", shape: "polygon" });
  it("closes only with 3+ corners", () => {
    expect(draftReducer(run(poly, A, B), { type: "close" }).closed).toBe(false);
    const closed = draftReducer(run(poly, A, B, C), { type: "close" });
    expect(closed.closed).toBe(true);
    expect(areaRing(closed)).toEqual([A, B, C]);
  });
  it("undo reopens, then removes corners", () => {
    let d = draftReducer(run(poly, A, B, C), { type: "close" });
    d = draftReducer(d, { type: "undo" });
    expect(d.closed).toBe(false);
    expect(draftReducer(d, { type: "undo" }).points).toEqual([A, B]);
  });
});

describe("route drawing", () => {
  const route = draftReducer(emptyDraft, { type: "setMode", mode: "route" });
  it("collects waypoints, reverses and clears", () => {
    const d = run(route, A, B, C);
    expect(d.points).toHaveLength(3);
    expect(draftReducer(d, { type: "reverse" }).points).toEqual([C, B, A]);
    expect(draftReducer(d, { type: "clear" }).points).toEqual([]);
  });
  it("switching mode clears the drawing", () => {
    expect(draftReducer(run(route, A, B), { type: "setMode", mode: "area" }).points).toEqual([]);
  });
  it("clamps the corridor width", () => {
    expect(draftReducer(route, { type: "setCorridor", metres: 5000 }).corridorM).toBe(1000);
    expect(draftReducer(route, { type: "setCorridor", metres: 1 }).corridorM).toBe(50);
  });
});

describe("toSelection", () => {
  it("is null until something complete is drawn and a game is picked", () => {
    expect(toSelection(emptyDraft, null, ["ac"])).toBeNull();
    expect(toSelection(run(emptyDraft, A, B), null, [])).toBeNull();
    const route = run(draftReducer(emptyDraft, { type: "setMode", mode: "route" }), A, B);
    expect(toSelection(route, null, ["ac"])).toBeNull();
  });
  it("builds area and route payloads", () => {
    expect(toSelection(run(emptyDraft, A, B), null, ["ac"])).toMatchObject({ mode: "area", games: ["ac"] });
    const route = run(draftReducer(emptyDraft, { type: "setMode", mode: "route" }), A, B);
    const sel = toSelection(route, [A, C, B], ["ac", "beamng"]);
    expect(sel).toMatchObject({ mode: "route", route: [A, C, B], corridor_m: 300 });
  });
});

describe("hints", () => {
  it("guide the user step by step", () => {
    expect(hintFor(emptyDraft)).toMatch(/one corner/);
    expect(hintFor(run(emptyDraft, A))).toMatch(/opposite corner/);
    const route = draftReducer(emptyDraft, { type: "setMode", mode: "route" });
    expect(hintFor(route)).toMatch(/starts/);
  });
});
