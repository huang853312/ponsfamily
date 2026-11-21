from __future__ import annotations
import sqlite3
from pathlib import Path
from datetime import datetime, timezone

class Database:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as c:
            c.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS signals(
              signal_id TEXT PRIMARY KEY, chain_id INTEGER NOT NULL,
              tx_hash TEXT NOT NULL, log_index INTEGER NOT NULL,
              rule_id TEXT NOT NULL, token_address TEXT NOT NULL,
              status TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS orders(
              order_id TEXT PRIMARY KEY, signal_id TEXT NOT NULL UNIQUE,
              chain_id INTEGER NOT NULL, amount_usd REAL NOT NULL,
              status TEXT NOT NULL, tx_hash TEXT,
              created_at TEXT NOT NULL, day_utc TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_orders_day_status ON orders(day_utc,status);
            ''')
    def connect(self):
        c=sqlite3.connect(self.path,timeout=30,isolation_level=None)
        c.row_factory=sqlite3.Row
        c.execute('PRAGMA busy_timeout=30000')
        return c
    def insert_signal_once(self, signal: dict) -> bool:
        with self.connect() as c:
            try:
                c.execute('BEGIN IMMEDIATE')
                c.execute('INSERT INTO signals VALUES(?,?,?,?,?,?,?,?,?)',(
                    signal['signal_id'],signal['chain_id'],signal['tx_hash'],signal['log_index'],
                    signal['rule_id'],signal['token_address'],'received',signal['payload_json'],
                    datetime.now(timezone.utc).isoformat()))
                c.commit(); return True
            except sqlite3.IntegrityError:
                c.rollback(); return False
    def reserve_daily_budget(self, order_id: str, signal_id: str, chain_id: int, amount_usd: float, daily_limit: float) -> bool:
        day=datetime.now(timezone.utc).date().isoformat()
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            used=c.execute("SELECT COALESCE(SUM(amount_usd),0) total FROM orders WHERE day_utc=? AND status IN ('reserved','submitted','confirmed')",(day,)).fetchone()['total']
            if used+amount_usd > daily_limit+1e-9: c.rollback(); return False
            try:
                c.execute('INSERT INTO orders(order_id,signal_id,chain_id,amount_usd,status,created_at,day_utc) VALUES(?,?,?,?,?,?,?)',
                    (order_id,signal_id,chain_id,amount_usd,'reserved',datetime.now(timezone.utc).isoformat(),day))
                c.commit(); return True
            except sqlite3.IntegrityError:
                c.rollback(); return False
