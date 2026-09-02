from __future__ import annotations

from dj_sync.database.database import Database
from dj_sync.tidal.review import review_match_candidates


def seed_review(database: Database, *, spotify_id: str = "spotify-review") -> None:
    with database.connect() as connection:
        connection.execute(
            """
            INSERT INTO tracks (
                spotify_track_id, isrc, title, artist, duration_ms, match_method
            )
            VALUES (?, 'USAAA1234567', 'Spotify Song', 'Spotify Artist', 180000, 'isrc_not_found')
            """,
            (spotify_id,),
        )
    database.save_match_candidate(
        spotify_track_id=spotify_id,
        tidal_track_id="tidal-review",
        tidal_title="TIDAL Song",
        tidal_artist="TIDAL Artist",
        tidal_duration_ms=181000,
        score=0.88,
        title_score=0.90,
        artist_score=0.87,
        duration_score=0.98,
        status="review",
    )


def test_review_approve_saves_permanent_manual_match(tmp_path) -> None:
    database = Database(tmp_path / "dj_sync.db")
    database.initialize()
    seed_review(database)
    output: list[str] = []

    summary = review_match_candidates(
        database=database,
        input_fn=lambda _: "a",
        output_fn=output.append,
    )

    assert summary.approved == 1
    assert database.list_match_candidates(status="review") == []
    with database.connect() as connection:
        row = connection.execute(
            "SELECT tidal_track_id, match_method, match_score FROM tracks WHERE spotify_track_id = 'spotify-review'"
        ).fetchone()
    assert row["tidal_track_id"] == "tidal-review"
    assert row["match_method"] == "manual"
    assert row["match_score"] == 0.88


def test_review_reject_keeps_track_unmatched_and_removes_from_review_queue(tmp_path) -> None:
    database = Database(tmp_path / "dj_sync.db")
    database.initialize()
    seed_review(database)

    summary = review_match_candidates(
        database=database,
        input_fn=lambda _: "r",
        output_fn=lambda _: None,
    )

    assert summary.rejected == 1
    assert database.list_match_candidates(status="review") == []
    assert len(database.list_match_candidates(status="not_found")) == 1
    with database.connect() as connection:
        row = connection.execute(
            "SELECT tidal_track_id FROM tracks WHERE spotify_track_id = 'spotify-review'"
        ).fetchone()
    assert row["tidal_track_id"] is None


def test_review_skip_leaves_candidate_for_later(tmp_path) -> None:
    database = Database(tmp_path / "dj_sync.db")
    database.initialize()
    seed_review(database)

    summary = review_match_candidates(
        database=database,
        input_fn=lambda _: "s",
        output_fn=lambda _: None,
    )

    assert summary.deferred == 1
    assert len(database.list_match_candidates(status="review")) == 1


def test_review_quit_preserves_current_and_remaining_candidates(tmp_path) -> None:
    database = Database(tmp_path / "dj_sync.db")
    database.initialize()
    seed_review(database, spotify_id="spotify-1")
    seed_review(database, spotify_id="spotify-2")

    summary = review_match_candidates(
        database=database,
        input_fn=lambda _: "q",
        output_fn=lambda _: None,
    )

    assert summary.quit_early is True
    assert len(database.list_match_candidates(status="review")) == 2
