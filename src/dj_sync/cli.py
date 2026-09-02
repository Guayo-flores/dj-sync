from __future__ import annotations

import argparse

from dj_sync.config import Settings
from dj_sync.database.database import Database
from dj_sync.playlist_selection import parse_selection
from dj_sync.spotify.auth import (
    SpotifyTokenStore,
    login_with_pkce,
    refresh_access_token as refresh_spotify_access_token,
)
from dj_sync.spotify.client import SpotifyClient, SpotifyPlaylist
from dj_sync.spotify.ingest import ingest_managed_playlists
from dj_sync.tidal.auth import (
    TidalTokenStore,
    login_with_pkce as tidal_login_with_pkce,
    refresh_access_token as refresh_tidal_access_token,
)
from dj_sync.tidal.client import TidalClient
from dj_sync.tidal.matcher import match_unmatched_tracks_by_isrc
from dj_sync.tidal.manual_resolution import resolve_unmatched_tracks
from dj_sync.tidal.metadata_matcher import match_unmatched_tracks_by_metadata
from dj_sync.tidal.review import review_match_candidates
from dj_sync.sync.incremental import build_incremental_sync_plan, execute_incremental_sync
from dj_sync.sync.lifecycle import cleanup_pending_playlists, refresh_playlist_lifecycle


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dj-sync",
        description="Mirror selected Spotify playlists to TIDAL.",
    )
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("init-db", help="Initialize the SQLite state database.")
    subparsers.add_parser("spotify-login", help="Authorize DJ Sync with Spotify.")
    subparsers.add_parser("spotify-playlists", help="List Spotify playlists visible to DJ Sync.")
    subparsers.add_parser("spotify-select", help="Choose which Spotify playlists DJ Sync manages.")
    subparsers.add_parser("managed-playlists", help="Show saved DJ Sync playlist selections.")
    subparsers.add_parser(
        "playlist-cleanup",
        help="Resolve Spotify playlists that were deleted while keeping TIDAL safe by default.",
    )
    subparsers.add_parser(
        "spotify-ingest",
        help="Fetch and persist normalized tracks from managed Spotify playlists.",
    )
    subparsers.add_parser("tidal-login", help="Authorize DJ Sync with TIDAL.")
    subparsers.add_parser(
        "tidal-write-test",
        help="Create, verify, and delete a temporary TIDAL playlist.",
    )
    isrc_parser = subparsers.add_parser(
        "tidal-match-isrc",
        help="Match unmatched Spotify tracks to TIDAL by exact ISRC.",
    )
    isrc_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only process this many unmatched Spotify tracks.",
    )

    metadata_parser = subparsers.add_parser(
        "tidal-match-metadata",
        help="Search TIDAL for tracks that did not match by exact ISRC.",
    )
    metadata_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only process this many unmatched Spotify tracks.",
    )

    subparsers.add_parser(
        "review",
        help="Interactively approve or reject ambiguous Spotify-to-TIDAL matches.",
    )
    subparsers.add_parser(
        "resolve-unmatched",
        help="Search TIDAL interactively and manually link remaining unmatched tracks.",
    )

    sync_parser = subparsers.add_parser("sync", help="Synchronize managed playlists.")
    sync_mode = sync_parser.add_mutually_exclusive_group()
    sync_mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without modifying TIDAL.",
    )
    sync_mode.add_argument(
        "--apply",
        action="store_true",
        help="Apply the verified plan to managed TIDAL playlists.",
    )
    sync_parser.add_argument(
        "--playlist",
        default=None,
        help="Only preview/apply one managed playlist by exact name.",
    )
    return parser


def _spotify_client(settings: Settings) -> SpotifyClient:
    if not settings.spotify_client_id:
        raise RuntimeError(
            "SPOTIFY_CLIENT_ID is missing. Copy .env.example to .env and add it."
        )

    token_store = SpotifyTokenStore(settings.spotify_token_path)
    token = token_store.load()
    if token is None:
        raise RuntimeError("Spotify is not connected. Run: dj-sync spotify-login")

    def refresh() -> str:
        current = token_store.load()
        if current is None or not current.refresh_token:
            raise RuntimeError(
                "Spotify session expired and no refresh token is available. "
                "Run: dj-sync spotify-login"
            )
        refreshed = refresh_spotify_access_token(
            client_id=settings.spotify_client_id,
            refresh_token=current.refresh_token,
        )
        token_store.save(refreshed)
        return refreshed.access_token

    return SpotifyClient(token.access_token, token_refresher=refresh)


