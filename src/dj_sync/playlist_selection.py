from __future__ import annotations

from collections.abc import Sequence

from dj_sync.spotify.client import SpotifyPlaylist


def parse_selection(raw: str, playlists: Sequence[SpotifyPlaylist]) -> list[SpotifyPlaylist]:
    """Parse comma-separated 1-based indexes and ranges, e.g. '1,3,5-8'."""
    raw = raw.strip().lower()
    eligible_indexes = {
        index
        for index, playlist in enumerate(playlists, start=1)
        if playlist.can_read_items
    }

    if raw in {"all", "a"}:
        selected_indexes = set(eligible_indexes)
    elif raw in {"none", "n", ""}:
        selected_indexes = set()
    else:
        selected_indexes: set[int] = set()
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                start_text, end_text = part.split("-", 1)
                start, end = int(start_text), int(end_text)
                if start > end:
                    start, end = end, start
                selected_indexes.update(range(start, end + 1))
            else:
                selected_indexes.add(int(part))

    invalid = {index for index in selected_indexes if index not in eligible_indexes}
    if invalid:
        invalid_text = ", ".join(str(index) for index in sorted(invalid))
        raise ValueError(
            f"Playlist selection contains unavailable/invalid indexes: {invalid_text}"
        )

    return [
        playlist
        for index, playlist in enumerate(playlists, start=1)
        if index in selected_indexes
    ]
