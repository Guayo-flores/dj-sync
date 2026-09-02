from __future__ import annotations

from dataclasses import dataclass
from itertools import islice
import time
from typing import Callable

from dj_sync.database.database import Database
from dj_sync.matching.isrc import normalize_isrc
from dj_sync.tidal.client import TidalClient


@dataclass(frozen=True, slots=True)
class IsrcMatchSummary:
    candidates: int
    matched: int
    misses: int
    invalid: int
    batches: int


def _batched(items: list, size: int):
    iterator = iter(items)
    while batch := list(islice(iterator, size)):
        yield batch


def match_unmatched_tracks_by_isrc(
    *,
    client: TidalClient,
    database: Database,
    limit: int | None = None,
    batch_delay_seconds: float = 0.75,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> IsrcMatchSummary:
    candidates = database.list_tracks_pending_isrc_match(limit=limit)
    matched = 0
    misses = 0
    invalid = 0
    batches = 0

    for batch in _batched(candidates, 20):
        normalized_rows: list[tuple[object, str]] = []
        for row in batch:
            normalized = normalize_isrc(row["isrc"])
            if normalized is None:
                database.mark_isrc_invalid(row["spotify_track_id"])
                invalid += 1
                continue
            normalized_rows.append((row, normalized))

        if not normalized_rows:
            continue

        # De-duplicate equivalent recordings within a batch while preserving
        # order. A single TIDAL result can satisfy multiple Spotify track IDs.
        isrcs = list(dict.fromkeys(isrc for _, isrc in normalized_rows))
        batches += 1
        tidal_tracks = client.get_tracks_by_isrc(isrcs)
        by_isrc = {
            normalized: track
            for track in tidal_tracks
            if (normalized := normalize_isrc(track.isrc)) is not None
        }

        for row, isrc in normalized_rows:
            tidal_track = by_isrc.get(isrc)
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

        # The initial library import can require dozens of TIDAL requests. Pace
        # successful batches proactively; the client separately handles 429s
        # with Retry-After/exponential backoff if the service still throttles us.
        if batch_delay_seconds > 0:
            sleep_fn(batch_delay_seconds)

    return IsrcMatchSummary(
        candidates=len(candidates),
        matched=matched,
        misses=misses,
        invalid=invalid,
        batches=batches,
    )
