from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from dj_sync.database.database import Database
from dj_sync.spotify.client import SpotifyClient
from dj_sync.tidal.client import TidalClient


@dataclass(frozen=True, slots=True)
class PlaylistLifecycleSummary:
    visible_playlists: int
    renamed: tuple[str, ...]
    newly_missing: tuple[str, ...]
    restored: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PlaylistCleanupSummary:
    deleted: int
    kept: int
    skipped: int
    remaining: int


def refresh_playlist_lifecycle(
    *, client: SpotifyClient, database: Database
) -> PlaylistLifecycleSummary:
    """Refresh managed playlist names and safely detect Spotify deletions.

    A playlist missing from Spotify is *not* deleted on TIDAL here. It is marked
    pending deletion and removed from active ingest/sync until the user resolves
    it with ``dj-sync playlist-cleanup``. If it becomes visible again before
    cleanup, the pending flag is cleared automatically.
    """
    visible = {playlist.id: playlist for playlist in client.iter_playlists()}
    renamed: list[str] = []
    newly_missing: list[str] = []
    restored: list[str] = []

    for row in database.list_managed_playlist_lifecycle_rows():
        spotify_id = str(row["spotify_playlist_id"])
        existing_name = str(row["spotify_name"])
        was_pending = bool(row["pending_deletion"])
        live = visible.get(spotify_id)

        if live is None:
            if not was_pending:
                database.mark_playlist_pending_deletion(spotify_id)
                newly_missing.append(existing_name)
            continue

        if live.name != existing_name:
            renamed.append(f"{existing_name} → {live.name}")
        if was_pending:
            restored.append(live.name)
        database.refresh_managed_playlist_metadata(spotify_id, live.name)

    return PlaylistLifecycleSummary(
        visible_playlists=len(visible),
        renamed=tuple(renamed),
        newly_missing=tuple(newly_missing),
        restored=tuple(restored),
    )


def cleanup_pending_playlists(
    *,
    client: TidalClient,
    database: Database,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> PlaylistCleanupSummary:
    pending = database.list_pending_deletion_playlists()
    if not pending:
        output_fn("No playlists are awaiting deletion cleanup.")
        return PlaylistCleanupSummary(deleted=0, kept=0, skipped=0, remaining=0)

    output_fn(f"DJ Sync — Playlist cleanup ({len(pending)} pending)")
    output_fn(
        "A missing Spotify playlist is never deleted from TIDAL without this explicit decision."
    )

    deleted = 0
    kept = 0
    skipped = 0

    for index, row in enumerate(pending, start=1):
        name = str(row["spotify_name"])
        tidal_id = str(row["tidal_playlist_id"]) if row["tidal_playlist_id"] else None
        tidal_name = str(row["tidal_name"]) if row["tidal_name"] else name

        output_fn(f"\n[{index}/{len(pending)}] Spotify playlist missing: {name}")
        if tidal_id:
            output_fn(f"TIDAL copy: {tidal_name} [{tidal_id}]")
        else:
            output_fn("TIDAL copy: not created yet")

        while True:
            choice = input_fn(
                "[D]elete TIDAL copy  [K]eep TIDAL copy + pause  [S]kip  [Q]uit: "
            ).strip().lower()
            if choice in {"d", "k", "s", "q"}:
                break
            output_fn("Choose D, K, S, or Q.")

        if choice == "q":
            break
        if choice == "s":
            skipped += 1
            continue
        if choice == "k":
            database.keep_pending_playlist_as_paused(str(row["spotify_playlist_id"]))
            kept += 1
            output_fn("  ✓ TIDAL copy kept; DJ Sync will no longer manage it.")
            continue

        if tidal_id:
            client.delete_playlist(tidal_id)
        database.delete_playlist_record(str(row["spotify_playlist_id"]))
        deleted += 1
        output_fn("  ✓ TIDAL copy deleted and DJ Sync mapping removed.")

    remaining = len(database.list_pending_deletion_playlists())
    return PlaylistCleanupSummary(
        deleted=deleted,
        kept=kept,
        skipped=skipped,
        remaining=remaining,
    )
