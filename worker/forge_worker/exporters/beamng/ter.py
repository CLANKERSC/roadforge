"""BeamNG terrain file (.ter, version 9): write and read.

Layout (little-endian), from BeamNG's level format documentation:
  u8   version (9)
  u32  size                      the grid is size x size, a power of two
  u16  heightMap[size * size]    height in metres = value * maxHeight / 65535
  u8   layerMap[size * size]     index into the material list, 255 = hole
  u32  materialCount
  then per material: u8 length + that many bytes of the material's internal name

Row 0 of the arrays is the SOUTH edge (lowest y), columns run west to east. See
docs/FORMAT_NOTES_BEAMNG.md for what is confirmed and what is assumed.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

VERSION = 9
HOLE = 255


@dataclass
class TerFile:
    size: int
    heights: NDArray[np.uint16]  # (size, size), row 0 = south
    layers: NDArray[np.uint8]  # (size, size)
    materials: list[str]


def write_ter(path: Path, ter: TerFile) -> None:
    if (
        ter.size & (ter.size - 1)
        or ter.heights.shape != (ter.size, ter.size)
        or ter.layers.shape != ter.heights.shape
    ):
        raise ValueError("terrain grid must be square with a power-of-two size")
    if not 0 < len(ter.materials) <= 254:
        raise ValueError("a terrain needs 1 to 254 materials")
    with path.open("wb") as f:
        f.write(struct.pack("<BI", VERSION, ter.size))
        f.write(ter.heights.astype("<u2").tobytes())
        f.write(ter.layers.astype("u1").tobytes())
        f.write(struct.pack("<I", len(ter.materials)))
        for name in ter.materials:
            raw = name.encode("ascii")
            if len(raw) > 254:
                raise ValueError("terrain material name too long")
            f.write(struct.pack("<B", len(raw)) + raw)


def read_ter(path: Path) -> TerFile:
    data = path.read_bytes()
    version, size = struct.unpack_from("<BI", data, 0)
    if version != VERSION:
        raise ValueError(f"unsupported .ter version {version}")
    pos, n = 5, size * size
    heights = np.frombuffer(data, dtype="<u2", count=n, offset=pos).reshape(size, size).copy()
    pos += 2 * n
    layers = np.frombuffer(data, dtype="u1", count=n, offset=pos).reshape(size, size).copy()
    pos += n
    (count,) = struct.unpack_from("<I", data, pos)
    pos += 4
    materials = []
    for _ in range(count):
        length = data[pos]
        materials.append(data[pos + 1 : pos + 1 + length].decode("ascii"))
        pos += 1 + length
    if pos != len(data):
        raise ValueError("unexpected bytes at the end of the .ter file")
    return TerFile(size=size, heights=heights, layers=layers, materials=materials)


@dataclass
class TerrainGrid:
    """A height grid packed for BeamNG, with what the TerrainBlock object needs to place it."""

    ter: TerFile
    square_size: float  # metres per sample
    max_height: float  # metres represented by the value 65535
    position: tuple[float, float, float]  # world position of sample (0, 0): south-west corner


def pack_heights(
    z_north_first: NDArray[np.float32], west_x: float, north_y: float, res: float, material: str
) -> TerrainGrid:
    """Our height grid (row 0 = north, heights at cell centres) -> BeamNG's square power-of-two grid.

    The grid is padded to the next power of two by repeating the edge heights, so the extra
    terrain outside the map is a flat continuation instead of a cliff.
    """
    rows, cols = z_north_first.shape
    size = 1 << (max(rows, cols, 2) - 1).bit_length()
    south_first = z_north_first[::-1].astype(np.float64)
    padded = np.pad(south_first, ((0, size - rows), (0, size - cols)), mode="edge")
    z_min = float(np.floor(np.min(padded)))
    max_height = float(max(16.0, np.ceil(np.max(padded) - z_min + 1.0)))
    values = np.clip(np.rint((padded - z_min) / max_height * 65535.0), 0, 65535).astype(np.uint16)
    ter = TerFile(
        size=size, heights=values, layers=np.zeros((size, size), dtype=np.uint8), materials=[material]
    )
    south_y = north_y - rows * res
    return TerrainGrid(ter, res, max_height, (west_x + res / 2.0, south_y + res / 2.0, z_min))


def unpack_heights(grid: TerrainGrid) -> NDArray[np.float64]:
    """Heights in metres as BeamNG will see them (row 0 = south)."""
    return grid.ter.heights.astype(np.float64) * grid.max_height / 65535.0 + grid.position[2]
