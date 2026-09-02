from __future__ import annotations

from dataclasses import dataclass
from itertools import islice

from dj_sync.database.database import Database
from dj_sync.tidal.client import TidalClient


@dataclass(frozen=True, slots=True)
class IsrcMatchSummary:
    candidates: int
    matched: int
    misses: int
    batches: int


def _batched(items: list, size: int):
    iterator = iter(items)
    while batch := list(islice(iterator, size)):
        yield batch


def match_unmatched_tracks_by_isrc(
    *, client: TidalClient, database: Database, limit: int | None = None
) -> IsrcMatchSummary:
    candidates = database.list_tracks_pending_isrc_match(limit=limit)
    matched = 0
    misses = 0
    batches = 0

    for batch in _batched(candidates, 20):
        batches += 1
        isrcs = [row["isrc"] for row in batch if row["isrc"]]
        tidal_tracks = client.get_tracks_by_isrc(isrcs)
        by_isrc = {track.isrc.upper(): track for track in tidal_tracks if track.isrc}

        for row in batch:
            isrc = row["isrc"]
            tidal_track = by_isrc.get(isrc.upper()) if isrc else None
            if tidal_track is None:
                database.mark_isrc_miss(row["spotify_track_id"])
                misses += 1
                continue

            database.save_track_match(
                spotify_track_id=row["spotify_track_id"],
                tidal_track_id=tidal_track.id,
                method="isrc",
                score=1.0,
            )
            matched += 1

    return IsrcMatchSummary(
        candidates=len(candidates),
        matched=matched,
        misses=misses,
        batches=batches,
    )
