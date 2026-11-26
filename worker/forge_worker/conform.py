"""Stage 05d: shape the terrain around the roads.

The road mesh is rasterised onto the height grid. Under every road (plus a margin) the
terrain is pressed just below the road surface, then eased back to the natural ground over
a few metres. Result: no terrain pokes through the asphalt and there is no cliff at the edge.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray
from scipy import ndimage

from forge_worker.roadmesh import RoadMesh
from forge_worker.terrain import Dtm

A = NDArray[np.float64]
CLEARANCE_M = 0.10  # terrain sits this far below the road surface
MARGIN_M = 1.5  # ... and stays there this far beyond the road edge
FALLOFF_M = 6.0  # then blends back to natural ground over this distance


def sample_triangles(vertices: A, faces: NDArray[np.int64], spacing: float) -> A:
    """Points spread over every triangle (corners, edges and inside), at most `spacing` apart."""
    if len(faces) == 0:
        return np.zeros((0, 3))
    tri = vertices[faces]  # (m, 3, 3)
    longest = np.max(np.linalg.norm(tri - np.roll(tri, 1, axis=1), axis=2), axis=1)
    steps = np.maximum(1, np.ceil(longest / spacing).astype(int))
    out = []
    for n in np.unique(steps):
        sel = tri[steps == n]
        i, j = np.meshgrid(np.arange(n + 1), np.arange(n + 1), indexing="ij")
        keep = i + j <= n
        u, v = i[keep] / n, j[keep] / n
        w = 1.0 - u - v
        pts = (
            w[None, :, None] * sel[:, None, 0]
            + u[None, :, None] * sel[:, None, 1]
            + v[None, :, None] * sel[:, None, 2]
        )
        out.append(pts.reshape(-1, 3))
    return np.concatenate(out)


def road_height_raster(dtm: Dtm, mesh: RoadMesh, keep: NDArray[np.bool_]) -> NDArray[np.float32]:
    """Lowest road surface height per grid cell for the chosen faces (NaN where there is none)."""
    pts = sample_triangles(mesh.vertices, mesh.faces[keep], spacing=dtm.res * 0.45)
    raster = np.full(dtm.shape, np.inf, dtype=np.float32)
    if len(pts):
        row, col = dtm.to_cell(pts[:, 0], pts[:, 1])
        r, c = np.rint(row).astype(int), np.rint(col).astype(int)
        ok = (r >= 0) & (r < dtm.shape[0]) & (c >= 0) & (c < dtm.shape[1])
        np.minimum.at(raster, (r[ok], c[ok]), pts[ok, 2].astype(np.float32))
    raster[np.isinf(raster)] = np.nan
    return raster


def face_masks(mesh: RoadMesh, bridge_edges: set[int]) -> tuple[NDArray[np.bool_], NDArray[np.bool_]]:
    """(faces that rest on the ground, faces of bridge decks). The apron is in neither."""
    solid = np.asarray(mesh.face_surface) != "apron"
    on_bridge = np.isin(mesh.face_edge, sorted(bridge_edges)) if bridge_edges else np.zeros(len(solid), bool)
    return solid & ~on_bridge, solid & on_bridge


def conform_terrain(
    dtm: Dtm, mesh: RoadMesh, bridge_edges: set[int], extra_clearance: float = 0.0
) -> tuple[Dtm, dict[str, float]]:
    """Return a new DTM pressed under the roads. Under bridges the ground is only lowered if it
    would touch the deck."""
    ground_faces, bridge_faces = face_masks(mesh, bridge_edges)
    road = road_height_raster(dtm, mesh, ground_faces)
    has_road = np.isfinite(road)
    z = dtm.z.copy()
    stats: dict[str, float] = {"cells_under_road": int(has_road.sum()), "max_cut_m": 0.0, "max_fill_m": 0.0}
    if has_road.any():
        margin = max(MARGIN_M, 0.75 * dtm.res)
        falloff = max(FALLOFF_M, 2.0 * dtm.res)
        # use the lowest road height among neighbours, so sloping roads stay clear between cell centres
        low = ndimage.minimum_filter(np.where(has_road, road, np.inf), size=3, mode="nearest")
        dist, idx = ndimage.distance_transform_edt(~has_road, sampling=dtm.res, return_indices=True)
        target = low[tuple(idx)] - CLEARANCE_M - extra_clearance
        t = np.clip((dist - margin) / falloff, 0.0, 1.0)
        blend = t * t * (3.0 - 2.0 * t)  # 0 under the road, 1 far away
        z = (target * (1.0 - blend) + z * blend).astype(np.float32)
        # Every grid point next to a road cell must sit below that road, whichever road is
        # nearest to it. This matters on switchbacks, where the ground between two legs would
        # otherwise follow the upper leg and stick out over the lower one.
        z = np.minimum(z, low - CLEARANCE_M - extra_clearance).astype(np.float32)
    if bridge_faces.any():
        deck = road_height_raster(dtm, mesh, bridge_faces)
        deck = ndimage.minimum_filter(np.where(np.isfinite(deck), deck, np.inf), size=3, mode="nearest")
        z = np.minimum(z, deck - 0.5 - extra_clearance).astype(np.float32)
    change = z - dtm.z
    stats["max_cut_m"] = round(float(-np.min(change)), 2)
    stats["max_fill_m"] = round(float(np.max(change)), 2)
    return Dtm(z=z, west=dtm.west, north=dtm.north, res=dtm.res, proj4=dtm.proj4), stats
