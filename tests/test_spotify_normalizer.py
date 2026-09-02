from dj_sync.spotify.normalizer import normalize_playlist_item


def spotify_item(*, track_id: str = "track-1", title: str = "NUEVAYoL"):
    return {
        "added_at": "2026-09-01T12:00:00Z",
        "is_local": False,
        "item": {
            "id": track_id,
            "name": title,
            "type": "track",
            "uri": f"spotify:track:{track_id}",
            "duration_ms": 183000,
            "artists": [{"name": "Bad Bunny"}, {"name": "Guest"}],
            "album": {"name": "DeBÍ TiRAR MáS FOToS"},
            "external_ids": {"isrc": "QM6N22512345"},
        },
    }


def test_normalize_playlist_item_extracts_matching_metadata() -> None:
    item = normalize_playlist_item(spotify_item(), position=7)

    assert item is not None
    assert item.position == 7
    assert item.added_at == "2026-09-01T12:00:00Z"
    assert item.track.spotify_id == "track-1"
    assert item.track.artist == "Bad Bunny, Guest"
    assert item.track.album == "DeBÍ TiRAR MáS FOToS"
    assert item.track.isrc == "QM6N22512345"
    assert item.track.spotify_uri == "spotify:track:track-1"


def test_normalize_playlist_item_skips_local_and_episode_items() -> None:
    local = spotify_item()
    local["is_local"] = True
    assert normalize_playlist_item(local, position=0) is None

    episode = spotify_item()
    episode["item"]["type"] = "episode"
    assert normalize_playlist_item(episode, position=1) is None


def test_normalize_playlist_item_accepts_legacy_track_key() -> None:
    payload = spotify_item()
    payload["track"] = payload.pop("item")

    item = normalize_playlist_item(payload, position=0)

    assert item is not None
    assert item.track.spotify_id == "track-1"
