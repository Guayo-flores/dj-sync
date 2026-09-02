from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import re
import unicodedata


_NON_WORD = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True, slots=True)
class MetadataScore:
    total: float
    title: float
    artist: float
    duration: float
    duration_difference_ms: int


def normalize_text(value: str) -> str:
    """Normalize catalogue text while preserving version-significant words.

    Terms such as ``remix``, ``live``, ``radio edit`` and ``extended`` are kept
    deliberately because dropping them can turn a safe match into the wrong DJ
    version of a track.
    """
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    ascii_text = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(_NON_WORD.sub(" ", ascii_text).split())


def text_similarity(left: str, right: str) -> float:
    normalized_left = normalize_text(left)
    normalized_right = normalize_text(right)
    if not normalized_left or not normalized_right:
        return 0.0
    return SequenceMatcher(None, normalized_left, normalized_right).ratio()


def artist_similarity(spotify_artist: str, tidal_artists: tuple[str, ...]) -> float:
    if not tidal_artists:
        return 0.0

    spotify_names = [name.strip() for name in spotify_artist.split(",") if name.strip()]
    if not spotify_names:
        return 0.0

    # Require the lead Spotify artist to appear strongly among TIDAL artists.
    # This is safer for DJ catalogues than comparing one long joined string,
    # where featured-artist formatting differs frequently between services.
    lead_score = max(text_similarity(spotify_names[0], name) for name in tidal_artists)

    spotify_joined = ", ".join(spotify_names)
    tidal_joined = ", ".join(tidal_artists)
    joined_score = text_similarity(spotify_joined, tidal_joined)
    return max(lead_score, joined_score)


def score_metadata_match(
    *,
    spotify_title: str,
    spotify_artist: str,
    spotify_duration_ms: int,
    tidal_title: str,
    tidal_artists: tuple[str, ...],
    tidal_duration_ms: int | None,
) -> MetadataScore:
    title_score = text_similarity(spotify_title, tidal_title)
    artist_score = artist_similarity(spotify_artist, tidal_artists)

    if tidal_duration_ms is None:
        duration_difference_ms = 60_000
        duration_score = 0.0
    else:
        duration_difference_ms = abs(spotify_duration_ms - tidal_duration_ms)
        duration_score = max(0.0, 1.0 - (duration_difference_ms / 15_000))

    total = (0.50 * title_score) + (0.35 * artist_score) + (0.15 * duration_score)
    return MetadataScore(
        total=round(total, 6),
        title=round(title_score, 6),
        artist=round(artist_score, 6),
        duration=round(duration_score, 6),
        duration_difference_ms=duration_difference_ms,
    )


def is_safe_automatic_match(score: MetadataScore) -> bool:
    """Only auto-link extremely strong metadata matches.

    A high aggregate score alone is not enough: title and artist must each be
    strong, and the recording duration must be within three seconds. Ambiguous
    remixes/edits are intentionally routed to manual review instead.
    """
    return (
        score.total >= 0.94
        and score.title >= 0.94
        and score.artist >= 0.90
        and score.duration_difference_ms <= 3_000
    )
