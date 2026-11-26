"""Turn the user's map name into ids each game accepts."""

from __future__ import annotations

import re
import unicodedata

AC_MAX = 32


def _ascii_slug(name: str) -> str:
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text


def ac_track_id(name: str) -> str:
    """Assetto Corsa: lowercase latin letters, digits, underscore, at most 32 characters."""
    slug = _ascii_slug(name)[:AC_MAX].strip("_")
    return slug or "track"


def beamng_level_id(name: str) -> str:
    """BeamNG: lowercase_with_underscores; must not start with a digit."""
    slug = _ascii_slug(name)[:48].strip("_") or "level"
    return f"level_{slug}" if slug[0].isdigit() else slug
