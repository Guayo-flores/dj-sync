from dj_sync.database.database import Database
from dj_sync.tidal.client import TidalTrack
from dj_sync.tidal.metadata_matcher import match_unmatched_tracks_by_metadata


class FakeSearchClient:
    def __init__(self, results):
        self.results = results
        self.queries = []

    def search_tracks(self, query: str, *, limit: int = 10):
        self.queries.append((query, limit))
        return self.results.get(query, [])


def seed(database: Database) -> None:
    with database.connect() as connection:
        connection.executemany(
            """
            INSERT INTO tracks (spotify_track_id, isrc, title, artist, duration_ms, match_method)
            VALUES (?, ?, ?, ?, ?, 'isrc_not_found')
            """,
            [
                ("safe", "USAAA1234567", "Song One", "Artist A", 180000),
                ("review", "USBBB1234567", "Song Two", "Artist B", 180000),
                ("miss", "USCCC1234567", "Unknown Song", "Nobody", 180000),
            ],
        )


def test_metadata_matcher_auto_matches_and_holds_ambiguous_for_review(tmp_path) -> None:
    database = Database(tmp_path / "dj_sync.db")
    database.initialize()
    seed(database)
    client = FakeSearchClient(
        {
            "Song One Artist A": [
                TidalTrack("tidal-safe", "Song One", None, "PT3M0S", ("Artist A",))
            ],
            "Song Two Artist B": [
                TidalTrack("tidal-review", "Song Two (Radio Edit)", None, "PT3M1S", ("Artist B",))
            ],
            "Unknown Song Nobody": [],
        }
    )

    summary = match_unmatched_tracks_by_metadata(
        client=client, database=database, search_delay_seconds=0
    )

    assert summary.automatic == 1
    assert summary.review == 1
    assert summary.not_found == 1
    assert database.track_match_counts()["matched"] == 1
    reviews = database.list_match_candidates(status="review")
    assert len(reviews) == 1
    assert reviews[0]["tidal_track_id"] == "tidal-review"
