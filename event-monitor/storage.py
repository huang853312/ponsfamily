import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class EventStore:
    """SQLite-backed event queue. Events remain pending until Telegram confirms delivery."""

    def __init__(self, database_path: Path):
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=20)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self):
        with self._connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    sent_at TEXT
                )
                """
            )

    def enqueue(self, event_id: str, payload: str) -> None:
        with self._connection() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO events(event_id, payload) VALUES (?, ?)",
                (event_id, payload),
            )

    def pending(self, limit: int = 100) -> list[tuple[str, str]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT event_id, payload FROM events "
                "WHERE status = 'pending' ORDER BY created_at LIMIT ?",
                (limit,),
            ).fetchall()
        return rows

    def mark_attempt(self, event_id: str) -> None:
        with self._connection() as connection:
            connection.execute(
                "UPDATE events SET attempts = attempts + 1 WHERE event_id = ?",
                (event_id,),
            )

    def mark_sent(self, event_id: str) -> None:
        with self._connection() as connection:
            connection.execute(
                "UPDATE events SET status = 'sent', sent_at = CURRENT_TIMESTAMP "
                "WHERE event_id = ?",
                (event_id,),
            )
