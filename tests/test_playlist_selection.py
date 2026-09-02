import pytest

from dj_sync.playlist_selection import parse_selection
from dj_sync.spotify.client import SpotifyPlaylist


def playlist(id_: str, name: str, can_read: bool = True) -> SpotifyPlaylist:
    return SpotifyPlaylist(
        id=id_,
        name=name,
        public=True,
        collaborative=False,
        owner_display_name="Eduardo",
        item_count=10,
        can_read_items=can_read,
    )


def test_parse_selection_supports_indexes_and_ranges() -> None:
    playlists = [playlist("a", "A"), playlist("b", "B"), playlist("c", "C"), playlist("d", "D")]
    selected = parse_selection("1,3-4", playlists)
    assert [item.id for item in selected] == ["a", "c", "d"]


def test_parse_selection_all_skips_unavailable_playlists() -> None:
    playlists = [playlist("a", "A"), playlist("b", "B", False), playlist("c", "C")]
    selected = parse_selection("all", playlists)
    assert [item.id for item in selected] == ["a", "c"]


def test_parse_selection_rejects_unavailable_playlist() -> None:
    playlists = [playlist("a", "A"), playlist("b", "B", False)]
    with pytest.raises(ValueError):
        parse_selection("2", playlists)
