from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class PlaylistStatus(StrEnum):
    MANAGED = "managed"
    PAUSED = "paused"


@dataclass(frozen=True, slots=True)
class Track:
    spotify_id: str
    title: str
    artist: str
    duration_ms: int
    isrc: str | None = None
    album: str | None = None
    spotify_uri: str | None = None


@dataclass(frozen=True, slots=True)
class PlaylistTrack:
    spotify_track_id: str
    position: int


@dataclass(frozen=True, slots=True)
class PlaylistSnapshot:
    spotify_playlist_id: str
    name: str
    tracks: tuple[PlaylistTrack, ...]
