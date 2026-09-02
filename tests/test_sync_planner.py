from dj_sync.database.database import Database
from dj_sync.spotify.normalizer import NormalizedPlaylistTrack
from dj_sync.models import Track
from dj_sync.sync.planner import build_sync_plan


def normalized(track_id: str, position: int) -> NormalizedPlaylistTrack:
    return NormalizedPlaylistTrack(
        track=Track(
            spotify_id=track_id,
            title=f"Song {track_id}",
            artist="Artist",
            duration_ms=180000,
            isrc=None,
            spotify_uri=f"spotify:track:{track_id}",
        ),
        position=position,
        added_at=None,
    )


def test_sync_plan_marks_unlinked_playlist_for_creation(tmp_path) -> None:
    database = Database(tmp_path / "dj_sync.db")
    database.initialize()
    database.upsert_managed_playlist("spotify-playlist", "Reggueton")
    database.replace_spotify_playlist_snapshot(
        "spotify-playlist", [normalized("spotify-1", 0), normalized("spotify-2", 1)]
    )
    database.save_track_match(
        spotify_track_id="spotify-1", tidal_track_id="tidal-1", method="isrc", score=1.0
    )

    plan = build_sync_plan(database)

    assert plan.playlists_to_create == 1
    assert plan.mapped_entries == 1
    assert plan.unmatched_entries == 1
    assert plan.unique_unmatched_tracks == 1
    assert plan.playlists[0].action == "create"
    assert plan.playlists[0].unmatched_entries[0].spotify_track_id == "spotify-2"


def test_sync_plan_uses_existing_tidal_mapping_as_update(tmp_path) -> None:
    database = Database(tmp_path / "dj_sync.db")
    database.initialize()
    database.upsert_managed_playlist("spotify-playlist", "House")
    with database.connect() as connection:
        connection.execute(
            """
            UPDATE playlists
            SET tidal_playlist_id = 'tidal-playlist', tidal_name = 'House'
            WHERE spotify_playlist_id = 'spotify-playlist'
            """
        )

    plan = build_sync_plan(database)

    assert plan.playlists_to_create == 0
    assert plan.playlists[0].action == "update"
    assert plan.playlists[0].tidal_playlist_id == "tidal-playlist"


def test_sync_plan_preserves_duplicate_playlist_entries(tmp_path) -> None:
    database = Database(tmp_path / "dj_sync.db")
    database.initialize()
    database.upsert_managed_playlist("spotify-playlist", "Party")
    database.replace_spotify_playlist_snapshot(
        "spotify-playlist", [normalized("spotify-1", 0), normalized("spotify-1", 1)]
    )
    database.save_track_match(
        spotify_track_id="spotify-1", tidal_track_id="tidal-1", method="isrc", score=1.0
    )

    plan = build_sync_plan(database)

    assert plan.playlists[0].playlist_entries == 2
    assert plan.playlists[0].mapped_entries == 2
