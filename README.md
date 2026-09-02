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

## Current milestone: 0.1 - Foundation

Implemented:

- Python package and CLI scaffold.
- SQLite schema for playlists, tracks, playlist membership, and sync history.
- Pure diff logic for additions/removals.
- `--dry-run` CLI flag.
- Automated tests for the database schema and diff behavior.
- Secret-safe `.env.example` and `.gitignore`.

Next:

1. Spotify OAuth with PKCE.
2. Fetch current user's playlists.
3. Interactive selection of managed playlists.
4. Persist selected Spotify playlists in SQLite.
5. TIDAL OAuth and playlist creation.
6. Track matching: ISRC first, metadata fallback.

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
dj-sync --init-db
```

Preview mode:

```bash
dj-sync --dry-run
```

Run tests:

```bash
pytest
```

## Security

OAuth tokens, `.env`, and the local SQLite database are intentionally excluded from Git. No Spotify or TIDAL credentials should ever be committed to the repository.
