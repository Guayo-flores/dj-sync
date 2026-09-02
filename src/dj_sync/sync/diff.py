from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Iterable


@dataclass(frozen=True, slots=True)
class PlaylistDiff:
    added: frozenset[str]
    removed: frozenset[str]
    unchanged: frozenset[str]

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.removed)


def diff_track_ids(current: Iterable[str], previous: Iterable[str]) -> PlaylistDiff:
    """Compare Spotify playlist membership against the last synced state."""
    current_ids = frozenset(current)
    previous_ids = frozenset(previous)
    return PlaylistDiff(
        added=current_ids - previous_ids,
        removed=previous_ids - current_ids,
        unchanged=current_ids & previous_ids,
    )
