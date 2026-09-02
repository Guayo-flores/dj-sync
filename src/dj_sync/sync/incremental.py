from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher

import requests

from dj_sync.database.database import Database
from dj_sync.sync.planner import UnmatchedPlaylistTrack
from dj_sync.tidal.client import TidalClient, TidalPlaylistItem


@dataclass(frozen=True, slots=True)
class IncrementalPlaylistPlan:
    spotify_playlist_id: str
    spotify_name: str
    tidal_playlist_id: str | None
    tidal_name: str | None
    desired_track_ids: tuple[str, ...]
    current_items: tuple[TidalPlaylistItem, ...]
    unmatched_entries: tuple[UnmatchedPlaylistTrack, ...]
    tracks_to_add: int
    tracks_to_remove: int
    rename: bool

    @property
    def action(self) -> str:
        if self.tidal_playlist_id is None:
            return "create"
        if self.tracks_to_add or self.tracks_to_remove or self.rename:
            return "update"
        return "unchanged"


@dataclass(frozen=True, slots=True)
class IncrementalSyncPlan:
    playlists: tuple[IncrementalPlaylistPlan, ...]

    @property
    def playlists_to_create(self) -> int:
        return sum(1 for playlist in self.playlists if playlist.action == "create")

    @property
    def playlists_changed(self) -> int:
        return sum(1 for playlist in self.playlists if playlist.action != "unchanged")

    @property
    def tracks_to_add(self) -> int:
        return sum(playlist.tracks_to_add for playlist in self.playlists)

    @property
    def tracks_to_remove(self) -> int:
        return sum(playlist.tracks_to_remove for playlist in self.playlists)

    @property
    def unmatched_entries(self) -> int:
        return sum(len(playlist.unmatched_entries) for playlist in self.playlists)


def _delta_counts(current_ids: tuple[str, ...], desired_ids: tuple[str, ...]) -> tuple[int, int]:
    matcher = SequenceMatcher(a=current_ids, b=desired_ids, autojunk=False)
    additions = 0
    removals = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in {"insert", "replace"}:
            additions += j2 - j1
        if tag in {"delete", "replace"}:
            removals += i2 - i1
    return additions, removals


def build_incremental_sync_plan(
    *,
    client: TidalClient,
    database: Database,
    playlist_name: str | None = None,
) -> IncrementalSyncPlan:
    """Compare the latest Spotify snapshot with the live TIDAL mirrors.

    Spotify is the source of truth. This planner reads TIDAL but never writes to it.
    A playlist can be scoped by exact name for controlled real-account testing.
    """
    managed = database.list_managed_playlists_for_sync()
    if playlist_name is not None:
        matches = [
            playlist
            for playlist in managed
            if str(playlist["spotify_name"]).casefold() == playlist_name.casefold()
        ]
        if not matches:
            raise ValueError(f'No managed Spotify playlist named "{playlist_name}"')
        if len(matches) > 1:
            raise ValueError(
                f'Multiple managed playlists are named "{playlist_name}"; '
                "use unique playlist names before scoped syncing."
            )
        managed = matches

    plans: list[IncrementalPlaylistPlan] = []
    for playlist in managed:
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
        desired_ids = tuple(
            str(row["tidal_track_id"])
            for row in entries
            if row["tidal_track_id"] is not None
        )

        tidal_playlist_id = playlist["tidal_playlist_id"]
        current_items: tuple[TidalPlaylistItem, ...] = ()
        tidal_name = str(playlist["tidal_name"]) if playlist["tidal_name"] else None
        rename = False
        if tidal_playlist_id:
            live_playlist = client.get_playlist(str(tidal_playlist_id))
            tidal_name = live_playlist.name
            rename = live_playlist.name != str(playlist["spotify_name"])
            current_items = tuple(client.iter_playlist_items(str(tidal_playlist_id)))
            non_tracks = [item for item in current_items if item.type != "tracks"]
            if non_tracks:
                raise RuntimeError(
                    f'TIDAL playlist "{playlist["spotify_name"]}" contains non-track items; '
                    "DJ Sync will not reconcile it automatically."
                )

        current_ids = tuple(item.id for item in current_items)
        additions, removals = _delta_counts(current_ids, desired_ids)
        plans.append(
            IncrementalPlaylistPlan(
                spotify_playlist_id=str(playlist["spotify_playlist_id"]),
                spotify_name=str(playlist["spotify_name"]),
                tidal_playlist_id=str(tidal_playlist_id) if tidal_playlist_id else None,
                tidal_name=tidal_name,
                desired_track_ids=desired_ids,
                current_items=current_items,
                unmatched_entries=unmatched,
                tracks_to_add=additions,
                tracks_to_remove=removals,
                rename=rename,
            )
        )

    return IncrementalSyncPlan(playlists=tuple(plans))


@dataclass(frozen=True, slots=True)
class _InsertionGroup:
    track_ids: tuple[str, ...]
    position_before: str | None


