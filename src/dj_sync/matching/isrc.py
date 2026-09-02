from __future__ import annotations

import re

_ISRC_PATTERN = re.compile(r"^[A-Z]{2}[A-Z0-9]{3}[0-9]{7}$")


def normalize_isrc(value: str | None) -> str | None:
    """Return a compact uppercase ISRC, or None when the value is malformed.

    Spotify normally returns compact ISRCs, but catalogue metadata can contain
    lowercase values, whitespace, or hyphens. TIDAL's ISRC filter is strict
    enough that one malformed value can reject an entire batch with HTTP 400.
    """
    if value is None:
        return None

    normalized = value.strip().replace("-", "").replace(" ", "").upper()
    if not _ISRC_PATTERN.fullmatch(normalized):
        return None
    return normalized
