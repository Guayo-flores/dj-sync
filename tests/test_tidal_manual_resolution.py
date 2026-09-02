from __future__ import annotations

from dj_sync.database.database import Database
from dj_sync.tidal.client import TidalTrack
from dj_sync.tidal.manual_resolution import resolve_unmatched_tracks


class FakeTidalClient:
    def __init__(self, results_by_query: dict[str, list[TidalTrack]]) -> None:
        self.results_by_query = results_by_query
        self.queries: list[str] = []

    def search_tracks(self, query: str, *, limit: int = 5) -> list[TidalTrack]:
        self.queries.append(query)
        return self.results_by_query.get(query, [])[:limit]


def seed_unmatched(database: Database, *, spotify_id: str = "spotify-1") -> None:
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
        connection.execute(
            """
            INSERT INTO match_candidates (
                spotify_track_id, tidal_track_id, tidal_title, tidal_artist,
                tidal_duration_ms, score, title_score, artist_score,
                duration_score, status
            )
            VALUES (?, 'rejected-track', 'Wrong Song', 'Wrong Artist',
                    180000, 0.70, 0.70, 0.70, 1.0, 'not_found')
            """,
            (spotify_id,),
        )


def candidate(track_id: str = "tidal-1") -> TidalTrack:
    return TidalTrack(
        id=track_id,
        title="Spotify Song",
        isrc=None,
        duration="PT3M0S",
        artists=("Spotify Artist",),
    )


def test_manual_resolution_selects_numbered_result_and_saves_mapping(tmp_path) -> None:
    database = Database(tmp_path / "dj_sync.db")
    database.initialize()
    seed_unmatched(database)
    query = "Spotify Song Spotify Artist"
    client = FakeTidalClient({query: [candidate()]})

    summary = resolve_unmatched_tracks(
        client=client,
        database=database,
        input_fn=lambda _: "1",
        output_fn=lambda _: None,
    )

    assert summary.resolved == 1
    assert database.list_unmatched_tracks() == []
    with database.connect() as connection:
        row = connection.execute(
            "SELECT tidal_track_id, match_method FROM tracks WHERE spotify_track_id = 'spotify-1'"
        ).fetchone()
        candidates = connection.execute("SELECT COUNT(*) AS count FROM match_candidates").fetchone()
    assert row["tidal_track_id"] == "tidal-1"
    assert row["match_method"] == "manual_search"
    assert candidates["count"] == 0


def test_manual_resolution_custom_search_can_replace_default_query(tmp_path) -> None:
    database = Database(tmp_path / "dj_sync.db")
    database.initialize()
    seed_unmatched(database)
    default_query = "Spotify Song Spotify Artist"
    custom_query = "alternate version"
    client = FakeTidalClient({default_query: [], custom_query: [candidate("tidal-2")]})
    answers = iter(["c", custom_query, "1"])

    summary = resolve_unmatched_tracks(
        client=client,
        database=database,
        input_fn=lambda _: next(answers),
        output_fn=lambda _: None,
    )

    assert summary.resolved == 1
    assert client.queries == [default_query, custom_query]
    with database.connect() as connection:
        row = connection.execute(
            "SELECT tidal_track_id FROM tracks WHERE spotify_track_id = 'spotify-1'"
        ).fetchone()
    assert row["tidal_track_id"] == "tidal-2"


def test_manual_resolution_skip_keeps_track_unmatched(tmp_path) -> None:
    database = Database(tmp_path / "dj_sync.db")
    database.initialize()
    seed_unmatched(database)
    query = "Spotify Song Spotify Artist"
    client = FakeTidalClient({query: [candidate()]})

    summary = resolve_unmatched_tracks(
        client=client,
        database=database,
        input_fn=lambda _: "s",
        output_fn=lambda _: None,
    )

    assert summary.skipped == 1
    assert len(database.list_unmatched_tracks()) == 1


def test_manual_resolution_quit_preserves_all_unmatched_tracks(tmp_path) -> None:
    database = Database(tmp_path / "dj_sync.db")
    database.initialize()
    seed_unmatched(database, spotify_id="spotify-1")
    seed_unmatched(database, spotify_id="spotify-2")
    client = FakeTidalClient({})

    summary = resolve_unmatched_tracks(
        client=client,
        database=database,
        input_fn=lambda _: "q",
        output_fn=lambda _: None,
    )

    assert summary.quit_early is True
    assert len(database.list_unmatched_tracks()) == 2
