from __future__ import annotations

import argparse

from dj_sync.config import Settings
from dj_sync.database.database import Database
from dj_sync.playlist_selection import parse_selection
from dj_sync.spotify.auth import SpotifyTokenStore, login_with_pkce
from dj_sync.spotify.client import SpotifyClient, SpotifyPlaylist


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

    sync_parser = subparsers.add_parser("sync", help="Synchronize managed playlists.")
    sync_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without modifying TIDAL.",
    )
    return parser


def _spotify_token(settings: Settings):
    token_store = SpotifyTokenStore(settings.spotify_token_path)
    token = token_store.load()
    if token is None:
        raise RuntimeError("Spotify is not connected. Run: dj-sync spotify-login")
    return token


def _spotify_playlists(settings: Settings) -> list[SpotifyPlaylist]:
    token = _spotify_token(settings)
    return list(SpotifyClient(token.access_token).iter_playlists())


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

    if args.command == "managed-playlists":
        database.initialize()
        rows = database.list_playlists()
        if not rows:
            print("No managed playlists saved yet. Run: dj-sync spotify-select")
            return 0
        for row in rows:
            marker = "✓" if row["status"] == "managed" else "⏸"
            print(f"{marker} {row['spotify_name']} [{row['status']}]")
        return 0

    if args.command == "sync":
        database.initialize()
        mode = "DRY RUN" if args.dry_run else "SYNC"
        print(f"DJ Sync — {mode}")
        managed = [row for row in database.list_playlists() if row["status"] == "managed"]
        print(f"Managed Spotify playlists: {len(managed)}")
        print("TIDAL authentication and track matching are the next milestones.")
        return 0

    build_parser().print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
