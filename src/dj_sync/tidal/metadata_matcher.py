from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Callable

from dj_sync.database.database import Database
from dj_sync.matching.metadata import is_safe_automatic_match, score_metadata_match
from dj_sync.tidal.client import TidalClient, TidalTrack


@dataclass(frozen=True, slots=True)
class MetadataReview:
    spotify_title: str
    spotify_artist: str
    tidal_title: str
    tidal_artist: str
    score: float


@dataclass(frozen=True, slots=True)
class MetadataMatchSummary:
    candidates: int
    automatic: int
    review: int
    not_found: int
    reviews: tuple[MetadataReview, ...]


def _best_candidate(row, candidates: list[TidalTrack]):
    best = None
    for candidate in candidates:
        score = score_metadata_match(
            spotify_title=row["title"],
            spotify_artist=row["artist"],
            spotify_duration_ms=row["duration_ms"],
            tidal_title=candidate.title,
            tidal_artists=candidate.artists,
            tidal_duration_ms=candidate.duration_ms,
        )
        if best is None or score.total > best[1].total:
            best = (candidate, score)
    return best


def match_unmatched_tracks_by_metadata(
    *,
    client: TidalClient,
    database: Database,
    limit: int | None = None,
    search_delay_seconds: float = 0.75,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> MetadataMatchSummary:
    rows = database.list_tracks_pending_metadata_match(limit=limit)
    automatic = 0
    review = 0
    not_found = 0
    reviews: list[MetadataReview] = []

    for row in rows:
        query = f'{row["title"]} {row["artist"]}'
        candidates = client.search_tracks(query, limit=10)
        best = _best_candidate(row, candidates)

        if best is None:
            database.save_match_candidate(
                spotify_track_id=row["spotify_track_id"],
                tidal_track_id=None,
                tidal_title=None,
                tidal_artist=None,
                tidal_duration_ms=None,
                score=0.0,
                title_score=None,
                artist_score=None,
                duration_score=None,
                status="not_found",
            )
            not_found += 1
        else:
            candidate, score = best
            tidal_artist = ", ".join(candidate.artists)
            if is_safe_automatic_match(score):
                database.save_track_match(
                    spotify_track_id=row["spotify_track_id"],
                    tidal_track_id=candidate.id,
                    method="metadata",
                    score=score.total,
                )
                automatic += 1
            elif score.total >= 0.75:
                database.save_match_candidate(
                    spotify_track_id=row["spotify_track_id"],
                    tidal_track_id=candidate.id,
                    tidal_title=candidate.title,
                    tidal_artist=tidal_artist,
                    tidal_duration_ms=candidate.duration_ms,
                    score=score.total,
                    title_score=score.title,
                    artist_score=score.artist,
                    duration_score=score.duration,
                    status="review",
                )
                reviews.append(
                    MetadataReview(
                        spotify_title=row["title"],
                        spotify_artist=row["artist"],
                        tidal_title=candidate.title,
                        tidal_artist=tidal_artist,
                        score=score.total,
                    )
                )
                review += 1
            else:
                database.save_match_candidate(
                    spotify_track_id=row["spotify_track_id"],
                    tidal_track_id=candidate.id,
                    tidal_title=candidate.title,
                    tidal_artist=tidal_artist,
                    tidal_duration_ms=candidate.duration_ms,
                    score=score.total,
                    title_score=score.title,
                    artist_score=score.artist,
                    duration_score=score.duration,
                    status="not_found",
                )
                not_found += 1

        if search_delay_seconds > 0:
            sleep_fn(search_delay_seconds)

    return MetadataMatchSummary(
        candidates=len(rows),
        automatic=automatic,
        review=review,
        not_found=not_found,
        reviews=tuple(reviews),
    )