@dataclass(frozen=True, slots=True)
class PlaylistIncrementalResult:
    name: str
    created: bool
    renamed: bool
    tracks_added: int
    tracks_removed: int
    unmatched_skipped: int


@dataclass(frozen=True, slots=True)
class IncrementalExecutionSummary:
    playlists: tuple[PlaylistIncrementalResult, ...]

    @property
    def playlists_created(self) -> int:
        return sum(1 for item in self.playlists if item.created)

    @property
    def playlists_renamed(self) -> int:
        return sum(1 for item in self.playlists if item.renamed)

    @property
    def tracks_added(self) -> int:
        return sum(item.tracks_added for item in self.playlists)

    @property
    def tracks_removed(self) -> int:
        return sum(item.tracks_removed for item in self.playlists)


def _chunks_list(values: tuple[TidalPlaylistItem, ...], size: int = 50):
    for index in range(0, len(values), size):
        yield list(values[index : index + size])


def _chunks_ids(values: tuple[str, ...], size: int = 50):
    for index in range(0, len(values), size):
        yield list(values[index : index + size])


def _reconcile_operations(
    current_items: tuple[TidalPlaylistItem, ...], desired_ids: tuple[str, ...]
) -> tuple[tuple[TidalPlaylistItem, ...], tuple[_InsertionGroup, ...]]:
    """Return occurrence removals and ordered insertion groups.

    TIDAL removal is occurrence-specific through ``meta.itemId``. SequenceMatcher
    gives us a stable common subsequence, so we remove everything outside that
    subsequence and insert the missing desired segments before surviving item IDs.
    This supports additions, removals, duplicate tracks, and reorders without
    replacing the entire playlist.
    """
    current_ids = tuple(item.id for item in current_items)
    matcher = SequenceMatcher(a=current_ids, b=desired_ids, autojunk=False)
    removals: list[TidalPlaylistItem] = []
    insertions: list[_InsertionGroup] = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in {"delete", "replace"}:
            removals.extend(current_items[i1:i2])
        if tag in {"insert", "replace"} and j1 != j2:
            position_before = None
            if i2 < len(current_items):
                position_before = current_items[i2].item_id
                if position_before is None:
                    raise RuntimeError(
                        "TIDAL did not return itemId metadata required for ordered insertion"
                    )
            insertions.append(
                _InsertionGroup(
                    track_ids=desired_ids[j1:j2],
                    position_before=position_before,
                )
            )

    return tuple(removals), tuple(insertions)


def _reconcile_playlist_items(
    *,
    client: TidalClient,
    playlist_id: str,
    current_items: tuple[TidalPlaylistItem, ...],
    desired_ids: tuple[str, ...],
) -> tuple[int, int]:
    removals, insertions = _reconcile_operations(current_items, desired_ids)

    for batch in _chunks_list(removals, 50):
        client.remove_playlist_items(playlist_id, batch)

    added = 0
    for group in insertions:
        for batch in _chunks_ids(group.track_ids, 50):
            result = client.add_playlist_tracks(
                playlist_id,
                batch,
                position_before=group.position_before,
            )
            if result.skipped_ids:
                raise RuntimeError(
                    "TIDAL skipped mapped track(s) while reconciling playlist: "
                    + ", ".join(result.skipped_ids)
                )
            added += result.added

    final_items = tuple(client.iter_playlist_items(playlist_id))
    if any(item.type != "tracks" for item in final_items):
        raise RuntimeError("TIDAL playlist contains non-track items after reconciliation")
    final_ids = tuple(item.id for item in final_items)
    if final_ids != desired_ids:
        raise RuntimeError(
            "TIDAL playlist verification failed after reconciliation; "
            "DJ Sync stopped instead of assuming success."
        )

    return added, len(removals)



def _create_verified_replacement_playlist(
    *,
    client: TidalClient,
    name: str,
    desired_ids: tuple[str, ...],
) -> str:
    """Create a replacement mirror and verify it before returning its id.

    This is used only when TIDAL rejects the documented playlist PATCH rename
    operation. The old playlist is deliberately left untouched until this new
    mirror has been populated and verified in Spotify order.
    """
    replacement = client.create_playlist(
        name,
        description="Mirrored from Spotify by DJ Sync.",
    )
    try:
        for batch in _chunks_ids(desired_ids, 50):
            result = client.add_playlist_tracks(replacement.id, batch)
            if result.skipped_ids:
                raise RuntimeError(
                    "TIDAL skipped mapped track(s) while creating rename fallback: "
                    + ", ".join(result.skipped_ids)
                )

        replacement_items = tuple(client.iter_playlist_items(replacement.id))
        replacement_ids = tuple(item.id for item in replacement_items)
        if replacement_ids != desired_ids:
            raise RuntimeError(
                "TIDAL rename fallback verification failed; the original playlist "
                "was left untouched."
            )
        return replacement.id
    except Exception:
        # Best-effort cleanup. Never sacrifice the known-good original playlist
        # when constructing the replacement fails.
        try:
            client.delete_playlist(replacement.id)
        except Exception:
            pass
        raise


