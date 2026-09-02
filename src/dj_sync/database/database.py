from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable

from dj_sync.database.schema import SCHEMA


class Database:
    def __init__(self, path: str | Path = "data/dj_sync.db") -> None:
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)

    def table_names(self) -> set[str]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        return {row["name"] for row in rows}

    def upsert_managed_playlist(self, spotify_playlist_id: str, name: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO playlists (
                    spotify_playlist_id,
                    spotify_name,
                    status,
                    managed_by_dj_sync,
                    pending_deletion,
                    updated_at
                )
                VALUES (?, ?, 'managed', 1, 0, CURRENT_TIMESTAMP)
                ON CONFLICT(spotify_playlist_id) DO UPDATE SET
                    spotify_name = excluded.spotify_name,
                    status = 'managed',
                    managed_by_dj_sync = 1,
                    pending_deletion = 0,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (spotify_playlist_id, name),
            )

    def pause_unselected_playlists(self, selected_spotify_ids: Iterable[str]) -> None:
        selected = tuple(selected_spotify_ids)
        with self.connect() as connection:
            if not selected:
                connection.execute(
                    "UPDATE playlists SET status = 'paused', updated_at = CURRENT_TIMESTAMP"
                )
                return
            placeholders = ",".join("?" for _ in selected)
            connection.execute(
                f"""
                UPDATE playlists
                SET status = 'paused', updated_at = CURRENT_TIMESTAMP
                WHERE spotify_playlist_id NOT IN ({placeholders})
                """,
                selected,
            )

    def list_playlists(self) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return connection.execute(
                """
                SELECT spotify_playlist_id, tidal_playlist_id, spotify_name,
                       tidal_name, status, pending_deletion, last_synced_at
                FROM playlists
                ORDER BY spotify_name COLLATE NOCASE
                """
            ).fetchall()
