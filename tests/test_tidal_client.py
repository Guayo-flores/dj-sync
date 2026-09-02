from __future__ import annotations

from typing import Any

from dj_sync.tidal.client import JSON_API_CONTENT_TYPE, TidalClient


class FakeResponse:
    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        self.payload = payload or {}
        self.raise_for_status_called = False

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
