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


def test_database_migrates_existing_track_columns_and_playlist_membership_key(tmp_path) -> None:
    import sqlite3

    path = tmp_path / "legacy.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE playlists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            spotify_playlist_id TEXT NOT NULL UNIQUE,
            spotify_name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'managed',
            managed_by_dj_sync INTEGER NOT NULL DEFAULT 1,
            pending_deletion INTEGER NOT NULL DEFAULT 0,
            tidal_playlist_id TEXT UNIQUE,
            tidal_name TEXT,
            last_synced_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE tracks (
            spotify_track_id TEXT PRIMARY KEY,
            tidal_track_id TEXT,
            isrc TEXT,
            title TEXT NOT NULL,
            artist TEXT NOT NULL,
            duration_ms INTEGER NOT NULL,
            match_method TEXT,
            match_score REAL,
            first_matched_at TEXT,
            last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE playlist_tracks (
            playlist_id INTEGER NOT NULL,
            spotify_track_id TEXT NOT NULL,
            position INTEGER NOT NULL,
            added_at TEXT,
            last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (playlist_id, spotify_track_id)
        );
        CREATE TABLE sync_runs (id INTEGER PRIMARY KEY AUTOINCREMENT);
        """
    )
    connection.close()

    database = Database(path)
    database.initialize()

    with database.connect() as migrated:
        track_columns = {row["name"] for row in migrated.execute("PRAGMA table_info(tracks)")}
        membership_info = migrated.execute("PRAGMA table_info(playlist_tracks)").fetchall()
    primary_key = [row["name"] for row in sorted(membership_info, key=lambda row: row["pk"]) if row["pk"]]

    assert {"album", "spotify_uri"}.issubset(track_columns)
    assert primary_key == ["playlist_id", "position"]


def test_database_saves_and_counts_track_matches(tmp_path) -> None:
    database = Database(tmp_path / "dj_sync.db")
    database.initialize()
    with database.connect() as connection:
        connection.execute(
            """
            INSERT INTO tracks (spotify_track_id, isrc, title, artist, duration_ms)
            VALUES ('spotify-1', 'USABC1234567', 'Song', 'Artist', 180000)
            """
        )

    pending = database.list_tracks_pending_isrc_match()
    assert [row["spotify_track_id"] for row in pending] == ["spotify-1"]

    database.save_track_match(
        spotify_track_id="spotify-1",
        tidal_track_id="tidal-1",
        method="isrc",
        score=1.0,
    )

    counts = database.track_match_counts()
    assert counts["total"] == 1
    assert counts["matched"] == 1
    assert database.list_tracks_pending_isrc_match() == []


def test_database_marks_isrc_miss_so_exact_pass_is_not_repeated(tmp_path) -> None:
    database = Database(tmp_path / "dj_sync.db")
    database.initialize()
    with database.connect() as connection:
        connection.execute(
            """
            INSERT INTO tracks (spotify_track_id, isrc, title, artist, duration_ms)
            VALUES ('spotify-1', 'USABC1234567', 'Song', 'Artist', 180000)
            """
        )

    database.mark_isrc_miss("spotify-1")

    assert database.list_tracks_pending_isrc_match() == []
    assert database.track_match_counts()["isrc_misses"] == 1
