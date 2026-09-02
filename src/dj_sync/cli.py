from __future__ import annotations

import argparse

from dj_sync.config import Settings
from dj_sync.database.database import Database
from dj_sync.spotify.auth import SpotifyTokenStore, login_with_pkce
from dj_sync.spotify.client import SpotifyClient


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dj-sync",
        description="Mirror selected Spotify playlists to TIDAL.",
    )
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("init-db", help="Initialize the SQLite state database.")
    subparsers.add_parser("spotify-login", help="Authorize DJ Sync with Spotify.")
    subparsers.add_parser("spotify-playlists", help="List Spotify playlists visible to DJ Sync.")

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
        token = _spotify_token(settings)
        client = SpotifyClient(token.access_token)
        playlists = list(client.iter_playlists())
        if not playlists:
            print("No Spotify playlists were returned.")
            return 0
        for index, playlist in enumerate(playlists, start=1):
            count = "?" if playlist.item_count is None else playlist.item_count
            print(f"{index:>2}. {playlist.name} ({count} items) [{playlist.id}]")
        return 0

    if args.command == "sync":
        database.initialize()
        mode = "DRY RUN" if args.dry_run else "SYNC"
        print(f"DJ Sync — {mode}")
        print("Spotify authentication is implemented; playlist selection is next.")
        return 0

    build_parser().print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
