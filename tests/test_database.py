from dj_sync.database.database import Database


def test_database_initialization_creates_core_tables(tmp_path) -> None:
    database = Database(tmp_path / "dj_sync.db")
    database.initialize()

    assert {
        "playlists",
        "tracks",
        "playlist_tracks",
        "sync_runs",
    }.issubset(database.table_names())


def test_managed_playlist_selection_can_be_saved_and_paused(tmp_path) -> None:
    database = Database(tmp_path / "dj_sync.db")
    database.initialize()

    database.upsert_managed_playlist("spotify-a", "Thursday @ The Den")
    database.upsert_managed_playlist("spotify-b", "Tailgate")
    database.pause_unselected_playlists(["spotify-a"])

    rows = {row["spotify_playlist_id"]: row for row in database.list_playlists()}
    assert rows["spotify-a"]["status"] == "managed"
    assert rows["spotify-b"]["status"] == "paused"
