from __future__ import annotations

from dj_sync.database.database import Database
from dj_sync.spotify.client import SpotifyPlaylist
from dj_sync.sync.lifecycle import cleanup_pending_playlists, refresh_playlist_lifecycle
from dj_sync.tidal.client import TidalPlaylist


class FakeSpotifyLifecycleClient:
    def __init__(self, playlists: list[SpotifyPlaylist]) -> None:
        self.playlists = playlists

    def iter_playlists(self):
        yield from self.playlists


class FakeCleanupTidalClient:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    def delete_playlist(self, playlist_id: str) -> None:
        self.deleted.append(playlist_id)


def spotify_playlist(id_: str, name: str) -> SpotifyPlaylist:
    return SpotifyPlaylist(
        id=id_,
        name=name,
        public=False,
        collaborative=False,
        owner_display_name="Eduardo",
        item_count=1,
        can_read_items=True,
    )


def test_lifecycle_detects_rename_and_missing_playlist(tmp_path) -> None:
    database = Database(tmp_path / "dj_sync.db")
    database.initialize()
    database.upsert_managed_playlist("spotify-a", "Old Name")
    database.upsert_managed_playlist("spotify-b", "Deleted Set")

    summary = refresh_playlist_lifecycle(
        client=FakeSpotifyLifecycleClient([spotify_playlist("spotify-a", "New Name")]),
        database=database,
    )

    assert summary.renamed == ("Old Name → New Name",)
    assert summary.newly_missing == ("Deleted Set",)
    active = database.list_managed_playlists()
    assert [row["spotify_name"] for row in active] == ["New Name"]
    pending = database.list_pending_deletion_playlists()
    assert [row["spotify_name"] for row in pending] == ["Deleted Set"]


def test_lifecycle_restores_playlist_before_cleanup(tmp_path) -> None:
    database = Database(tmp_path / "dj_sync.db")
    database.initialize()
    database.upsert_managed_playlist("spotify-a", "My Set")
    database.mark_playlist_pending_deletion("spotify-a")

    summary = refresh_playlist_lifecycle(
        client=FakeSpotifyLifecycleClient([spotify_playlist("spotify-a", "My Set")]),
        database=database,
    )

    assert summary.restored == ("My Set",)
    assert database.list_pending_deletion_playlists() == []
    assert len(database.list_managed_playlists()) == 1


def test_cleanup_can_delete_tidal_copy_and_mapping(tmp_path) -> None:
    database = Database(tmp_path / "dj_sync.db")
    database.initialize()
    database.upsert_managed_playlist("spotify-a", "Old Set")
    database.save_tidal_playlist_mapping("spotify-a", "tidal-a", "Old Set")
    database.mark_playlist_pending_deletion("spotify-a")
    client = FakeCleanupTidalClient()

    summary = cleanup_pending_playlists(
        client=client,
        database=database,
        input_fn=lambda _: "d",
        output_fn=lambda _: None,
    )

    assert client.deleted == ["tidal-a"]
    assert summary.deleted == 1
    assert database.list_playlists() == []


def test_cleanup_can_keep_tidal_copy_and_pause_mapping(tmp_path) -> None:
    database = Database(tmp_path / "dj_sync.db")
    database.initialize()
    database.upsert_managed_playlist("spotify-a", "Old Set")
    database.save_tidal_playlist_mapping("spotify-a", "tidal-a", "Old Set")
    database.mark_playlist_pending_deletion("spotify-a")
    client = FakeCleanupTidalClient()

    summary = cleanup_pending_playlists(
        client=client,
        database=database,
        input_fn=lambda _: "k",
        output_fn=lambda _: None,
    )

    assert client.deleted == []
    assert summary.kept == 1
    row = database.list_playlists()[0]
    assert row["status"] == "paused"
    assert row["pending_deletion"] == 0