def _rename_with_safe_fallback(
    *,
    client: TidalClient,
    database: Database,
    playlist: IncrementalPlaylistPlan,
    tidal_playlist_id: str,
) -> tuple[str, bool]:
    """Rename a managed mirror, recreating it only on TIDAL PATCH 403.

    TIDAL documents PATCH /playlists/{id} for third-party ``playlists.write``
    apps, but some developer accounts currently receive 403 specifically for
    that operation while create/add/remove continue to work. In that narrow
    case, make and verify a replacement first, then delete the old DJ Sync
    mirror and atomically move our mapping to the replacement.
    """
    try:
        updated = client.update_playlist_name(tidal_playlist_id, playlist.spotify_name)
    except requests.HTTPError as exc:
        response = exc.response
        if response is None or response.status_code != 403:
            raise

        replacement_id = _create_verified_replacement_playlist(
            client=client,
            name=playlist.spotify_name,
            desired_ids=playlist.desired_track_ids,
        )
        try:
            client.delete_playlist(tidal_playlist_id)
        except Exception:
            # Keep the original authoritative mirror if we cannot finish the
            # swap. Remove the temporary replacement when possible.
            try:
                client.delete_playlist(replacement_id)
            except Exception:
                pass
            raise RuntimeError(
                "TIDAL rejected playlist rename and DJ Sync could not safely "
                "replace the old mirror; no database mapping was changed."
            ) from exc

        database.save_tidal_playlist_mapping(
            playlist.spotify_playlist_id,
            replacement_id,
            playlist.spotify_name,
        )
        return replacement_id, True

    database.save_tidal_playlist_mapping(
        playlist.spotify_playlist_id, tidal_playlist_id, updated.name
    )
    return tidal_playlist_id, False

def execute_incremental_sync(
    *,
    client: TidalClient,
    database: Database,
    plan: IncrementalSyncPlan,
) -> IncrementalExecutionSummary:
    """Make managed TIDAL playlists exactly mirror the mapped Spotify snapshot."""
    results: list[PlaylistIncrementalResult] = []

    # Listing every owned TIDAL playlist requires playlists.read and is only
    # needed before creating a brand-new mirror (to avoid same-name duplicates).
    # Existing mapped playlists already have stable TIDAL IDs, so updates/renames
    # should not depend on a full-account collection scan.
    unmapped = [playlist for playlist in plan.playlists if playlist.tidal_playlist_id is None]
    if unmapped:
        owned_by_name: dict[str, list[str]] = {}
        for tidal_playlist in client.iter_owned_playlists():
            owned_by_name.setdefault(tidal_playlist.name.casefold(), []).append(
                tidal_playlist.id
            )
        collisions = [
            playlist.spotify_name
            for playlist in unmapped
            if playlist.spotify_name.casefold() in owned_by_name
        ]
        if collisions:
            names = ", ".join(f'"{name}"' for name in collisions)
            raise RuntimeError(
                "Existing TIDAL playlist name collision(s): "
                + names
                + ". DJ Sync made no changes."
            )

    for playlist in plan.playlists:
        tidal_playlist_id = playlist.tidal_playlist_id
        created = False
        renamed = False
        if tidal_playlist_id is None:
            created_playlist = client.create_playlist(
                playlist.spotify_name,
                description="Mirrored from Spotify by DJ Sync.",
            )
            tidal_playlist_id = created_playlist.id
            database.save_tidal_playlist_mapping(
                playlist.spotify_playlist_id,
                tidal_playlist_id,
                created_playlist.name,
            )
            current_items: tuple[TidalPlaylistItem, ...] = ()
            created = True
        else:
            current_items = tuple(client.iter_playlist_items(tidal_playlist_id))
            if any(item.type != "tracks" for item in current_items):
                raise RuntimeError(
                    f'TIDAL playlist "{playlist.spotify_name}" contains non-track items; '
                    "DJ Sync will not modify it automatically."
                )

        rename_recreated = False
        if playlist.rename and not created:
            tidal_playlist_id, rename_recreated = _rename_with_safe_fallback(
                client=client,
                database=database,
                playlist=playlist,
                tidal_playlist_id=tidal_playlist_id,
            )
            renamed = True

        if rename_recreated:
            # The replacement was built directly from the desired Spotify
            # snapshot, so there is nothing left to reconcile. Report the
            # semantic delta from the plan rather than the physical full copy.
            added = playlist.tracks_to_add
            removed = playlist.tracks_to_remove
        else:
            added, removed = _reconcile_playlist_items(
                client=client,
                playlist_id=tidal_playlist_id,
                current_items=current_items,
                desired_ids=playlist.desired_track_ids,
            )
        database.mark_playlist_synced(playlist.spotify_playlist_id)
        results.append(
            PlaylistIncrementalResult(
                name=playlist.spotify_name,
                created=created,
                renamed=renamed,
                tracks_added=added,
                tracks_removed=removed,
                unmatched_skipped=len(playlist.unmatched_entries),
            )
        )

    return IncrementalExecutionSummary(playlists=tuple(results))
