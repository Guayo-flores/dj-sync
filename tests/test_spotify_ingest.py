from dj_sync.database.database import Database
from dj_sync.spotify.ingest import ingest_managed_playlists


class FakeSpotifyClient:
    def __init__(self, items_by_playlist):
        self.items_by_playlist = items_by_playlist
        self.calls = []

    def iter_playlist_items(self, playlist_id):
        self.calls.append(playlist_id)
        yield from self.items_by_playlist[playlist_id]


def item(track_id: str, title: str, *, added_at: str = "2026-09-01T12:00:00Z"):
    return {
        "added_at": added_at,
        "is_local": False,
        "item": {
            "id": track_id,
            "name": title,
            "type": "track",
            "uri": f"spotify:track:{track_id}",
            "duration_ms": 200000,
            "artists": [{"name": "Artist"}],
            "album": {"name": "Album"},
            "external_ids": {"isrc": f"ISRC-{track_id}"},
        },
    }


def test_ingestion_deduplicates_tracks_globally_and_keeps_membership(tmp_path) -> None:
    database = Database(tmp_path / "dj_sync.db")
    database.initialize()
    database.upsert_managed_playlist("playlist-a", "Reggaeton")
    database.upsert_managed_playlist("playlist-b", "Thursday")
    client = FakeSpotifyClient(
        {
            "playlist-a": [item("track-1", "Song One"), item("track-2", "Song Two")],
            "playlist-b": [item("track-1", "Song One")],
        }
    )

    summary = ingest_managed_playlists(client=client, database=database)

    assert summary.total_tracks_saved == 3
    assert summary.unique_tracks == 2
    assert database.count_playlist_tracks("playlist-a") == 2
    assert database.count_playlist_tracks("playlist-b") == 1


def test_ingestion_preserves_duplicate_track_positions(tmp_path) -> None:
    database = Database(tmp_path / "dj_sync.db")
    database.initialize()
    database.upsert_managed_playlist("playlist-a", "Party")
    client = FakeSpotifyClient(
        {"playlist-a": [item("track-1", "Song One"), item("track-1", "Song One")]}
    )

    summary = ingest_managed_playlists(client=client, database=database)

    assert summary.total_tracks_saved == 2
    assert summary.unique_tracks == 1
    assert database.count_playlist_tracks("playlist-a") == 2
