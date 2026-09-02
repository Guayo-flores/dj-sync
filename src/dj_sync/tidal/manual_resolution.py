from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from dj_sync.database.database import Database
from dj_sync.matching.metadata import score_metadata_match
from dj_sync.tidal.client import TidalClient, TidalTrack


@dataclass(frozen=True, slots=True)
class ManualResolutionSummary:
    total: int
    resolved: int
    skipped: int
    quit_early: bool


def _format_duration(milliseconds: int | None) -> str:
    if milliseconds is None:
        return "unknown"
    seconds = max(0, int(round(milliseconds / 1000)))
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes}:{seconds:02d}"


def _artist_text(track: TidalTrack) -> str:
    return ", ".join(track.artists) if track.artists else "Unknown artist"


def _candidate_score(row, track: TidalTrack):
    return score_metadata_match(
        spotify_title=row["title"],
        spotify_artist=row["artist"],
        spotify_duration_ms=row["duration_ms"],
        tidal_title=track.title,
        tidal_artists=track.artists,
        tidal_duration_ms=track.duration_ms,
    )


def resolve_unmatched_tracks(
    *,
    client: TidalClient,
    database: Database,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
    search_limit: int = 5,
) -> ManualResolutionSummary:
    """Interactively search TIDAL and manually link the remaining Spotify tracks.

    This intentionally keeps search and approval separate: a search result is never
    saved unless the user explicitly chooses its numbered candidate.
    """
    rows = database.list_unmatched_tracks()
    resolved = 0
    skipped = 0

    if not rows:
        output_fn("All ingested Spotify tracks already have TIDAL mappings.")
        return ManualResolutionSummary(0, 0, 0, False)

    output_fn(f"DJ Sync — Resolve unmatched tracks ({len(rows)} track(s))")
    output_fn("Choose a numbered TIDAL result only when it is the recording you want.\n")

    for index, row in enumerate(rows, start=1):
        default_query = f'{row["title"]} {row["artist"]}'
        query = default_query

        while True:
            output_fn(f"[{index}/{len(rows)}]")
            output_fn(f"Spotify: {row['artist']} — {row['title']}")
            output_fn(f"         duration {_format_duration(row['duration_ms'])}")
            output_fn(f"Search:  {query}")

            candidates = client.search_tracks(query, limit=search_limit)
            if candidates:
                for candidate_index, candidate in enumerate(candidates, start=1):
                    score = _candidate_score(row, candidate)
                    output_fn(
                        f"  {candidate_index}. {_artist_text(candidate)} — {candidate.title} "
                        f"[{_format_duration(candidate.duration_ms)}]  {score.total:.1%}"
                    )
            else:
                output_fn("  No TIDAL track results found.")

            prompt = (
                f"Choose [1-{len(candidates)}], [C]ustom search, [S]kip, [Q]uit: "
                if candidates
                else "[C]ustom search  [S]kip  [Q]uit: "
            )
            action = input_fn(prompt).strip()
            lowered = action.lower()

            if action.isdigit() and candidates:
                selected_index = int(action)
                if 1 <= selected_index <= len(candidates):
                    selected = candidates[selected_index - 1]
                    score = _candidate_score(row, selected)
                    database.save_track_match(
                        spotify_track_id=row["spotify_track_id"],
                        tidal_track_id=selected.id,
                        method="manual_search",
                        score=score.total,
                    )
                    resolved += 1
                    output_fn("  ✓ Manual TIDAL mapping saved permanently.\n")
                    break

            if lowered in {"c", "custom", "search"}:
                custom_query = input_fn("Custom TIDAL search: ").strip()
                if custom_query:
                    query = custom_query
                else:
                    output_fn("Search query cannot be empty.")
                continue

            if lowered in {"s", "skip", "n", "next"}:
                skipped += 1
                output_fn("  → Left unmatched for now.\n")
                break

            if lowered in {"q", "quit"}:
                output_fn("Resolution stopped. Remaining tracks stay unmatched.")
                return ManualResolutionSummary(
                    total=len(rows),
                    resolved=resolved,
                    skipped=skipped,
                    quit_early=True,
                )

            output_fn("Please choose a displayed number, C, S, or Q.")

    return ManualResolutionSummary(
        total=len(rows),
        resolved=resolved,
        skipped=skipped,
        quit_early=False,
    )
