from dj_sync.spotify.client import SpotifyClient


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return FakeResponse(self.pages.pop(0))


def test_iter_playlists_handles_pagination_and_2026_items_summary() -> None:
    session = FakeSession(
        [
            {
                "items": [
                    {
                        "id": "p1",
                        "name": "Thursday @ The Den",
                        "public": False,
                        "collaborative": False,
                        "owner": {"display_name": "Eduardo"},
                        "items": {"total": 120},
                    }
                ],
                "next": "next-page",
            },
            {
                "items": [
                    {
                        "id": "p2",
                        "name": "Tailgate",
                        "public": True,
                        "collaborative": False,
                        "owner": {"display_name": "Eduardo"},
                        "items": {"total": 75},
                    }
                ],
                "next": None,
            },
        ]
    )
    client = SpotifyClient("token", session=session)

    playlists = list(client.iter_playlists())

    assert [playlist.id for playlist in playlists] == ["p1", "p2"]
    assert playlists[0].item_count == 120
    assert len(session.calls) == 2
    assert session.calls[0][1]["params"]["offset"] == 0
    assert session.calls[1][1]["params"]["offset"] == 1


def test_iter_playlist_items_uses_current_50_item_page_limit() -> None:
    session = FakeSession([{"items": [], "next": None}])
    client = SpotifyClient("token", session=session)

    assert list(client.iter_playlist_items("playlist-1")) == []

    assert session.calls[0][1]["params"]["limit"] == 50


class StatusResponse(FakeResponse):
    def __init__(self, payload, status_code):
        super().__init__(payload)
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(f"HTTP {self.status_code}")


class StatusSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def test_client_refreshes_once_on_401_and_retries_request() -> None:
    session = StatusSession(
        [
            StatusResponse({}, 401),
            StatusResponse({"items": [], "next": None}, 200),
        ]
    )
    refresh_calls = []

    def refresh():
        refresh_calls.append(True)
        return "fresh-token"

    client = SpotifyClient("expired-token", token_refresher=refresh, session=session)

    assert list(client.iter_playlist_items("playlist-1")) == []
    assert len(refresh_calls) == 1
    assert len(session.calls) == 2
    assert session.calls[0][1]["headers"]["Authorization"] == "Bearer expired-token"
    assert session.calls[1][1]["headers"]["Authorization"] == "Bearer fresh-token"


def test_client_does_not_loop_if_refreshed_token_is_also_unauthorized() -> None:
    import pytest
    import requests

    session = StatusSession([StatusResponse({}, 401), StatusResponse({}, 401)])
    client = SpotifyClient(
        "expired-token", token_refresher=lambda: "still-bad", session=session
    )

    with pytest.raises(requests.HTTPError):
        list(client.iter_playlist_items("playlist-1"))

    assert len(session.calls) == 2
