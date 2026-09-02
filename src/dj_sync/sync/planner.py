from __future__ import annotations

from dataclasses import dataclass

from dj_sync.database.database import Database


@dataclass(frozen=True, slots=True)
class UnmatchedPlaylistTrack:
    spotify_track_id: str
    title: str
    artist: str
    position: int


@dataclass(frozen=True, slots=True)
class PlaylistSyncPlan:
    spotify_playlist_id: str
    spotify_name: str
    tidal_playlist_id: str | None
    action: str
    playlist_entries: int
    mapped_entries: int
    tidal_track_ids: tuple[str, ...]
    unmatched_entries: tuple[UnmatchedPlaylistTrack, ...]

    @property
    def unmatched_count(self) -> int:
        return len(self.unmatched_entries)


@dataclass(frozen=True, slots=True)
class SyncPlan:
    playlists: tuple[PlaylistSyncPlan, ...]
    unique_unmatched_tracks: int

    @property
    def playlists_to_create(self) -> int:
        return sum(1 for playlist in self.playlists if playlist.action == "create")

    @property
    def mapped_entries(self) -> int:
        return sum(playlist.mapped_entries for playlist in self.playlists)

    @property
    def unmatched_entries(self) -> int:
        return sum(playlist.unmatched_count for playlist in self.playlists)


def build_sync_plan(database: Database) -> SyncPlan:
    """Build a read-only plan for mirroring managed Spotify playlists to TIDAL.

    The plan never writes to TIDAL. Missing track mappings are surfaced and skipped
    rather than blocking an otherwise healthy playlist from being synchronized.
    """
    playlists: list[PlaylistSyncPlan] = []
    unique_unmatched: set[str] = set()

    for playlist in database.list_managed_playlists_for_sync():
        entries = database.list_playlist_tracks_for_sync(int(playlist["id"]))
        unmatched = tuple(
            UnmatchedPlaylistTrack(
                spotify_track_id=str(row["spotify_track_id"]),
                title=str(row["title"]),
                artist=str(row["artist"]),
                position=int(row["position"]),
            )
            for row in entries
            if row["tidal_track_id"] is None
        )
        unique_unmatched.update(item.spotify_track_id for item in unmatched)
        mapped_entries = sum(1 for row in entries if row["tidal_track_id"] is not None)
        tidal_playlist_id = playlist["tidal_playlist_id"]

        playlists.append(
            PlaylistSyncPlan(
                spotify_playlist_id=str(playlist["spotify_playlist_id"]),
                spotify_name=str(playlist["spotify_name"]),
                tidal_playlist_id=str(tidal_playlist_id) if tidal_playlist_id else None,
                action="update" if tidal_playlist_id else "create",
                playlist_entries=len(entries),
                mapped_entries=mapped_entries,
                tidal_track_ids=tuple(
                    str(row["tidal_track_id"])
                    for row in entries
                    if row["tidal_track_id"] is not None
                ),
                unmatched_entries=unmatched,
            )
        )

    return SyncPlan(
        playlists=tuple(playlists),
        unique_unmatched_tracks=len(unique_unmatched),
    )
