from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True, slots=True)
class Settings:
    database_path: str = "data/dj_sync.db"
    spotify_client_id: str | None = None
    spotify_redirect_uri: str = "http://127.0.0.1:8888/callback"
    spotify_token_path: str = "data/spotify.token.json"

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        return cls(
            database_path=os.getenv("DJ_SYNC_DATABASE", "data/dj_sync.db"),
            spotify_client_id=os.getenv("SPOTIFY_CLIENT_ID"),
            spotify_redirect_uri=os.getenv(
                "SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback"
            ),
            spotify_token_path=os.getenv(
                "SPOTIFY_TOKEN_PATH", "data/spotify.token.json"
            ),
        )
