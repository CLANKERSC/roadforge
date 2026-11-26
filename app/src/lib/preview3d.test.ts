import { describe, expect, it, vi } from "vitest";

vi.mock("three", () => ({}));
vi.mock("three/examples/jsm/controls/OrbitControls.js", () => ({}));
vi.mock("three/examples/jsm/loaders/GLTFLoader.js", () => ({}));

import { cameraStart, toScene } from "./preview3d";

describe("3D preview coordinates", () => {
  it("turns map coordinates (z up) into scene coordinates (y up, north = -z)", () => {
    expect(toScene([10, 20, 300])).toEqual([10, 300, -20]);
  });

  it("puts the camera behind the car, looking along the road", () => {
    // heading north: camera is south of the car (larger scene z) and above it
    const north = cameraStart({ xyz: [0, 0, 100], heading_deg: 0 }, 60, 28);
    expect(north[0]).toBeCloseTo(0);
    expect(north[1]).toBeCloseTo(128);
    expect(north[2]).toBeCloseTo(60);
    // heading east: camera is west of the car
    const east = cameraStart({ xyz: [0, 0, 100], heading_deg: 90 }, 60, 28);
    expect(east[0]).toBeCloseTo(-60);
    expect(east[2]).toBeCloseTo(0);
  });
});
