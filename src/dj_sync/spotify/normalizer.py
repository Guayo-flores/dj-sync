from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dj_sync.models import Track


@dataclass(frozen=True, slots=True)
class NormalizedPlaylistTrack:
    track: Track
    position: int
    added_at: str | None


def normalize_playlist_item(
    payload: dict[str, Any], *, position: int
) -> NormalizedPlaylistTrack | None:
    """Normalize one Spotify playlist item into DJ Sync's track model.

    Spotify's current playlist-items response uses ``item`` for the media
    resource. ``track`` is accepted as a compatibility fallback because older
    payloads and transitional examples can still expose that key.
    """
    if payload.get("is_local"):
        return None

    media = payload.get("item") or payload.get("track")
    if not isinstance(media, dict):
        return None
    if media.get("type") != "track":
        return None

    spotify_id = media.get("id")
    title = media.get("name")
    duration_ms = media.get("duration_ms")
    if not spotify_id or not title or not isinstance(duration_ms, int):
        return None

    artists = [
        artist.get("name", "").strip()
        for artist in (media.get("artists") or [])
        if isinstance(artist, dict) and artist.get("name")
    ]
    artist_text = ", ".join(name for name in artists if name)
    if not artist_text:
        return None

    album = media.get("album") or {}
    external_ids = media.get("external_ids") or {}

    track = Track(
        spotify_id=spotify_id,
        title=title,
        artist=artist_text,
        duration_ms=duration_ms,
        isrc=external_ids.get("isrc"),
        album=album.get("name") if isinstance(album, dict) else None,
        spotify_uri=media.get("uri"),
    )
    return NormalizedPlaylistTrack(
        track=track,
        position=position,
        added_at=payload.get("added_at"),
    )
