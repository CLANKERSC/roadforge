"""Licence bookkeeping: what the user must credit, and a check that no adapter forgets it."""

from __future__ import annotations

from collections.abc import Iterable

from forge_worker.brand import brand
from forge_worker.sources.base import SourceInfo

DISCLAIMER = "This map is an approximation made for games. It is not for navigation."


def problems(info: SourceInfo) -> list[str]:
    """Why this adapter must not be used (empty list = fine). Enforced by a test."""
    out = []
    if not info.licence.strip():
        out.append("missing licence")
    if not info.attribution.strip():
        out.append("missing attribution")
    if not info.licence_url.startswith("https://"):
        out.append("missing licence URL")
    if not info.redistributable_derived:
        out.append("derived data may not be redistributed, so it cannot feed the generator")
    return out


def credits_text(sources: Iterable[SourceInfo], year: int) -> str:
    """Body of CREDITS.txt for an export."""
    lines = [f"Made with {brand()['name']}.", DISCLAIMER, "", "Data sources:"]
    for info in sorted(sources, key=lambda i: (i.layer, i.id)):
        lines.append(
            f"- {info.name}: {info.attribution.format(year=year)} [{info.licence}] {info.licence_url}"
        )
    return "\n".join(lines) + "\n"
