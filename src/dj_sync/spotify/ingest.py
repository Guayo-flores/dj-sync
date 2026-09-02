from __future__ import annotations

from dataclasses import dataclass

from dj_sync.database.database import Database
from dj_sync.spotify.client import SpotifyClient
from dj_sync.spotify.normalizer import NormalizedPlaylistTrack, normalize_playlist_item


@dataclass(frozen=True, slots=True)
class PlaylistIngestResult:
    playlist_id: str
    name: str
    items_seen: int
    tracks_saved: int
    skipped_items: int


@dataclass(frozen=True, slots=True)
class IngestSummary:
    playlists: tuple[PlaylistIngestResult, ...]
    unique_tracks: int

    @property
    def total_items_seen(self) -> int:
        return sum(item.items_seen for item in self.playlists)

    @property
    def total_tracks_saved(self) -> int:
        return sum(item.tracks_saved for item in self.playlists)

    @property
    def total_skipped_items(self) -> int:
        return sum(item.skipped_items for item in self.playlists)


def ingest_managed_playlists(
    *, client: SpotifyClient, database: Database
) -> IngestSummary:
    results: list[PlaylistIngestResult] = []

    for playlist in database.list_managed_playlists():
        normalized: list[NormalizedPlaylistTrack] = []
        items_seen = 0
        skipped = 0

        for position, raw_item in enumerate(
            client.iter_playlist_items(playlist["spotify_playlist_id"])
        ):
            items_seen += 1
            item = normalize_playlist_item(raw_item, position=position)
            if item is None:
                skipped += 1
                continue
            normalized.append(item)

        database.replace_spotify_playlist_snapshot(
            playlist["spotify_playlist_id"], normalized
        )
        results.append(
            PlaylistIngestResult(
                playlist_id=playlist["spotify_playlist_id"],
                name=playlist["spotify_name"],
                items_seen=items_seen,
                tracks_saved=len(normalized),
                skipped_items=skipped,
            )
        )

    return IngestSummary(playlists=tuple(results), unique_tracks=database.count_tracks())
