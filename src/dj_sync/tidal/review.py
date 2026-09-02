from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from dj_sync.database.database import Database


@dataclass(frozen=True, slots=True)
class ReviewSummary:
    total: int
    approved: int
    rejected: int
    deferred: int
    quit_early: bool


def _format_duration(milliseconds: int | None) -> str:
    if milliseconds is None:
        return "unknown"
    seconds = max(0, int(round(milliseconds / 1000)))
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes}:{seconds:02d}"


def review_match_candidates(
    *,
    database: Database,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> ReviewSummary:
    rows = database.list_match_candidates(status="review")
    approved = 0
    rejected = 0
    deferred = 0
    quit_early = False

    if not rows:
        output_fn("No TIDAL match candidates are waiting for review.")
        return ReviewSummary(0, 0, 0, 0, False)

    output_fn(f"DJ Sync — Match review ({len(rows)} candidate(s))")
    output_fn("Approve only when you are confident the TIDAL recording is the one you want.\n")

    for index, row in enumerate(rows, start=1):
        output_fn(f"[{index}/{len(rows)}]")
        output_fn(f"Spotify: {row['spotify_artist']} — {row['spotify_title']}")
        output_fn(f"         duration {_format_duration(row['spotify_duration_ms'])}")
        output_fn(f"TIDAL:   {row['tidal_artist']} — {row['tidal_title']}")
        output_fn(f"         duration {_format_duration(row['tidal_duration_ms'])}")
        output_fn(
            "Score:   "
            f"{row['score']:.1%} total "
            f"(title {row['title_score']:.1%}, "
            f"artist {row['artist_score']:.1%}, "
            f"duration {row['duration_score']:.1%})"
        )

        while True:
            action = input_fn("[A]pprove  [R]eject candidate  [S]kip for now  [Q]uit: ").strip().lower()
            if action in {"a", "approve"}:
                database.save_track_match(
                    spotify_track_id=row["spotify_track_id"],
                    tidal_track_id=row["tidal_track_id"],
                    method="manual",
                    score=float(row["score"]),
                )
                approved += 1
                output_fn("  ✓ Approved and saved as a permanent manual match.\n")
                break
            if action in {"r", "reject"}:
                database.reject_match_candidate(row["spotify_track_id"])
                rejected += 1
                output_fn("  ✗ Candidate rejected; track remains unmatched.\n")
                break
            if action in {"s", "skip", "n", "next"}:
                deferred += 1
                output_fn("  → Left in the review queue for later.\n")
                break
            if action in {"q", "quit"}:
                quit_early = True
                output_fn("Review stopped. Remaining candidates stay in the queue.")
                return ReviewSummary(
                    total=len(rows),
                    approved=approved,
                    rejected=rejected,
                    deferred=deferred,
                    quit_early=True,
                )
            output_fn("Please choose A, R, S, or Q.")

    return ReviewSummary(
        total=len(rows),
        approved=approved,
        rejected=rejected,
        deferred=deferred,
        quit_early=quit_early,
    )
