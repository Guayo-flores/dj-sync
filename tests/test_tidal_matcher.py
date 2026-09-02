from __future__ import annotations

from dj_sync.database.database import Database
from dj_sync.tidal.client import TidalTrack
from dj_sync.tidal.matcher import match_unmatched_tracks_by_isrc


class FakeTidalClient:
    def __init__(self, matches: dict[str, str]) -> None:
        self.matches = matches
        self.calls: list[list[str]] = []

    def get_tracks_by_isrc(self, isrcs: list[str]) -> list[TidalTrack]:
        self.calls.append(isrcs)
        return [
            TidalTrack(id=self.matches[isrc], title="Song", isrc=isrc)
            for isrc in isrcs
            if isrc in self.matches
        ]


def seed_tracks(database: Database, count: int) -> None:
    with database.connect() as connection:
        connection.executemany(
            """
            INSERT INTO tracks (spotify_track_id, isrc, title, artist, duration_ms)
            VALUES (?, ?, ?, 'Artist', 180000)
            """,
            [
                (f"spotify-{index}", f"USABC{index:07d}", f"Song {index}")
                for index in range(count)
            ],
        )


def test_isrc_matcher_batches_twenty_and_persists_matches_and_misses(tmp_path) -> None:
    database = Database(tmp_path / "dj_sync.db")
    database.initialize()
    seed_tracks(database, 21)
    client = FakeTidalClient(
        {
            "USABC0000000": "tidal-0",
            "USABC0000020": "tidal-20",
        }
    )

    summary = match_unmatched_tracks_by_isrc(
        client=client, database=database, batch_delay_seconds=0
    )

    assert summary.candidates == 21
    assert summary.batches == 2
    assert summary.matched == 2
    assert summary.misses == 19
    assert [len(call) for call in client.calls] == [20, 1]
    counts = database.track_match_counts()
    assert counts["matched"] == 2
    assert counts["isrc_misses"] == 19


def test_isrc_matcher_normalizes_lowercase_before_tidal_lookup(tmp_path) -> None:
    database = Database(tmp_path / "dj_sync.db")
    database.initialize()
    with database.connect() as connection:
        connection.execute(
            """
            INSERT INTO tracks (spotify_track_id, isrc, title, artist, duration_ms)
            VALUES ('spotify-lower', 'ushm92249275', 'Song', 'Artist', 180000)
            """
        )
    client = FakeTidalClient({"USHM92249275": "tidal-lower"})

    summary = match_unmatched_tracks_by_isrc(
        client=client, database=database, batch_delay_seconds=0
    )

    assert client.calls == [["USHM92249275"]]
    assert summary.matched == 1
    assert summary.invalid == 0
    assert database.track_match_counts()["matched"] == 1


def test_isrc_matcher_marks_malformed_isrc_without_calling_tidal(tmp_path) -> None:
    database = Database(tmp_path / "dj_sync.db")
    database.initialize()
    with database.connect() as connection:
        connection.execute(
            """
            INSERT INTO tracks (spotify_track_id, isrc, title, artist, duration_ms)
            VALUES ('spotify-bad', 'BAD', 'Song', 'Artist', 180000)
            """
        )
    client = FakeTidalClient({})

    summary = match_unmatched_tracks_by_isrc(
        client=client, database=database, batch_delay_seconds=0
    )

    assert client.calls == []
    assert summary.invalid == 1
    assert summary.batches == 0
    assert database.list_tracks_pending_isrc_match() == []


def test_isrc_matcher_paces_batches(tmp_path) -> None:
    database = Database(tmp_path / "dj_sync.db")
    database.initialize()
    seed_tracks(database, 21)
    client = FakeTidalClient({})
    sleeps: list[float] = []

    summary = match_unmatched_tracks_by_isrc(
        client=client,
        database=database,
        batch_delay_seconds=0.75,
        sleep_fn=sleeps.append,
    )

    assert summary.batches == 2
    assert sleeps == [0.75, 0.75]
