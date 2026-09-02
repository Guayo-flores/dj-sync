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
