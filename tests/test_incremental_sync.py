from __future__ import annotations

from dj_sync.database.database import Database
from dj_sync.models import Track
from dj_sync.spotify.normalizer import NormalizedPlaylistTrack
from dj_sync.sync.incremental import build_incremental_sync_plan, execute_incremental_sync
from dj_sync.tidal.client import TidalPlaylist, TidalPlaylistAddResult, TidalPlaylistItem


def normalized(track_id: str, position: int) -> NormalizedPlaylistTrack:
    return NormalizedPlaylistTrack(
        track=Track(
            spotify_id=f"spotify-{track_id}",
            title=f"Song {track_id}",
            artist="Artist",
            duration_ms=180000,
            isrc=None,
            spotify_uri=f"spotify:track:spotify-{track_id}",
        ),
        position=position,
        added_at=None,
    )


class FakeIncrementalTidalClient:
    def __init__(self) -> None:
        self.playlists: dict[str, TidalPlaylist] = {}
        self.items: dict[str, list[TidalPlaylistItem]] = {}
        self.next_occurrence = 100

    def seed(self, playlist_id: str, name: str, track_ids: list[str]) -> None:
        self.playlists[playlist_id] = TidalPlaylist(id=playlist_id, name=name)
        self.items[playlist_id] = [self._item(track_id) for track_id in track_ids]

    def _item(self, track_id: str) -> TidalPlaylistItem:
        self.next_occurrence += 1
        return TidalPlaylistItem(
            id=track_id,
            type="tracks",
            item_id=f"item-{self.next_occurrence}",
        )

    def iter_owned_playlists(self):
        return iter(self.playlists.values())

    def get_playlist(self, playlist_id: str) -> TidalPlaylist:
        return self.playlists[playlist_id]

    def iter_playlist_items(self, playlist_id: str):
        yield from self.items.get(playlist_id, [])

    def create_playlist(self, name: str, *, description: str | None = None):
        playlist_id = f"created-{len(self.playlists) + 1}"
        playlist = TidalPlaylist(id=playlist_id, name=name)
        self.playlists[playlist_id] = playlist
        self.items[playlist_id] = []
        return playlist

    def update_playlist_name(self, playlist_id: str, name: str) -> TidalPlaylist:
        playlist = TidalPlaylist(id=playlist_id, name=name)
        self.playlists[playlist_id] = playlist
        return playlist

    def remove_playlist_items(self, playlist_id: str, items: list[TidalPlaylistItem]) -> None:
        remove_ids = {item.item_id for item in items}
        self.items[playlist_id] = [
            item for item in self.items[playlist_id] if item.item_id not in remove_ids
        ]

    def add_playlist_tracks(
        self,
        playlist_id: str,
        track_ids: list[str],
        *,
        position_before: str | None = None,
    ) -> TidalPlaylistAddResult:
        new_items = [self._item(track_id) for track_id in track_ids]
        if position_before is None:
            self.items[playlist_id].extend(new_items)
        else:
            index = next(
                index
                for index, item in enumerate(self.items[playlist_id])
                if item.item_id == position_before
            )
            self.items[playlist_id][index:index] = new_items
        return TidalPlaylistAddResult(added=len(track_ids))


def prepared_database(tmp_path, desired: list[str], *, name: str = "Hype Reggueton") -> Database:
    database = Database(tmp_path / "dj_sync.db")
    database.initialize()
    database.upsert_managed_playlist("spotify-playlist", name)
    database.replace_spotify_playlist_snapshot(
        "spotify-playlist",
        [normalized(track_id, position) for position, track_id in enumerate(desired)],
    )
    for track_id in set(desired):
        database.save_track_match(
            spotify_track_id=f"spotify-{track_id}",
            tidal_track_id=f"tidal-{track_id}",
            method="isrc",
            score=1.0,
        )
    database.save_tidal_playlist_mapping("spotify-playlist", "tidal-playlist", name)
    return database


def test_incremental_plan_detects_addition_and_removal(tmp_path) -> None:
    database = prepared_database(tmp_path, ["a", "d", "c"])
    client = FakeIncrementalTidalClient()
    client.seed("tidal-playlist", "Hype Reggueton", ["tidal-a", "tidal-b", "tidal-c"])

    plan = build_incremental_sync_plan(client=client, database=database)

    playlist = plan.playlists[0]
    assert playlist.action == "update"
    assert playlist.tracks_to_add == 1
    assert playlist.tracks_to_remove == 1


def test_incremental_executor_reconciles_reorder_and_duplicates(tmp_path) -> None:
    database = prepared_database(tmp_path, ["a", "a", "c", "b"])
    client = FakeIncrementalTidalClient()
    client.seed(
        "tidal-playlist",
        "Hype Reggueton",
        ["tidal-a", "tidal-b", "tidal-a", "tidal-c"],
    )
    plan = build_incremental_sync_plan(client=client, database=database)

    summary = execute_incremental_sync(client=client, database=database, plan=plan)

    assert [item.id for item in client.items["tidal-playlist"]] == [
        "tidal-a",
        "tidal-a",
        "tidal-c",
        "tidal-b",
    ]
    assert summary.tracks_added >= 1
    assert summary.tracks_removed >= 1


def test_incremental_executor_mirrors_playlist_rename(tmp_path) -> None:
    database = prepared_database(tmp_path, ["a"], name="New Spotify Name")
    client = FakeIncrementalTidalClient()
    client.seed("tidal-playlist", "Old TIDAL Name", ["tidal-a"])

    plan = build_incremental_sync_plan(client=client, database=database)
    assert plan.playlists[0].rename is True

    summary = execute_incremental_sync(client=client, database=database, plan=plan)

    assert client.playlists["tidal-playlist"].name == "New Spotify Name"
    assert summary.playlists_renamed == 1


def test_incremental_plan_can_scope_one_managed_playlist(tmp_path) -> None:
    database = prepared_database(tmp_path, ["a"])
    database.upsert_managed_playlist("spotify-other", "House")
    database.replace_spotify_playlist_snapshot("spotify-other", [normalized("b", 0)])
    database.save_track_match(
        spotify_track_id="spotify-b", tidal_track_id="tidal-b", method="isrc", score=1.0
    )
    client = FakeIncrementalTidalClient()
    client.seed("tidal-playlist", "Hype Reggueton", ["tidal-a"])

    plan = build_incremental_sync_plan(
        client=client, database=database, playlist_name="Hype Reggueton"
    )

    assert len(plan.playlists) == 1
    assert plan.playlists[0].spotify_name == "Hype Reggueton"


def test_incremental_executor_creates_newly_managed_playlist(tmp_path) -> None:
    database = Database(tmp_path / "dj_sync.db")
    database.initialize()
    database.upsert_managed_playlist("spotify-new", "New Playlist")
    database.replace_spotify_playlist_snapshot("spotify-new", [normalized("a", 0)])
    database.save_track_match(
        spotify_track_id="spotify-a", tidal_track_id="tidal-a", method="isrc", score=1.0
    )
    client = FakeIncrementalTidalClient()

    plan = build_incremental_sync_plan(client=client, database=database)
    summary = execute_incremental_sync(client=client, database=database, plan=plan)

    assert summary.playlists_created == 1
    created_id = next(iter(client.playlists))
    assert client.playlists[created_id].name == "New Playlist"
    assert [item.id for item in client.items[created_id]] == ["tidal-a"]
