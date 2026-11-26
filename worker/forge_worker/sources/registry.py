"""All known source adapters and the rule for choosing between them."""

from __future__ import annotations

from forge_worker.selection import Selection
from forge_worker.sources.base import LAYERS, Layer, Source
from forge_worker.sources.copernicus import CopernicusDem30, Sentinel2
from forge_worker.sources.cuzk import CuzkDmr5g, CuzkOrtofoto
from forge_worker.sources.osm import OsmOverpass

# Add new countries here. Street-level sources arrive in M6.
ALL_SOURCES: tuple[Source, ...] = (
    CuzkDmr5g(),
    CopernicusDem30(),
    CuzkOrtofoto(),
    Sentinel2(),
    OsmOverpass(),
)

# Layers a build needs in M1.
REQUIRED_LAYERS: tuple[Layer, ...] = ("elevation", "imagery", "vectors")


def candidates(layer: Layer, sel: Selection) -> list[Source]:
    """Sources that cover the selection, sharpest first (global fallbacks come last)."""
    found = [s for s in ALL_SOURCES if s.info.layer == layer and s.covers(sel)]
    return sorted(found, key=lambda s: (s.info.resolution_m or 0.0, s.info.id))


def choose(sel: Selection) -> dict[Layer, list[Source]]:
    return {layer: candidates(layer, sel) for layer in LAYERS if layer in REQUIRED_LAYERS}
