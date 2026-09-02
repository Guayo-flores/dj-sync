SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS playlists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    spotify_playlist_id TEXT NOT NULL UNIQUE,
    tidal_playlist_id TEXT UNIQUE,
    spotify_name TEXT NOT NULL,
    tidal_name TEXT,
    status TEXT NOT NULL DEFAULT 'managed' CHECK (status IN ('managed', 'paused')),
    managed_by_dj_sync INTEGER NOT NULL DEFAULT 1 CHECK (managed_by_dj_sync IN (0, 1)),
    pending_deletion INTEGER NOT NULL DEFAULT 0 CHECK (pending_deletion IN (0, 1)),
    last_synced_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tracks (
    spotify_track_id TEXT PRIMARY KEY,
    tidal_track_id TEXT,
    isrc TEXT,
    title TEXT NOT NULL,
    artist TEXT NOT NULL,
    album TEXT,
    spotify_uri TEXT,
    duration_ms INTEGER NOT NULL,
    match_method TEXT,
    match_score REAL,
    first_matched_at TEXT,
    last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_tracks_isrc ON tracks(isrc);
CREATE INDEX IF NOT EXISTS idx_tracks_tidal_id ON tracks(tidal_track_id);

CREATE TABLE IF NOT EXISTS playlist_tracks (
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

CREATE TABLE IF NOT EXISTS match_candidates (
    spotify_track_id TEXT PRIMARY KEY,
    tidal_track_id TEXT,
    tidal_title TEXT,
    tidal_artist TEXT,
    tidal_duration_ms INTEGER,
    score REAL NOT NULL,
    title_score REAL,
    artist_score REAL,
    duration_score REAL,
    status TEXT NOT NULL CHECK (status IN ('review', 'not_found')),
    searched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (spotify_track_id) REFERENCES tracks(spotify_track_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS sync_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TEXT,
    dry_run INTEGER NOT NULL DEFAULT 0 CHECK (dry_run IN (0, 1)),
    playlists_checked INTEGER NOT NULL DEFAULT 0,
    tracks_added INTEGER NOT NULL DEFAULT 0,
    tracks_removed INTEGER NOT NULL DEFAULT 0,
    tracks_needing_review INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'running',
    error_message TEXT
);
"""