def _tidal_client(settings: Settings) -> TidalClient:
    token_store = TidalTokenStore(settings.tidal_token_path)
    token = token_store.load()
    if token is None:
        raise RuntimeError("TIDAL is not connected. Run: dj-sync tidal-login")

    def refresh() -> str:
        current = token_store.load()
        if current is None or not current.refresh_token:
            raise RuntimeError(
                "TIDAL session expired and no refresh token is available. "
                "Run: dj-sync tidal-login"
            )
        refreshed = refresh_tidal_access_token(refresh_token=current.refresh_token)
        token_store.save(refreshed)
        return refreshed.access_token

    return TidalClient(token.access_token, token_refresher=refresh)


def _spotify_playlists(settings: Settings) -> list[SpotifyPlaylist]:
    return list(_spotify_client(settings).iter_playlists())


def _print_spotify_playlists(playlists: list[SpotifyPlaylist]) -> None:
    for index, playlist in enumerate(playlists, start=1):
        count = "?" if playlist.item_count is None else playlist.item_count
        eligibility = "" if playlist.can_read_items else " — unavailable for item sync"
        print(f"{index:>2}. {playlist.name} ({count} items){eligibility}")


def main() -> int:
    args = build_parser().parse_args()
    settings = Settings.from_env()
    database = Database(settings.database_path)

    if args.command == "init-db":
        database.initialize()
        print(f"DJ Sync database initialized: {database.path}")
        return 0

    if args.command == "spotify-login":
        if not settings.spotify_client_id:
            raise RuntimeError(
                "SPOTIFY_CLIENT_ID is missing. Copy .env.example to .env and add it."
            )
        token_store = SpotifyTokenStore(settings.spotify_token_path)
        login_with_pkce(
            client_id=settings.spotify_client_id,
            redirect_uri=settings.spotify_redirect_uri,
            token_store=token_store,
        )
        print("Spotify connected successfully.")
        return 0

    if args.command == "tidal-login":
        if not settings.tidal_client_id:
            raise RuntimeError(
                "TIDAL_CLIENT_ID is missing. Add it to your local .env file."
            )
        token_store = TidalTokenStore(settings.tidal_token_path)
        tidal_login_with_pkce(
            client_id=settings.tidal_client_id,
            redirect_uri=settings.tidal_redirect_uri,
            token_store=token_store,
        )
        print("TIDAL connected successfully.")
        return 0

    if args.command == "tidal-write-test":
        client = _tidal_client(settings)
        created_playlist = None

        print("Creating temporary TIDAL playlist: DJ Sync Test")
        try:
            created_playlist = client.create_playlist(
                "DJ Sync Test",
                description="Temporary playlist created by DJ Sync to verify API write access.",
            )
            print(f"  ✓ Created [{created_playlist.id}]")

            fetched_playlist = client.get_playlist(created_playlist.id)
            if fetched_playlist.name != "DJ Sync Test":
                raise RuntimeError(
                    "TIDAL returned an unexpected playlist name during verification"
                )
            print("  ✓ Read-back verification passed")
        finally:
            if created_playlist is not None:
                client.delete_playlist(created_playlist.id)
                print("  ✓ Temporary playlist deleted")

        print("TIDAL playlist write test passed.")
        return 0

    if args.command == "tidal-match-isrc":
        database.initialize()
        client = _tidal_client(settings)
        print(
            "Matching is paced automatically; DJ Sync will wait and retry if "
            "TIDAL rate-limits the initial library import."
        )
        summary = match_unmatched_tracks_by_isrc(
            client=client, database=database, limit=args.limit
        )
        counts = database.track_match_counts()
        print("DJ Sync — TIDAL exact ISRC matching")
        print(f"Candidates processed: {summary.candidates}")
        print(f"Exact matches:        {summary.matched}")
        print(f"ISRC misses:          {summary.misses}")
        print(f"Invalid ISRCs:        {summary.invalid}")
        print(f"API batches:          {summary.batches}")
        print(f"Mapped tracks total:  {counts['matched']} / {counts['total']}")
        return 0

    if args.command == "tidal-match-metadata":
        database.initialize()
        client = _tidal_client(settings)
        print(
            "Metadata fallback uses conservative auto-match rules; ambiguous "
            "remixes/edits are held for review."
        )
        summary = match_unmatched_tracks_by_metadata(
            client=client, database=database, limit=args.limit
        )
        counts = database.track_match_counts()
        print("DJ Sync — TIDAL metadata matching")
        print(f"Candidates processed: {summary.candidates}")
        print(f"Automatic matches:    {summary.automatic}")
        print(f"Needs review:         {summary.review}")
        print(f"Not found/low score:  {summary.not_found}")
        print(f"Mapped tracks total:  {counts['matched']} / {counts['total']}")
        if summary.reviews:
            print("\nReview candidates:")
            for item in summary.reviews:
                print(
                    f"  ? {item.spotify_artist} — {item.spotify_title}\n"
                    f"    → {item.tidal_artist} — {item.tidal_title} "
                    f"({item.score:.1%})"
                )
        return 0

    if args.command == "review":
        database.initialize()
        summary = review_match_candidates(database=database)
        counts = database.track_match_counts()
        remaining = len(database.list_match_candidates(status="review"))
        print("\nDJ Sync — Review summary")
        print(f"Approved:             {summary.approved}")
        print(f"Rejected candidates:  {summary.rejected}")
        print(f"Deferred:             {summary.deferred}")
        print(f"Still awaiting review:{remaining:>3}")
        print(f"Mapped tracks total:  {counts['matched']} / {counts['total']}")
        return 0

    if args.command == "resolve-unmatched":
        database.initialize()
        client = _tidal_client(settings)
        summary = resolve_unmatched_tracks(client=client, database=database)
        counts = database.track_match_counts()
        remaining = len(database.list_unmatched_tracks())
        print("\nDJ Sync — Manual resolution summary")
        print(f"Resolved:             {summary.resolved}")
        print(f"Skipped:              {summary.skipped}")
        print(f"Still unmatched:      {remaining}")
        print(f"Mapped tracks total:  {counts['matched']} / {counts['total']}")
        return 0

    if args.command == "spotify-playlists":
        playlists = _spotify_playlists(settings)
        if not playlists:
            print("No Spotify playlists were returned.")
            return 0
        _print_spotify_playlists(playlists)
        return 0

    if args.command == "spotify-select":
        database.initialize()
        playlists = _spotify_playlists(settings)
        if not playlists:
            print("No Spotify playlists were returned.")
            return 0

        print("DJ Sync — Choose managed Spotify playlists")
        print("Only playlists whose items Spotify allows DJ Sync to read are selectable.\n")
        _print_spotify_playlists(playlists)
        raw = input("\nSelect playlists (example: 1,3,5-8; or 'all'): ")
        selected = parse_selection(raw, playlists)

        database.pause_unselected_playlists(playlist.id for playlist in selected)
        for playlist in selected:
            database.upsert_managed_playlist(playlist.id, playlist.name)

        print(f"\nSaved {len(selected)} managed playlist(s).")
        for playlist in selected:
            print(f"  ✓ {playlist.name}")
        return 0

    if args.command == "spotify-ingest":
        database.initialize()
        client = _spotify_client(settings)
        managed = database.list_managed_playlists()
        if not managed:
            print("No managed playlists saved yet. Run: dj-sync spotify-select")
            return 0

        print(f"DJ Sync — Spotify ingestion ({len(managed)} managed playlists)")
        summary = ingest_managed_playlists(client=client, database=database)
        for result in summary.playlists:
            skipped = f", {result.skipped_items} skipped" if result.skipped_items else ""
            print(f"  ✓ {result.name}: {result.tracks_saved} tracks{skipped}")

        print("\nSpotify ingestion complete.")
        print(f"Playlist entries saved: {summary.total_tracks_saved}")
        print(f"Unique Spotify tracks: {summary.unique_tracks}")
        if summary.total_skipped_items:
            print(f"Skipped non-track/local/unavailable items: {summary.total_skipped_items}")
        return 0

    if args.command == "managed-playlists":
        database.initialize()
        rows = database.list_playlists()
        if not rows:
            print("No managed playlists saved yet. Run: dj-sync spotify-select")
            return 0
        for row in rows:
            if row["pending_deletion"]:
                marker = "⚠"
                status = "missing on Spotify — cleanup pending"
            elif row["status"] == "managed":
                marker = "✓"
                status = "managed"
            else:
                marker = "⏸"
                status = "paused"
            tidal = " linked to TIDAL" if row["tidal_playlist_id"] else ""
            print(f"{marker} {row['spotify_name']} [{status}]{tidal}")
        return 0

    if args.command == "playlist-cleanup":
        database.initialize()
        client = _tidal_client(settings)
        summary = cleanup_pending_playlists(client=client, database=database)
        print("\nDJ Sync — Playlist cleanup summary")
        print(f"Deleted from TIDAL: {summary.deleted}")
        print(f"Kept + paused:      {summary.kept}")
        print(f"Skipped:            {summary.skipped}")
        print(f"Still pending:      {summary.remaining}")
        return 0

    if args.command == "sync":
        database.initialize()

        # A sync command always refreshes Spotify first. The local SQLite snapshot
        # is a cache/state store; Spotify remains the source of truth.
        spotify_client = _spotify_client(settings)
        lifecycle_summary = refresh_playlist_lifecycle(
            client=spotify_client, database=database
        )
        ingest_summary = ingest_managed_playlists(client=spotify_client, database=database)

        tidal_client = _tidal_client(settings)

        # Only newly seen/unmapped recordings flow through these passes. Existing
        # Spotify -> TIDAL mappings are reused globally across every playlist.
        isrc_summary = match_unmatched_tracks_by_isrc(
            client=tidal_client, database=database
        )
        metadata_summary = match_unmatched_tracks_by_metadata(
            client=tidal_client, database=database
        )

        plan = build_incremental_sync_plan(
            client=tidal_client,
            database=database,
            playlist_name=args.playlist,
        )

        print("DJ Sync — refreshed source state")
        print(f"Spotify playlists visible:   {lifecycle_summary.visible_playlists}")
        print(f"Managed playlists refreshed: {len(ingest_summary.playlists)}")
        print(f"Spotify playlist entries:    {ingest_summary.total_tracks_saved}")
        if lifecycle_summary.renamed:
            print(f"Playlist renames detected:   {len(lifecycle_summary.renamed)}")
        if lifecycle_summary.newly_missing:
            print(f"New missing playlists:       {len(lifecycle_summary.newly_missing)}")
        if lifecycle_summary.restored:
            print(f"Restored playlists:          {len(lifecycle_summary.restored)}")
        print(f"New exact ISRC matches:      {isrc_summary.matched}")
        print(f"New metadata matches:        {metadata_summary.automatic}")
        if metadata_summary.review:
            print(f"New matches needing review:  {metadata_summary.review}")
        print()

        if args.dry_run:
            scope = f' — {args.playlist}' if args.playlist else ""
            print(f"DJ Sync — incremental preview (DRY RUN){scope}")
            print(f"Playlists checked:     {len(plan.playlists)}")
            print(f"Playlists changed:     {plan.playlists_changed}")
            print(f"Playlists to create:   {plan.playlists_to_create}")
            print(f"Tracks to add:         {plan.tracks_to_add}")
            print(f"Tracks to remove:      {plan.tracks_to_remove}")
            print(f"Unmatched entries:     {plan.unmatched_entries}")
            print()

            for playlist in plan.playlists:
                if playlist.action == "unchanged":
                    print(f"  = {playlist.spotify_name}: unchanged")
                    continue
                action = "CREATE" if playlist.action == "create" else "UPDATE"
                rename = " + rename" if playlist.rename else ""
                print(
                    f"  {action:<6} {playlist.spotify_name}: "
                    f"+{playlist.tracks_to_add} -{playlist.tracks_to_remove}{rename}"
                )
                for item in playlist.unmatched_entries:
                    print(
                        f"         ! skip #{item.position + 1}: "
                        f"{item.artist} — {item.title}"
                    )

            pending = database.list_pending_deletion_playlists()
            if pending:
                print("\n  ⚠ Spotify playlist deletion(s) awaiting cleanup:")
                for row in pending:
                    print(f"    - {row['spotify_name']} (TIDAL copy preserved)")
                print("    Run: dj-sync playlist-cleanup")

            print("\nDry run only — TIDAL was not modified.")
            return 0

        if not args.apply:
            print("DJ Sync — SYNC")
            print("Choose an explicit mode:")
            print("  dj-sync sync --dry-run")
            print("  dj-sync sync --apply")
            return 2

        print("DJ Sync — APPLYING INCREMENTAL SYNC")
        print("Spotify is the source of truth for DJ Sync-managed TIDAL playlists.")
        summary = execute_incremental_sync(
            client=tidal_client, database=database, plan=plan
        )
        for item in summary.playlists:
            changes = []
            if item.created:
                changes.append("created")
            if item.renamed:
                changes.append("renamed")
            if item.tracks_added:
                changes.append(f"+{item.tracks_added}")
            if item.tracks_removed:
                changes.append(f"-{item.tracks_removed}")
            if not changes:
                changes.append("unchanged")
            suffix = (
                f"; {item.unmatched_skipped} unmatched skipped"
                if item.unmatched_skipped
                else ""
            )
            print(f"  ✓ {item.name}: {', '.join(changes)}{suffix}")

        print("\nIncremental TIDAL sync complete.")
        print(f"Playlists created: {summary.playlists_created}")
        print(f"Playlists renamed: {summary.playlists_renamed}")
        print(f"Tracks added:      {summary.tracks_added}")
        print(f"Tracks removed:    {summary.tracks_removed}")
        return 0

    build_parser().print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
