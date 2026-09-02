# DJ Sync

DJ Sync is a local-first Python application for mirroring selected Spotify playlists to TIDAL for DJ preparation.

Spotify remains the source of truth: playlists explicitly selected for management can be created and updated on TIDAL, while track additions and removals are synchronized safely. The project is designed around a persistent mapping database so each unique track only needs to be matched to TIDAL once, even when it appears in several playlists.

## Project goals

- Select only the Spotify playlists that should be managed.
- Create corresponding TIDAL playlists automatically.
- Keep Spotify playlist additions and removals mirrored to TIDAL.
- Preserve global Spotify track -> TIDAL track mappings across playlists and sync runs.
- Never modify unmanaged TIDAL playlists.
- Support dry-run previews before making changes.
- Keep playlist deletion safe and explicit.
- Support roughly 20+ managed playlists without repeatedly matching duplicate tracks.

## Architecture

```text
Spotify API
    |
    v
Playlist snapshots ----> Sync diff engine
                              |
                              v
SQLite state <----> Global track matcher ----> TIDAL API
```

The SQLite database separates:

- `playlists`: Spotify <-> TIDAL playlist mappings and management state.
- `tracks`: global Spotify <-> TIDAL track mappings.
- `playlist_tracks`: membership of tracks in each managed playlist.
- `sync_runs`: sync history and result counts.

## Current milestone: 0.6 - TIDAL exact ISRC matching

Implemented:

- Python package and CLI scaffold.
- SQLite schema for playlists, tracks, playlist membership, and sync history.
- Pure diff logic for additions/removals.
- `--dry-run` CLI flag.
- Automated tests for the database schema and diff behavior.
- Secret-safe `.env.example` and `.gitignore`.
- Spotify OAuth 2.0 PKCE foundation and local callback flow.
- Current Spotify playlist listing with pagination and 2026 API fields.
- Interactive managed-playlist selection persisted in SQLite.
- TIDAL OAuth 2.1 Authorization Code + PKCE login flow.
- Secret-safe local storage for TIDAL access and refresh tokens.
- TIDAL JSON:API client for playlist create/read/delete operations.
- Safe `tidal-write-test` command that creates, verifies, and deletes a temporary playlist.
- Spotify managed-playlist ingestion with current 50-item API pagination.
- Normalized track metadata: Spotify ID/URI, ISRC, title, artists, album, duration, added date, and playlist position.
- Global track deduplication across playlists while preserving duplicate entries within a playlist.
- SQLite migration support for the new track metadata and position-based membership model.
- Exact TIDAL track lookup by ISRC using batches of up to 20 identifiers per API request.
- Persistent exact-match mappings so repeated playlist appearances never need to be matched again.
- ISRC miss tracking so the exact-match pass is idempotent and fallback matching can handle the remainder.
- `tidal-match-isrc` command with an optional `--limit` for safe staged testing.

Next:

1. Add metadata fallback matching with confidence scoring.
2. Add a manual review queue for ambiguous matches.
3. Create and populate managed TIDAL playlist mirrors.
4. Implement delta sync for additions, removals, renames, and playlist lifecycle.

## Local setup

```bash
python -m venv .venv
```

Activate the virtual environment, then install the project in editable mode:

```bash
pip install -e ".[dev]"
```

Initialize the local database:

```bash
dj-sync init-db
```

Connect Spotify after creating a Spotify Developer app and setting `SPOTIFY_CLIENT_ID` in `.env`:

```bash
dj-sync spotify-login
```

List playlists visible to DJ Sync:

```bash
dj-sync spotify-playlists
```

Connect TIDAL after creating a TIDAL Developer app and setting `TIDAL_CLIENT_ID` in `.env`:

```bash
dj-sync tidal-login
```

Verify TIDAL playlist read/write/delete access without touching managed playlists:

```bash
dj-sync tidal-write-test
```

Fetch and persist normalized tracks from all managed Spotify playlists:

```bash
dj-sync spotify-ingest
```

Match ingested Spotify tracks to TIDAL by exact ISRC. Use a small limit first when validating a real account:

```bash
dj-sync tidal-match-isrc --limit 40
```

Then process the remaining exact ISRC matches:

```bash
dj-sync tidal-match-isrc
```

Preview mode:

```bash
dj-sync sync --dry-run
```

Run tests:

```bash
pytest
```

## Security

OAuth tokens, `.env`, and the local SQLite database are intentionally excluded from Git. No Spotify or TIDAL credentials should ever be committed to the repository.
