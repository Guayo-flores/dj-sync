from __future__ import annotations

from typing import Any

from dj_sync.tidal.client import JSON_API_CONTENT_TYPE, TidalClient


class FakeResponse:
    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        self.payload = payload or {}
        self.raise_for_status_called = False
        self.status_code = 200
        self.headers: dict[str, str] = {}

    def raise_for_status(self) -> None:
        self.raise_for_status_called = True

    def json(self) -> dict[str, Any]:
        return self.payload


class FakeSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.responses: list[FakeResponse] = []

    def queue(self, payload: dict[str, Any] | None = None) -> FakeResponse:
        response = FakeResponse(payload)
        self.responses.append(response)
        return response

    def _next(self, method: str, url: str, kwargs: dict[str, Any]) -> FakeResponse:
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        return self._next("POST", url, kwargs)

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        return self._next("GET", url, kwargs)

    def delete(self, url: str, **kwargs: Any) -> FakeResponse:
        return self._next("DELETE", url, kwargs)


def playlist_payload(playlist_id: str = "playlist-123") -> dict[str, Any]:
    return {
        "data": {
            "id": playlist_id,
            "type": "playlists",
            "attributes": {
                "name": "DJ Sync Test",
                "description": "Temporary",
                "accessType": "UNLISTED",
            },
        }
    }


def test_create_playlist_uses_json_api_and_idempotency_key() -> None:
    session = FakeSession()
    response = session.queue(playlist_payload())
    client = TidalClient("access-123", session=session)

    playlist = client.create_playlist("DJ Sync Test", description="Temporary")

    method, url, kwargs = session.calls[0]
    assert method == "POST"
    assert url.endswith("/playlists")
    assert kwargs["headers"]["Authorization"] == "Bearer access-123"
    assert kwargs["headers"]["Content-Type"] == JSON_API_CONTENT_TYPE
    assert kwargs["headers"]["Idempotency-Key"]
    assert kwargs["json"] == {
        "data": {
            "type": "playlists",
            "attributes": {
                "name": "DJ Sync Test",
                "accessType": "UNLISTED",
                "description": "Temporary",
            },
        }
    }
    assert playlist.id == "playlist-123"
    assert playlist.name == "DJ Sync Test"
    assert response.raise_for_status_called


def test_get_playlist_parses_resource() -> None:
    session = FakeSession()
    session.queue(playlist_payload("playlist-456"))
    client = TidalClient("access-123", session=session)

    playlist = client.get_playlist("playlist-456")

    method, url, _ = session.calls[0]
    assert method == "GET"
    assert url.endswith("/playlists/playlist-456")
    assert playlist.id == "playlist-456"
    assert playlist.access_type == "UNLISTED"


def test_delete_playlist_uses_idempotency_key() -> None:
    session = FakeSession()
    response = session.queue({"meta": {}})
    client = TidalClient("access-123", session=session)

    client.delete_playlist("playlist-789")

    method, url, kwargs = session.calls[0]
    assert method == "DELETE"
    assert url.endswith("/playlists/playlist-789")
    assert kwargs["headers"]["Idempotency-Key"]
    assert response.raise_for_status_called


def test_get_tracks_by_isrc_uses_batched_filter_and_parses_tracks() -> None:
    session = FakeSession()
    response = session.queue(
        {
            "data": [
                {
                    "id": "75413016",
                    "type": "tracks",
                    "attributes": {
                        "title": "Example Song",
                        "isrc": "USABC1234567",
                        "duration": "PT3M12S",
                    },
                }
            ]
        }
    )
    client = TidalClient("access-123", session=session)

    tracks = client.get_tracks_by_isrc(["USABC1234567", "USXYZ7654321"])

    method, url, kwargs = session.calls[0]
    assert method == "GET"
    assert url.endswith("/tracks")
    assert kwargs["params"]["filter[isrc]"] == ["USABC1234567", "USXYZ7654321"]
    assert tracks[0].id == "75413016"
    assert tracks[0].isrc == "USABC1234567"
    assert response.raise_for_status_called


def test_get_tracks_by_isrc_rejects_more_than_twenty() -> None:
    client = TidalClient("access-123", session=FakeSession())

    try:
        client.get_tracks_by_isrc([f"ISRC{i:02d}" for i in range(21)])
    except ValueError as error:
        assert "at most 20" in str(error)
    else:
        raise AssertionError("Expected ValueError")


def test_get_tracks_by_isrc_normalizes_lowercase_and_hyphenated_values() -> None:
    session = FakeSession()
    session.queue({"data": []})
    client = TidalClient("access-123", session=session)

    client.get_tracks_by_isrc(["ushm92249275", "US-ABC-12-34567"])

    _, _, kwargs = session.calls[0]
    assert kwargs["params"]["filter[isrc]"] == [
        "USHM92249275",
        "USABC1234567",
    ]


def test_get_tracks_by_isrc_rejects_malformed_isrc() -> None:
    client = TidalClient("access-123", session=FakeSession())

    try:
        client.get_tracks_by_isrc(["not-an-isrc"])
    except ValueError as error:
        assert "Invalid ISRC" in str(error)
    else:
        raise AssertionError("Expected ValueError")


