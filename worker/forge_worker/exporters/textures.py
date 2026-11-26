"""Our own seamless ground textures, made from noise. No photo or game asset is copied.

Every texture tiles without a visible seam (the noise is periodic) and is the same for the same
seed, so builds stay reproducible. Colours are sRGB 0-255; "data" maps are plain grey values.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

U8 = NDArray[np.uint8]
F = NDArray[np.float64]


def tile_noise(size: int, seed: int, falloff: float, low_cut: float = 0.0) -> F:
    """Seamless noise in 0..1. Bigger `falloff` gives larger, softer blobs; `low_cut` removes
    features larger than size/low_cut pixels so the tile does not show as a repeating blotch."""
    rng = np.random.default_rng(seed)
    spectrum = np.fft.fft2(rng.standard_normal((size, size)))
    fy = np.fft.fftfreq(size)[:, None] * size
    fx = np.fft.fftfreq(size)[None, :] * size
    f = np.hypot(fx, fy)
    gain = np.where(f > 0, 1.0 / np.maximum(f, 1.0) ** falloff, 0.0)
    gain = np.where(f < low_cut, 0.0, gain)
    out = np.real(np.fft.ifft2(spectrum * gain))
    lo, hi = np.percentile(out, [0.5, 99.5])
    return np.asarray(np.clip((out - lo) / max(float(hi - lo), 1e-9), 0.0, 1.0), dtype=np.float64)


def normal_map(height: F, strength: float) -> U8:
    """Tangent-space normal map (RGB) of a seamless height field."""
    dx = (np.roll(height, -1, axis=1) - np.roll(height, 1, axis=1)) * strength
    dy = (np.roll(height, -1, axis=0) - np.roll(height, 1, axis=0)) * strength
    n = np.stack([-dx, dy, np.ones_like(height)])
    n /= np.linalg.norm(n, axis=0)
    return np.asarray(np.rint((n * 0.5 + 0.5) * 255.0), dtype=np.uint8)


def _rgb(base: tuple[float, float, float], shade: F) -> U8:
    """Base colour times a brightness field around 1."""
    bands = [np.clip(c * shade, 0, 255) for c in base]
    return np.asarray(np.rint(np.stack(bands)), dtype=np.uint8)


def _grey(values: F) -> U8:
    return np.asarray(np.rint(np.clip(values, 0.0, 1.0) * 255.0), dtype=np.uint8)[None, :, :]


@dataclass(frozen=True)
class TextureSet:
    """color: (3, n, n) sRGB. normal: (3, n, n). roughness, ao, height: (1, n, n) grey."""

    color: U8
    normal: U8
    roughness: U8
    ao: U8
    height: U8
    tile_m: float  # how many metres one repeat of the texture covers


def asphalt(size: int = 1024, seed: int = 11) -> TextureSet:
    """Worn road asphalt: dark grey binder, fine pale stones, faint larger patches."""
    grain = tile_noise(size, seed, 0.3, low_cut=size / 6)  # stones of 1-3 pixels
    mid = tile_noise(size, seed + 1, 1.6, low_cut=6)
    patch = tile_noise(size, seed + 2, 2.4, low_cut=2)
    stones = np.clip((grain - 0.62) * 4.0, 0.0, 1.0)  # the pale tops of the aggregate
    shade = 0.80 + 0.22 * grain + 0.55 * stones + 0.12 * (mid - 0.5) + 0.10 * (patch - 0.5)
    height = 0.5 + 0.35 * (grain - 0.5) + 0.25 * stones
    return TextureSet(
        color=_rgb((66.0, 66.0, 69.0), shade),
        normal=normal_map(height, 2.2),
        roughness=_grey(0.86 - 0.18 * stones + 0.06 * (mid - 0.5)),
        ao=_grey(0.90 + 0.10 * grain),
        height=_grey(height),
        tile_m=4.0,
    )


def concrete(size: int = 1024, seed: int = 21) -> TextureSet:
    grain = tile_noise(size, seed, 0.6, low_cut=size / 10)
    patch = tile_noise(size, seed + 1, 2.2, low_cut=2)
    shade = 0.90 + 0.12 * grain + 0.16 * (patch - 0.5)
    height = 0.5 + 0.2 * (grain - 0.5)
    return TextureSet(
        color=_rgb((150.0, 148.0, 142.0), shade),
        normal=normal_map(height, 1.2),
        roughness=_grey(0.9 - 0.08 * grain),
        ao=_grey(0.94 + 0.06 * grain),
        height=_grey(height),
        tile_m=4.0,
    )


def gravel(
    size: int = 1024, seed: int = 31, base: tuple[float, float, float] = (132.0, 120.0, 100.0)
) -> TextureSet:
    """Loose stones on earth, for gravel roads and road shoulders."""
    pebbles = tile_noise(size, seed, 1.1, low_cut=size / 24)
    fine = tile_noise(size, seed + 1, 0.4, low_cut=size / 6)
    patch = tile_noise(size, seed + 2, 2.4, low_cut=2)
    shade = 0.62 + 0.55 * pebbles + 0.18 * fine + 0.14 * (patch - 0.5)
    height = 0.2 + 0.6 * pebbles + 0.2 * fine
    return TextureSet(
        color=_rgb(base, shade),
        normal=normal_map(height, 3.0),
        roughness=_grey(0.95 - 0.1 * pebbles),
        ao=_grey(0.75 + 0.25 * pebbles),
        height=_grey(height),
        tile_m=3.0,
    )


def cobble(size: int = 1024, seed: int = 41, stones_across: int = 16) -> TextureSet:
    """Rows of rounded setts with dark joints."""
    cell = size / stones_across
    y, x = np.mgrid[0:size, 0:size].astype(np.float64)
    row = np.floor(y / cell)
    xs = (x + (row % 2) * cell / 2) % size  # every other row is shifted by half a stone
    u, v = (xs % cell) / cell - 0.5, (y % cell) / cell - 0.5
    dome = np.clip(1.0 - (np.abs(u) * 2) ** 4 - (np.abs(v) * 2) ** 4, 0.0, 1.0) ** 0.5
    rng = np.random.default_rng(seed)
    tone = rng.uniform(0.82, 1.12, (stones_across, stones_across * 2))
    per_stone = tone[
        row.astype(int) % stones_across, np.floor(xs / cell * 2).astype(int) // 2 % (stones_across * 2)
    ]
    grain = tile_noise(size, seed + 1, 0.5, low_cut=size / 8)
    shade = (0.35 + 0.65 * dome) * per_stone * (0.9 + 0.2 * grain)
    return TextureSet(
        color=_rgb((118.0, 110.0, 102.0), shade),
        normal=normal_map(dome, 4.0),
        roughness=_grey(0.8 + 0.15 * (1 - dome)),
        ao=_grey(0.45 + 0.55 * dome),
        height=_grey(dome),
        tile_m=2.0,
    )


def grass(size: int = 1024, seed: int = 51) -> TextureSet:
    """Meadow seen from a car: blades as fine streaks, clumps, a few dry patches."""
    blades = tile_noise(size, seed, 0.5, low_cut=size / 5)
    clumps = tile_noise(size, seed + 1, 1.8, low_cut=5)
    dry = tile_noise(size, seed + 2, 2.6, low_cut=2)
    shade = 0.62 + 0.45 * blades + 0.30 * (clumps - 0.5)
    green = np.stack([np.full_like(shade, 74.0), np.full_like(shade, 104.0), np.full_like(shade, 46.0)])
    straw = np.stack([np.full_like(shade, 128.0), np.full_like(shade, 122.0), np.full_like(shade, 70.0)])
    mix = np.clip((dry - 0.62) * 2.2, 0.0, 0.55)  # where the grass has dried
    color = (green * (1 - mix) + straw * mix) * shade
    height = 0.3 + 0.4 * blades + 0.3 * clumps
    return TextureSet(
        color=np.asarray(np.rint(np.clip(color, 0, 255)), dtype=np.uint8),
        normal=normal_map(height, 2.5),
        roughness=_grey(0.92 - 0.06 * blades),
        ao=_grey(0.70 + 0.30 * blades),
        height=_grey(height),
        tile_m=3.0,
    )


def paint(size: int = 256, seed: int = 61) -> TextureSet:
    """Road-marking paint: off-white, slightly worn."""
    wear = tile_noise(size, seed, 1.2, low_cut=4)
    grain = tile_noise(size, seed + 1, 0.3, low_cut=size / 6)
    shade = 0.88 + 0.10 * grain - 0.10 * np.clip(wear - 0.7, 0, 1) * 3
    flat = np.full((size, size), 0.5)
    return TextureSet(
        color=_rgb((232.0, 232.0, 224.0), shade),
        normal=normal_map(flat + 0.1 * grain, 1.0),
        roughness=_grey(0.55 + 0.2 * grain),
        ao=_grey(np.ones((size, size))),
        height=_grey(flat),
        tile_m=2.0,
    )
