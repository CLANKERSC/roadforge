import { describe, expect, it } from "vitest";
import { parseLatLon } from "./coords";

describe("parseLatLon", () => {
  it("parses common forms", () => {
    expect(parseLatLon("50.0755, 14.4378")).toEqual({ lat: 50.0755, lon: 14.4378 });
    expect(parseLatLon("50.0755 14.4378")).toEqual({ lat: 50.0755, lon: 14.4378 });
    expect(parseLatLon("50,0755; 14,4378")).toEqual({ lat: 50.0755, lon: 14.4378 });
    expect(parseLatLon("-33.9, 151.2")).toEqual({ lat: -33.9, lon: 151.2 });
  });
  it("rejects non-coordinates", () => {
    expect(parseLatLon("Praha")).toBeNull();
    expect(parseLatLon("95, 10")).toBeNull();
    expect(parseLatLon("")).toBeNull();
  });
});