def test_get_tracks_by_isrc_retries_429_using_retry_after() -> None:
    session = FakeSession()
    first = session.queue({"errors": [{"status": "429"}]})
    first.status_code = 429
    first.headers = {"Retry-After": "3"}
    second = session.queue({"data": []})
    second.status_code = 200
    second.headers = {}
    sleeps: list[float] = []
    client = TidalClient(
        "access-123",
        session=session,
        sleep_fn=sleeps.append,
    )

    client.get_tracks_by_isrc(["USABC1234567"])

    assert len(session.calls) == 2
    assert sleeps == [3.0]


def test_get_tracks_by_isrc_uses_exponential_backoff_without_retry_after() -> None:
    session = FakeSession()
    first = session.queue({"errors": [{"status": "429"}]})
    first.status_code = 429
    first.headers = {}
    second = session.queue({"errors": [{"status": "429"}]})
    second.status_code = 429
    second.headers = {}
    third = session.queue({"data": []})
    third.status_code = 200
    third.headers = {}
    sleeps: list[float] = []
    client = TidalClient(
        "access-123",
        session=session,
        rate_limit_backoff_seconds=2.0,
        sleep_fn=sleeps.append,
    )

    client.get_tracks_by_isrc(["USABC1234567"])

    assert sleeps == [2.0, 4.0]


def test_search_tracks_parses_ranked_tracks_and_artists() -> None:
    session = FakeSession()
    session.queue(
        {
            "data": [
                {
                    "id": "search-1",
                    "type": "searchResults",
                    "relationships": {
                        "tracks": {
                            "data": [
                                {"type": "tracks", "id": "track-2"},
                                {"type": "tracks", "id": "track-1"},
                            ]
                        }
                    },
                }
            ],
            "included": [
                {
                    "id": "track-1",
                    "type": "tracks",
                    "attributes": {"title": "Song One", "isrc": None, "duration": "PT3M0S"},
                    "relationships": {"artists": {"data": [{"type": "artists", "id": "artist-1"}]}},
                },
                {
                    "id": "track-2",
                    "type": "tracks",
                    "attributes": {"title": "Song Two", "isrc": None, "duration": "PT3M1S"},
                    "relationships": {"artists": {"data": [{"type": "artists", "id": "artist-2"}]}},
                },
                {"id": "artist-1", "type": "artists", "attributes": {"name": "Artist One"}},
                {"id": "artist-2", "type": "artists", "attributes": {"name": "Artist Two"}},
            ],
        }
    )
    client = TidalClient("access-123", session=session)

    tracks = client.search_tracks("Song Artist", limit=2)

    _, url, kwargs = session.calls[0]
    assert url.endswith("/searchResults")
    assert kwargs["params"]["filter[query]"] == "Song Artist"
    assert kwargs["params"]["include"] == ["tracks", "tracks.artists"]
    assert [track.id for track in tracks] == ["track-2", "track-1"]
    assert tracks[0].artists == ("Artist Two",)
    assert tracks[0].duration_ms == 181000


def test_add_playlist_tracks_preserves_order_and_uses_relationship_endpoint() -> None:
    session = FakeSession()
    session.queue(
        {
            "data": [
                {"type": "tracks", "id": "track-1", "meta": {"itemId": "item-1"}},
                {"type": "tracks", "id": "track-2", "meta": {"itemId": "item-2"}},
            ],
            "meta": {"skipped": []},
        }
    )
    client = TidalClient("access-123", session=session)

    result = client.add_playlist_tracks("playlist-1", ["track-1", "track-2"])

    method, url, kwargs = session.calls[0]
    assert method == "POST"
    assert url.endswith("/playlists/playlist-1/relationships/items")
    assert kwargs["json"] == {
        "data": [
            {"type": "tracks", "id": "track-1"},
            {"type": "tracks", "id": "track-2"},
        ]
    }
    assert kwargs["headers"]["Idempotency-Key"]
    assert result.added == 2
    assert result.skipped_ids == ()


def test_add_playlist_tracks_rejects_more_than_fifty() -> None:
    client = TidalClient("access-123", session=FakeSession())

    try:
        client.add_playlist_tracks("playlist-1", [str(i) for i in range(51)])
    except ValueError as error:
        assert "at most 50" in str(error)
    else:
        raise AssertionError("Expected ValueError")


def test_iter_playlist_items_follows_json_api_next_link() -> None:
    session = FakeSession()
    session.queue(
        {
            "data": [{"type": "tracks", "id": "track-1", "meta": {"itemId": "item-1"}}],
            "links": {"next": "https://example.test/next-page"},
        }
    )
    session.queue(
        {
            "data": [{"type": "tracks", "id": "track-2", "meta": {"itemId": "item-2"}}],
            "links": {"self": "https://example.test/next-page"},
        }
    )
    client = TidalClient("access-123", session=session)

    items = list(client.iter_playlist_items("playlist-1"))

    assert [item.id for item in items] == ["track-1", "track-2"]
    assert session.calls[1][1] == "https://example.test/next-page"


def test_iter_owned_playlists_filters_for_authenticated_owner() -> None:
    session = FakeSession()
    session.queue({"data": [playlist_payload("owned-1")["data"]], "links": {}})
    client = TidalClient("access-123", session=session)

    playlists = list(client.iter_owned_playlists())

    _, url, kwargs = session.calls[0]
    assert url.endswith("/playlists")
    assert kwargs["params"] == {"filter[owners.id]": ["me"]}
    assert playlists[0].id == "owned-1"
