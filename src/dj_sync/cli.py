from __future__ import annotations

import argparse

from dj_sync.config import Settings
from dj_sync.database.database import Database


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dj-sync",
        description="Mirror selected Spotify playlists to TIDAL.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview sync changes without modifying TIDAL.",
    )
    parser.add_argument(
        "--init-db",
        action="store_true",
        help="Initialize the local SQLite state database.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    settings = Settings.from_env()
    database = Database(settings.database_path)

    if args.init_db:
        database.initialize()
        print(f"DJ Sync database initialized: {database.path}")
        return 0

    database.initialize()
    mode = "DRY RUN" if args.dry_run else "SYNC"
    print(f"DJ Sync — {mode}")
    print("Spotify/TIDAL API connections are the next milestone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
