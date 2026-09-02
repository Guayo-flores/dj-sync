from __future__ import annotations

import pytest

from dj_sync.database.database import Database
from dj_sync.models import Track
from dj_sync.spotify.normalizer import NormalizedPlaylistTrack
from dj_sync.sync.executor import execute_initial_sync
from dj_sync.sync.planner import build_sync_plan
from dj_sync.tidal.client import TidalPlaylist, TidalPlaylistAddResult, TidalPlaylistItem


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


class FakeTidalClient:
    def __init__(self) -> None:
        self.created: list[str] = []
        self.add_calls: list[tuple[str, list[str]]] = []
        self.items: dict[str, list[str]] = {}
        self.fail_add = False

    def iter_owned_playlists(self):
        return iter(())

    def create_playlist(self, name: str, *, description: str | None = None):
        playlist_id = f"tidal-{len(self.created) + 1}"
        self.created.append(name)
        self.items[playlist_id] = []
        return TidalPlaylist(id=playlist_id, name=name)

    def iter_playlist_items(self, playlist_id: str):
        for track_id in self.items.get(playlist_id, []):
            yield TidalPlaylistItem(id=track_id, type="tracks", item_id=f"item-{track_id}")

    def add_playlist_tracks(self, playlist_id: str, track_ids: list[str]):
        if self.fail_add:
            raise RuntimeError("simulated write failure")
        self.add_calls.append((playlist_id, list(track_ids)))
        self.items.setdefault(playlist_id, []).extend(track_ids)
        return TidalPlaylistAddResult(added=len(track_ids))


def prepared_database(tmp_path, count: int = 3) -> Database:
    database = Database(tmp_path / "dj_sync.db")
    database.initialize()
    database.upsert_managed_playlist("spotify-playlist", "Party")
    database.replace_spotify_playlist_snapshot(
        "spotify-playlist", [normalized(f"spotify-{i}", i) for i in range(count)]
    )
    for i in range(count):
        database.save_track_match(
            spotify_track_id=f"spotify-{i}",
            tidal_track_id=f"tidal-track-{i}",
            method="isrc",
            score=1.0,
        )
    return database


def test_initial_sync_creates_playlist_and_populates_tracks_in_order(tmp_path) -> None:
    database = prepared_database(tmp_path)
    client = FakeTidalClient()

    summary = execute_initial_sync(
        client=client, database=database, plan=build_sync_plan(database)
    )

    assert client.created == ["Party"]
    assert client.items["tidal-1"] == ["tidal-track-0", "tidal-track-1", "tidal-track-2"]
    assert summary.playlists_created == 1
    assert summary.tracks_added == 3
    row = database.list_playlists()[0]
    assert row["tidal_playlist_id"] == "tidal-1"
    assert row["last_synced_at"] is not None


def test_initial_sync_batches_playlist_adds_at_fifty(tmp_path) -> None:
    database = prepared_database(tmp_path, count=101)
    client = FakeTidalClient()

    execute_initial_sync(client=client, database=database, plan=build_sync_plan(database))

    assert [len(track_ids) for _, track_ids in client.add_calls] == [50, 50, 1]


def test_initial_sync_is_resumable_when_existing_items_are_exact_prefix(tmp_path) -> None:
    database = prepared_database(tmp_path, count=4)
    database.save_tidal_playlist_mapping("spotify-playlist", "tidal-existing", "Party")
    client = FakeTidalClient()
    client.items["tidal-existing"] = ["tidal-track-0", "tidal-track-1"]

    summary = execute_initial_sync(
        client=client, database=database, plan=build_sync_plan(database)
    )

    assert client.created == []
    assert client.add_calls == [("tidal-existing", ["tidal-track-2", "tidal-track-3"])]
    assert summary.playlists[0].already_present == 2
    assert summary.tracks_added == 2


def test_initial_sync_stops_if_existing_tidal_playlist_diverged(tmp_path) -> None:
    database = prepared_database(tmp_path, count=3)
    database.save_tidal_playlist_mapping("spotify-playlist", "tidal-existing", "Party")
    client = FakeTidalClient()
    client.items["tidal-existing"] = ["different-track"]

    with pytest.raises(RuntimeError, match="expected prefix"):
        execute_initial_sync(client=client, database=database, plan=build_sync_plan(database))

    assert client.add_calls == []


def test_playlist_mapping_is_persisted_before_track_population(tmp_path) -> None:
    database = prepared_database(tmp_path, count=1)
    client = FakeTidalClient()
    client.fail_add = True

    with pytest.raises(RuntimeError, match="simulated write failure"):
        execute_initial_sync(client=client, database=database, plan=build_sync_plan(database))

    row = database.list_playlists()[0]
    assert row["tidal_playlist_id"] == "tidal-1"


def test_initial_sync_stops_before_writes_on_existing_tidal_name_collision(tmp_path) -> None:
    database = prepared_database(tmp_path, count=1)
    client = FakeTidalClient()
    client.iter_owned_playlists = lambda: iter([TidalPlaylist(id="manual-1", name="Party")])

    with pytest.raises(RuntimeError, match="name collision"):
        execute_initial_sync(client=client, database=database, plan=build_sync_plan(database))

    assert client.created == []
    assert client.add_calls == []
