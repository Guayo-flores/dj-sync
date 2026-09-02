from __future__ import annotations

import sqlite3
from pathlib import Path

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
