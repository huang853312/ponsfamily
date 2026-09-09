"""SQLite persistence and idempotency boundaries."""
from __future__ import annotations

import json
from pathlib import Path
import aiosqlite

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
CREATE TABLE IF NOT EXISTS processed_blocks (chain_id INTEGER PRIMARY KEY, block_number INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS contracts (address TEXT PRIMARY KEY, creator TEXT, deployer TEXT, bytecode_hash TEXT, label TEXT NOT NULL, first_block INTEGER, selectors TEXT DEFAULT '[]', event_topics TEXT DEFAULT '[]', implementation TEXT);
CREATE TABLE IF NOT EXISTS deployers (address TEXT PRIMARY KEY, first_seen INTEGER, contract_count INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS pools (address TEXT PRIMARY KEY, block_number INTEGER, timestamp INTEGER, tx_hash TEXT, dex_version TEXT, token0 TEXT, token1 TEXT, creator TEXT, factory TEXT, discovered_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS tokens (address TEXT PRIMARY KEY, name TEXT, symbol TEXT, creator TEXT, first_seen INTEGER);
CREATE TABLE IF NOT EXISTS factories (address TEXT PRIMARY KEY, dex_version TEXT, known INTEGER DEFAULT 0, first_seen INTEGER);
CREATE TABLE IF NOT EXISTS contract_clusters (id INTEGER PRIMARY KEY AUTOINCREMENT, cluster_key TEXT UNIQUE, deployer TEXT, first_seen TEXT DEFAULT CURRENT_TIMESTAMP, last_seen TEXT DEFAULT CURRENT_TIMESTAMP, level TEXT, alerted_level TEXT);
CREATE TABLE IF NOT EXISTS cluster_members (cluster_id INTEGER, address TEXT UNIQUE, role TEXT, FOREIGN KEY(cluster_id) REFERENCES contract_clusters(id));
CREATE TABLE IF NOT EXISTS alerts (dedupe_key TEXT PRIMARY KEY, cluster_id INTEGER, level TEXT, sent_at TEXT DEFAULT CURRENT_TIMESTAMP, telegram_message_id TEXT, payload TEXT);
CREATE TABLE IF NOT EXISTS known_system_matches (address TEXT, system_name TEXT, kind TEXT, matched_at TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(address, system_name));
CREATE TABLE IF NOT EXISTS bytecode_hashes (hash TEXT PRIMARY KEY, first_address TEXT, first_seen INTEGER, seen_count INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS observations (id INTEGER PRIMARY KEY AUTOINCREMENT, cluster_id INTEGER, kind TEXT, address TEXT, block_number INTEGER, detail TEXT, observed_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(kind,address,block_number));
CREATE INDEX IF NOT EXISTS idx_observations_cluster ON observations(cluster_id, observed_at);
"""


class Database:
    def __init__(self, path: Path): self.path, self.db = path, None
    async def open(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = await aiosqlite.connect(self.path)
        await self.db.executescript(SCHEMA); await self.db.commit()
        return self
    async def close(self):
        if self.db: await self.db.close()
    async def cursor(self) -> int | None:
        async with self.db.execute("SELECT block_number FROM processed_blocks WHERE chain_id=4663") as c:
            row = await c.fetchone(); return row[0] if row else None
    async def set_cursor(self, block: int):
        await self.db.execute("INSERT INTO processed_blocks VALUES(4663,?) ON CONFLICT(chain_id) DO UPDATE SET block_number=excluded.block_number", (block,)); await self.db.commit()
    async def pool_exists(self, address: str) -> bool:
        async with self.db.execute("SELECT 1 FROM pools WHERE address=?", (address,)) as c: return await c.fetchone() is not None
    async def save_pool(self, p: dict):
        await self.db.execute("INSERT OR IGNORE INTO pools(address,block_number,timestamp,tx_hash,dex_version,token0,token1,creator,factory) VALUES(?,?,?,?,?,?,?,?,?)", tuple(p[k] for k in ("address","block_number","timestamp","tx_hash","dex_version","token0","token1","creator","factory")))
        await self.db.execute("INSERT OR IGNORE INTO factories(address,dex_version,first_seen) VALUES(?,?,?)", (p["factory"],p["dex_version"],p["block_number"]))
        await self.db.commit()
    async def upsert_contract(self, address, creator, code_hash, label, block, selectors):
        await self.db.execute("INSERT OR IGNORE INTO contracts(address,creator,deployer,bytecode_hash,label,first_block,selectors) VALUES(?,?,?,?,?,?,?)", (address,creator,creator,code_hash,label,block,json.dumps(selectors)))
        cur=await self.db.execute("INSERT OR IGNORE INTO bytecode_hashes(hash,first_address,first_seen) VALUES(?,?,?)",(code_hash,address,block))
        await self.db.execute("UPDATE bytecode_hashes SET seen_count=seen_count+1 WHERE hash=? AND first_address<>?",(code_hash,address)); await self.db.commit()
        return cur.rowcount == 1
    async def cluster(self, deployer, addresses):
        key=deployer.lower(); await self.db.execute("INSERT OR IGNORE INTO contract_clusters(cluster_key,deployer,level) VALUES(?,?,'B')",(key,key))
        async with self.db.execute("SELECT id FROM contract_clusters WHERE cluster_key=?",(key,)) as c: cid=(await c.fetchone())[0]
        for addr, role in addresses: await self.db.execute("INSERT OR IGNORE INTO cluster_members(cluster_id,address,role) VALUES(?,?,?)",(cid,addr,role))
        await self.db.execute("UPDATE contract_clusters SET last_seen=CURRENT_TIMESTAMP WHERE id=?",(cid,)); await self.db.commit(); return cid
    async def reserve_alert(self, key, cid, level, payload):
        cur=await self.db.execute("INSERT OR IGNORE INTO alerts(dedupe_key,cluster_id,level,payload) VALUES(?,?,?,?)",(key,cid,level,payload)); await self.db.commit(); return cur.rowcount == 1
    async def release_alert(self, key):
        await self.db.execute("DELETE FROM alerts WHERE dedupe_key=? AND telegram_message_id IS NULL",(key,)); await self.db.commit()
    async def mark_alert_sent(self, key, message_id):
        await self.db.execute("UPDATE alerts SET telegram_message_id=? WHERE dedupe_key=?",(str(message_id),key)); await self.db.commit()
