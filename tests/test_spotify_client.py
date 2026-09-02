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
