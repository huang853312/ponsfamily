import json
import sqlite3
from pathlib import Path
from typing import Any


class EventStore:
    def __init__(self, path: str = "data/monitor.db"):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=30000")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS chain_cursors (
            chain TEXT PRIMARY KEY,
            last_scanned_block INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS events (
            event_key TEXT PRIMARY KEY,
            chain TEXT NOT NULL,
            block_number INTEGER NOT NULL,
            tx_hash TEXT NOT NULL,
            log_index INTEGER NOT NULL,
            payload TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_events_pending ON events(status, created_at);
        """)
        self.db.commit()

    def get_cursor(self, chain: str):
        row = self.db.execute(
            "SELECT last_scanned_block FROM chain_cursors WHERE chain=?", (chain,)
        ).fetchone()
        return int(row["last_scanned_block"]) if row else None

    def set_cursor(self, chain: str, block_number: int):
        self.db.execute("""
            INSERT INTO chain_cursors(chain,last_scanned_block) VALUES(?,?)
            ON CONFLICT(chain) DO UPDATE SET last_scanned_block=excluded.last_scanned_block
        """, (chain, block_number))
        self.db.commit()

    def enqueue_logs(self, chain: str, logs: list[dict[str, Any]]):
        inserted = 0
        with self.db:
            for item in logs:
                tx_hash = str(item.get("transactionHash", "")).lower()
                log_index_hex = item.get("logIndex", "0x0")
                log_index = int(log_index_hex, 16) if isinstance(log_index_hex, str) else int(log_index_hex)
                block_hex = item.get("blockNumber", "0x0")
                block_number = int(block_hex, 16) if isinstance(block_hex, str) else int(block_hex)
                key = f"{chain}:{tx_hash}:{log_index}"
                cur = self.db.execute("""
                    INSERT OR IGNORE INTO events
                    (event_key,chain,block_number,tx_hash,log_index,payload)
                    VALUES(?,?,?,?,?,?)
                """, (key, chain, block_number, tx_hash, log_index, json.dumps(item)))
                inserted += cur.rowcount
        return inserted

    def pending_events(self, limit: int = 100):
        return self.db.execute(
            "SELECT event_key,chain,payload FROM events WHERE status='pending' ORDER BY created_at LIMIT ?",
            (limit,),
        ).fetchall()

    def mark_sent(self, event_key: str):
        with self.db:
            self.db.execute("UPDATE events SET status='sent' WHERE event_key=?", (event_key,))

    def close(self):
        self.db.close()
