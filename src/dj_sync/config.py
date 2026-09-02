from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Settings:
    database_path: str = "data/dj_sync.db"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(database_path=os.getenv("DJ_SYNC_DATABASE", "data/dj_sync.db"))
