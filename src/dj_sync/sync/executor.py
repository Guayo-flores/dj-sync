from __future__ import annotations

from dataclasses import dataclass

from dj_sync.database.database import Database
from dj_sync.sync.planner import PlaylistSyncPlan, SyncPlan
from dj_sync.tidal.client import TidalClient


@dataclass(frozen=True, slots=True)
class PlaylistExecutionResult:
    name: str
    created: bool
    tracks_added: int
    already_present: int
    unmatched_skipped: int


@dataclass(frozen=True, slots=True)
class SyncExecutionSummary:
    playlists: tuple[PlaylistExecutionResult, ...]

    @property
    def playlists_created(self) -> int:
        return sum(1 for item in self.playlists if item.created)

    @property
    def tracks_added(self) -> int:
        return sum(item.tracks_added for item in self.playlists)

    @property
    def unmatched_skipped(self) -> int:
        return sum(item.unmatched_skipped for item in self.playlists)


def _chunks(values: tuple[str, ...], size: int = 50):
    for index in range(0, len(values), size):
        yield values[index : index + size]


def _ensure_prefix_or_complete(
    *, playlist: PlaylistSyncPlan, current_ids: tuple[str, ...]
) -> tuple[str, ...]:
    desired_ids = playlist.tidal_track_ids
    if current_ids == desired_ids:
        return ()
    if len(current_ids) <= len(desired_ids) and desired_ids[: len(current_ids)] == current_ids:
        return desired_ids[len(current_ids) :]

    raise RuntimeError(
        f'TIDAL playlist "{playlist.spotify_name}" no longer matches DJ Sync\'s '
        "expected prefix. DJ Sync stopped before changing it. Reconciliation of "
        "removals/reorders is the next sync milestone."
    )


def execute_initial_sync(
    *, client: TidalClient, database: Database, plan: SyncPlan
) -> SyncExecutionSummary:
    """Create/populate managed TIDAL mirrors safely and resumably.

    New playlist mappings are persisted immediately after creation. If a run is
    interrupted, a rerun reads the existing TIDAL playlist and only appends the
    missing tail when its current items are an exact prefix of the desired state.
    Existing playlists that have diverged are never overwritten by this milestone.
    """
    results: list[PlaylistExecutionResult] = []

    # Never create a duplicate merely because DJ Sync has no mapping yet. A
    # same-name playlist in the user's TIDAL account may be valuable/manual, so
    # stop before any writes and let a later linking flow resolve it explicitly.
    owned_by_name: dict[str, list[str]] = {}
    for tidal_playlist in client.iter_owned_playlists():
        owned_by_name.setdefault(tidal_playlist.name.casefold(), []).append(
            tidal_playlist.id
        )
    collisions = [
        playlist.spotify_name
        for playlist in plan.playlists
        if playlist.tidal_playlist_id is None
        and playlist.spotify_name.casefold() in owned_by_name
    ]
    if collisions:
        names = ", ".join(f'"{name}"' for name in collisions)
        raise RuntimeError(
            "Existing TIDAL playlist name collision(s): "
            + names
            + ". DJ Sync made no changes. Link/reuse of existing playlists must "
              "be resolved explicitly instead of creating duplicates."
        )

    for playlist in plan.playlists:
        tidal_playlist_id = playlist.tidal_playlist_id
        created = False
        if tidal_playlist_id is None:
            created_playlist = client.create_playlist(
                playlist.spotify_name,
                description="Mirrored from Spotify by DJ Sync.",
            )
            tidal_playlist_id = created_playlist.id
            # Persist before adding the first track so a crash cannot create a
            # second TIDAL playlist on the next run.
            database.save_tidal_playlist_mapping(
                playlist.spotify_playlist_id,
                tidal_playlist_id,
                created_playlist.name,
            )
            created = True

        current_items = tuple(client.iter_playlist_items(tidal_playlist_id))
        non_tracks = [item for item in current_items if item.type != "tracks"]
        if non_tracks:
            raise RuntimeError(
                f'TIDAL playlist "{playlist.spotify_name}" contains non-track items; '
                "DJ Sync will not modify it automatically."
            )
        current_ids = tuple(item.id for item in current_items)
        remaining = _ensure_prefix_or_complete(
            playlist=playlist, current_ids=current_ids
        )

        added = 0
        for batch in _chunks(remaining, 50):
            result = client.add_playlist_tracks(tidal_playlist_id, list(batch))
            if result.skipped_ids:
                raise RuntimeError(
                    f'TIDAL skipped {len(result.skipped_ids)} mapped track(s) while '
                    f'populating "{playlist.spotify_name}": '
                    + ", ".join(result.skipped_ids)
                )
            added += result.added

        database.mark_playlist_synced(playlist.spotify_playlist_id)
        results.append(
            PlaylistExecutionResult(
                name=playlist.spotify_name,
                created=created,
                tracks_added=added,
                already_present=len(current_ids),
                unmatched_skipped=playlist.unmatched_count,
            )
        )

    return SyncExecutionSummary(playlists=tuple(results))
