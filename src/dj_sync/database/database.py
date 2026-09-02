from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable, Sequence

from dj_sync.database.schema import SCHEMA
from dj_sync.spotify.normalizer import NormalizedPlaylistTrack


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
            self._migrate_tracks_columns(connection)
            self._migrate_playlist_tracks_primary_key(connection)

    @staticmethod
    def _column_names(connection: sqlite3.Connection, table: str) -> set[str]:
        rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
        return {row["name"] for row in rows}

    def _migrate_tracks_columns(self, connection: sqlite3.Connection) -> None:
        columns = self._column_names(connection, "tracks")
        if "album" not in columns:
            connection.execute("ALTER TABLE tracks ADD COLUMN album TEXT")
        if "spotify_uri" not in columns:
            connection.execute("ALTER TABLE tracks ADD COLUMN spotify_uri TEXT")

    @staticmethod
    def _migrate_playlist_tracks_primary_key(connection: sqlite3.Connection) -> None:
        info = connection.execute("PRAGMA table_info(playlist_tracks)").fetchall()
        primary_key_columns = [
            row["name"] for row in sorted(info, key=lambda row: row["pk"]) if row["pk"]
        ]
        if primary_key_columns == ["playlist_id", "position"]:
            return

        connection.execute("ALTER TABLE playlist_tracks RENAME TO playlist_tracks_legacy")
        connection.executescript(
            """
            CREATE TABLE playlist_tracks (
                playlist_id INTEGER NOT NULL,
                spotify_track_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                added_at TEXT,
                last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (playlist_id, position),
                FOREIGN KEY (playlist_id) REFERENCES playlists(id) ON DELETE CASCADE,
                FOREIGN KEY (spotify_track_id) REFERENCES tracks(spotify_track_id) ON DELETE RESTRICT
            );
            CREATE INDEX IF NOT EXISTS idx_playlist_tracks_position
            ON playlist_tracks(playlist_id, position);
            CREATE INDEX IF NOT EXISTS idx_playlist_tracks_spotify_track
            ON playlist_tracks(spotify_track_id);
            """
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO playlist_tracks (
                playlist_id, spotify_track_id, position, added_at, last_seen_at
            )
            SELECT playlist_id, spotify_track_id, position, added_at, last_seen_at
            FROM playlist_tracks_legacy
            """
        )
        connection.execute("DROP TABLE playlist_tracks_legacy")

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

    def list_managed_playlists(self) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return connection.execute(
                """
                SELECT id, spotify_playlist_id, spotify_name
                FROM playlists
                WHERE status = 'managed' AND managed_by_dj_sync = 1
                ORDER BY spotify_name COLLATE NOCASE
                """
            ).fetchall()

    def replace_spotify_playlist_snapshot(
        self,
        spotify_playlist_id: str,
        tracks: Sequence[NormalizedPlaylistTrack],
    ) -> None:
        """Persist the latest observed Spotify state for one managed playlist.

        Track metadata is global, while playlist membership is replaced as a
        snapshot. Position is the membership key so duplicate songs in the same
        playlist are preserved.
        """
        with self.connect() as connection:
            playlist = connection.execute(
                "SELECT id FROM playlists WHERE spotify_playlist_id = ?",
                (spotify_playlist_id,),
            ).fetchone()
            if playlist is None:
                raise KeyError(f"Unknown managed Spotify playlist: {spotify_playlist_id}")

            for item in tracks:
                track = item.track
                connection.execute(
                    """
                    INSERT INTO tracks (
                        spotify_track_id, isrc, title, artist, album, spotify_uri,
                        duration_ms, last_seen_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(spotify_track_id) DO UPDATE SET
                        isrc = excluded.isrc,
                        title = excluded.title,
                        artist = excluded.artist,
                        album = excluded.album,
                        spotify_uri = excluded.spotify_uri,
                        duration_ms = excluded.duration_ms,
                        last_seen_at = CURRENT_TIMESTAMP
                    """,
                    (
                        track.spotify_id,
                        track.isrc,
                        track.title,
                        track.artist,
                        track.album,
                        track.spotify_uri,
                        track.duration_ms,
                    ),
                )

            playlist_id = playlist["id"]
            connection.execute(
                "DELETE FROM playlist_tracks WHERE playlist_id = ?", (playlist_id,)
            )
            connection.executemany(
                """
                INSERT INTO playlist_tracks (
                    playlist_id, spotify_track_id, position, added_at, last_seen_at
                )
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                [
                    (playlist_id, item.track.spotify_id, item.position, item.added_at)
                    for item in tracks
                ],
            )


    def list_tracks_pending_isrc_match(self, limit: int | None = None) -> list[sqlite3.Row]:
        query = """
            SELECT spotify_track_id, isrc, title, artist, duration_ms
            FROM tracks
            WHERE tidal_track_id IS NULL
              AND isrc IS NOT NULL
              AND TRIM(isrc) != ''
              AND (match_method IS NULL OR match_method NOT IN ('isrc_not_found', 'isrc_invalid'))
            ORDER BY spotify_track_id
        """
        params: tuple[object, ...] = ()
        if limit is not None:
            query += " LIMIT ?"
            params = (limit,)
        with self.connect() as connection:
            return connection.execute(query, params).fetchall()

    def list_tracks_pending_metadata_match(self, limit: int | None = None) -> list[sqlite3.Row]:
        query = """
            SELECT spotify_track_id, isrc, title, artist, duration_ms
            FROM tracks
            WHERE tidal_track_id IS NULL
              AND spotify_track_id NOT IN (
                  SELECT spotify_track_id FROM match_candidates
              )
            ORDER BY spotify_track_id
        """
        params: tuple[object, ...] = ()
        if limit is not None:
            query += " LIMIT ?"
            params = (limit,)
        with self.connect() as connection:
            return connection.execute(query, params).fetchall()

    def save_match_candidate(
        self,
        *,
        spotify_track_id: str,
        tidal_track_id: str | None,
        tidal_title: str | None,
        tidal_artist: str | None,
        tidal_duration_ms: int | None,
        score: float,
        title_score: float | None,
        artist_score: float | None,
        duration_score: float | None,
        status: str,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO match_candidates (
                    spotify_track_id, tidal_track_id, tidal_title, tidal_artist,
                    tidal_duration_ms, score, title_score, artist_score,
                    duration_score, status, searched_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(spotify_track_id) DO UPDATE SET
                    tidal_track_id = excluded.tidal_track_id,
                    tidal_title = excluded.tidal_title,
                    tidal_artist = excluded.tidal_artist,
                    tidal_duration_ms = excluded.tidal_duration_ms,
                    score = excluded.score,
                    title_score = excluded.title_score,
                    artist_score = excluded.artist_score,
                    duration_score = excluded.duration_score,
                    status = excluded.status,
                    searched_at = CURRENT_TIMESTAMP
                """,
                (
                    spotify_track_id, tidal_track_id, tidal_title, tidal_artist,
                    tidal_duration_ms, score, title_score, artist_score,
                    duration_score, status,
                ),
            )

    def list_match_candidates(self, *, status: str = "review") -> list[sqlite3.Row]:
        with self.connect() as connection:
            return connection.execute(
                """
                SELECT mc.*, t.title AS spotify_title, t.artist AS spotify_artist,
                       t.duration_ms AS spotify_duration_ms
                FROM match_candidates mc
                JOIN tracks t ON t.spotify_track_id = mc.spotify_track_id
                WHERE mc.status = ?
                ORDER BY mc.score DESC, t.artist COLLATE NOCASE, t.title COLLATE NOCASE
                """,
                (status,),
            ).fetchall()

    def reject_match_candidate(self, spotify_track_id: str) -> None:
        """Reject the currently suggested TIDAL candidate without rematching it.

        The candidate is retained as ``not_found`` so future metadata matching
        runs do not immediately propose the same rejected recording again.
        """
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE match_candidates
                SET status = 'not_found', searched_at = CURRENT_TIMESTAMP
                WHERE spotify_track_id = ? AND status = 'review'
                """,
                (spotify_track_id,),
            )

    def save_track_match(
        self,
        *,
        spotify_track_id: str,
        tidal_track_id: str,
        method: str,
        score: float,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE tracks
                SET tidal_track_id = ?,
                    match_method = ?,
                    match_score = ?,
                    first_matched_at = COALESCE(first_matched_at, CURRENT_TIMESTAMP)
                WHERE spotify_track_id = ?
                """,
                (tidal_track_id, method, score, spotify_track_id),
            )
            connection.execute(
                "DELETE FROM match_candidates WHERE spotify_track_id = ?",
                (spotify_track_id,),
            )

    def mark_isrc_miss(self, spotify_track_id: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE tracks
                SET match_method = 'isrc_not_found', match_score = 0
                WHERE spotify_track_id = ? AND tidal_track_id IS NULL
                """,
                (spotify_track_id,),
            )

    def mark_isrc_invalid(self, spotify_track_id: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE tracks
                SET match_method = 'isrc_invalid', match_score = 0
                WHERE spotify_track_id = ? AND tidal_track_id IS NULL
                """,
                (spotify_track_id,),
            )

    def track_match_counts(self) -> dict[str, int]:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN tidal_track_id IS NOT NULL THEN 1 ELSE 0 END) AS matched,
                    SUM(CASE WHEN tidal_track_id IS NULL AND isrc IS NULL THEN 1 ELSE 0 END) AS no_isrc,
                    SUM(CASE WHEN match_method = 'isrc_not_found' THEN 1 ELSE 0 END) AS isrc_misses
                FROM tracks
                """
            ).fetchone()
        return {
            "total": int(row["total"] or 0),
            "matched": int(row["matched"] or 0),
            "no_isrc": int(row["no_isrc"] or 0),
            "isrc_misses": int(row["isrc_misses"] or 0),
        }

    def count_tracks(self) -> int:
        with self.connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM tracks").fetchone()
        return int(row["count"])

    def count_playlist_tracks(self, spotify_playlist_id: str) -> int:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM playlist_tracks pt
                JOIN playlists p ON p.id = pt.playlist_id
                WHERE p.spotify_playlist_id = ?
                """,
                (spotify_playlist_id,),
            ).fetchone()
        return int(row["count"])
