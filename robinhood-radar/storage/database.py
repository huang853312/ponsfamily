"""SQLite WAL persistence, novelty ledger, clusters, and restart-safe windows."""
from __future__ import annotations
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import aiosqlite

SCHEMA="""
PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL; PRAGMA foreign_keys=ON; PRAGMA busy_timeout=5000;
CREATE TABLE IF NOT EXISTS processed_blocks (chain_id INTEGER PRIMARY KEY, block_number INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS monitor_cursors (chain_id INTEGER, monitor TEXT, block_number INTEGER NOT NULL, PRIMARY KEY(chain_id,monitor));
CREATE TABLE IF NOT EXISTS contracts (address TEXT PRIMARY KEY, creator TEXT, deployer TEXT, bytecode_hash TEXT, label TEXT NOT NULL, first_block INTEGER, selectors TEXT DEFAULT '[]', event_topics TEXT DEFAULT '[]', implementation TEXT);
CREATE TABLE IF NOT EXISTS deployers (address TEXT PRIMARY KEY, first_seen INTEGER, contract_count INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS pools (address TEXT PRIMARY KEY, block_number INTEGER, timestamp INTEGER, tx_hash TEXT, dex_version TEXT, token0 TEXT, token1 TEXT, creator TEXT, factory TEXT, discovered_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS tokens (address TEXT PRIMARY KEY, name TEXT, symbol TEXT, creator TEXT, first_seen INTEGER);
CREATE TABLE IF NOT EXISTS factories (address TEXT PRIMARY KEY, dex_version TEXT, known INTEGER DEFAULT 0, first_seen INTEGER);
CREATE TABLE IF NOT EXISTS contract_clusters (id INTEGER PRIMARY KEY AUTOINCREMENT, cluster_key TEXT UNIQUE, deployer TEXT, first_seen TEXT DEFAULT CURRENT_TIMESTAMP, last_seen TEXT DEFAULT CURRENT_TIMESTAMP, level TEXT, alerted_level TEXT);
CREATE TABLE IF NOT EXISTS cluster_members (cluster_id INTEGER, address TEXT, role TEXT, PRIMARY KEY(cluster_id,address), FOREIGN KEY(cluster_id) REFERENCES contract_clusters(id));
CREATE TABLE IF NOT EXISTS alerts (dedupe_key TEXT PRIMARY KEY, cluster_id INTEGER, level TEXT, sent_at TEXT DEFAULT CURRENT_TIMESTAMP, telegram_message_id TEXT, payload TEXT);
CREATE TABLE IF NOT EXISTS known_system_matches (address TEXT, system_name TEXT, kind TEXT, matched_at TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(address,system_name));
CREATE TABLE IF NOT EXISTS bytecode_hashes (hash TEXT PRIMARY KEY, first_address TEXT, first_seen INTEGER, seen_count INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS observations (id INTEGER PRIMARY KEY AUTOINCREMENT, cluster_id INTEGER, kind TEXT, address TEXT, block_number INTEGER, detail TEXT, observed_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(kind,address,block_number));
CREATE TABLE IF NOT EXISTS novelty_features (feature_type TEXT, fingerprint TEXT, first_address TEXT, first_block INTEGER, first_seen TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(feature_type,fingerprint));
CREATE TABLE IF NOT EXISTS cluster_relationships (cluster_id INTEGER, source TEXT, target TEXT, relation TEXT, tx_hash TEXT DEFAULT '', evidence TEXT, PRIMARY KEY(cluster_id,source,target,relation,tx_hash));
CREATE TABLE IF NOT EXISTS proxy_implementations (proxy TEXT PRIMARY KEY, implementation TEXT, kind TEXT, first_block INTEGER, first_seen TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS observation_windows (cluster_id INTEGER PRIMARY KEY, started_at TEXT, expires_at TEXT, initial_level TEXT, current_level TEXT, status TEXT DEFAULT 'active', final_summary TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS classification_evidence (address TEXT PRIMARY KEY, label TEXT, confidence REAL, evidence TEXT, classified_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS contract_discoveries (tx_hash TEXT, address TEXT, block_number INTEGER, creator TEXT, creation_type TEXT, trace_status TEXT, discovered_at TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(tx_hash,address));
CREATE TABLE IF NOT EXISTS contract_fingerprints (address TEXT PRIMARY KEY, runtime_hash TEXT, normalized_hash TEXT, selectors TEXT, event_topics TEXT, proxy_implementation TEXT, eip1167_implementation TEXT, code_size INTEGER, template_status TEXT, similarity REAL, evidence TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS deployer_events (id INTEGER PRIMARY KEY AUTOINCREMENT, deployer TEXT, contract_address TEXT, cluster_id INTEGER, classification TEXT, level TEXT, block_number INTEGER, tx_hash TEXT, event_time TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(tx_hash,contract_address));
CREATE TABLE IF NOT EXISTS deployer_relations (source TEXT, target TEXT, relation TEXT, evidence TEXT, first_seen TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(source,target,relation));
CREATE TABLE IF NOT EXISTS cluster_activity_snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, cluster_id INTEGER, timestamp TEXT, window_minutes INTEGER, native_value TEXT, token_flow_count INTEGER, unique_users INTEGER, tx_count INTEGER, verified_action_counts TEXT, unknown_call_count INTEGER, tvl_status TEXT DEFAULT 'UNKNOWN', capital_growth_5m TEXT, capital_growth_15m TEXT, capital_growth_30m TEXT, capital_growth_60m TEXT, usage_growth TEXT, UNIQUE(cluster_id,timestamp,window_minutes));
CREATE TABLE IF NOT EXISTS wallet_profiles (address TEXT PRIMARY KEY, status TEXT DEFAULT 'UNKNOWN', evidence TEXT DEFAULT '[]', updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS wallet_cluster_entries (wallet TEXT, cluster_id INTEGER, entry_type TEXT, entry_timestamp TEXT, tx_hash TEXT, PRIMARY KEY(wallet,cluster_id,entry_type));
CREATE TABLE IF NOT EXISTS wallet_performance (wallet TEXT, cluster_id INTEGER, outcome TEXT DEFAULT 'NO_DATA', evidence TEXT DEFAULT '[]', updated_at TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(wallet,cluster_id));
CREATE TABLE IF NOT EXISTS external_first_seen (cluster_id INTEGER, provider TEXT, status TEXT, first_seen TEXT, evidence TEXT, checked_at TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(cluster_id,provider));
CREATE TABLE IF NOT EXISTS opportunity_scores (id INTEGER PRIMARY KEY AUTOINCREMENT, cluster_id INTEGER, calculated_at TEXT DEFAULT CURRENT_TIMESTAMP, structural_level TEXT, total INTEGER, available_max INTEGER, confidence TEXT, components TEXT, risk_flags TEXT, evidence TEXT);
CREATE TABLE IF NOT EXISTS activity_trackers (cluster_id INTEGER PRIMARY KEY, started_at TEXT, start_block INTEGER, last_block INTEGER, status TEXT DEFAULT 'active', updated_at TEXT);
CREATE TABLE IF NOT EXISTS activity_events (cluster_id INTEGER, tx_hash TEXT, wallet TEXT, block_number INTEGER, native_value TEXT, selector TEXT, token_flow_count INTEGER DEFAULT 0, PRIMARY KEY(cluster_id,tx_hash));
CREATE INDEX IF NOT EXISTS idx_observations_cluster ON observations(cluster_id,observed_at);
CREATE INDEX IF NOT EXISTS idx_relationships_cluster ON cluster_relationships(cluster_id);
"""

class Database:
    def __init__(self,path:Path): self.path,self.db=path,None
    async def open(self):
        self.path.parent.mkdir(parents=True,exist_ok=True); self.db=await aiosqlite.connect(self.path)
        self.db.row_factory=aiosqlite.Row; await self.db.executescript(SCHEMA); await self._migrate_cluster_members(); await self._ensure_activity_columns(); await self.db.commit(); return self
    async def _migrate_cluster_members(self):
        """Remove the V0 global address uniqueness (shared infra belongs to many clusters)."""
        row=await (await self.db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='cluster_members'")).fetchone()
        if row and "address TEXT UNIQUE" in row[0]:
            await self.db.executescript("""
            ALTER TABLE cluster_members RENAME TO cluster_members_v0;
            CREATE TABLE cluster_members (cluster_id INTEGER, address TEXT, role TEXT, PRIMARY KEY(cluster_id,address), FOREIGN KEY(cluster_id) REFERENCES contract_clusters(id));
            INSERT OR IGNORE INTO cluster_members SELECT cluster_id,address,role FROM cluster_members_v0;
            DROP TABLE cluster_members_v0;
            """)
    async def _ensure_activity_columns(self):
        existing={row[1] for row in await (await self.db.execute("PRAGMA table_info(cluster_activity_snapshots)")).fetchall()}
        for name in ("capital_growth_5m","capital_growth_15m","capital_growth_30m","capital_growth_60m","usage_growth"):
            if name not in existing:await self.db.execute(f"ALTER TABLE cluster_activity_snapshots ADD COLUMN {name} TEXT")
    async def close(self):
        if self.db: await self.db.close()
    async def cursor(self):
        return await self.monitor_cursor("pools")
    async def set_cursor(self,block):
        await self.set_monitor_cursor("pools",block)
        await self.db.execute("INSERT INTO processed_blocks VALUES(4663,?) ON CONFLICT(chain_id) DO UPDATE SET block_number=excluded.block_number",(block,)); await self.db.commit()
    async def monitor_cursor(self,monitor):
        row=await (await self.db.execute("SELECT block_number FROM monitor_cursors WHERE chain_id=4663 AND monitor=?",(monitor,))).fetchone()
        if row:return row[0]
        if monitor=="pools":
            legacy=await (await self.db.execute("SELECT block_number FROM processed_blocks WHERE chain_id=4663")).fetchone()
            return legacy[0] if legacy else None
        return None
    async def set_monitor_cursor(self,monitor,block):
        await self.db.execute("INSERT INTO monitor_cursors VALUES(4663,?,?) ON CONFLICT(chain_id,monitor) DO UPDATE SET block_number=excluded.block_number",(monitor,block)); await self.db.commit()
    async def contract_discovery_exists(self,tx_hash,address):
        return await (await self.db.execute("SELECT 1 FROM contract_discoveries WHERE tx_hash=? AND address=?",(tx_hash,address))).fetchone() is not None
    async def save_contract_discovery(self,event):
        await self.db.execute("INSERT OR IGNORE INTO contract_discoveries(tx_hash,address,block_number,creator,creation_type,trace_status) VALUES(?,?,?,?,?,?)",
            tuple(event[k] for k in ("tx_hash","address","block_number","creator","creation_type","trace_status"))); await self.db.commit()
    async def pool_exists(self,address): return await (await self.db.execute("SELECT 1 FROM pools WHERE address=?",(address,))).fetchone() is not None
    async def save_pool(self,p):
        await self.db.execute("INSERT OR IGNORE INTO pools(address,block_number,timestamp,tx_hash,dex_version,token0,token1,creator,factory) VALUES(?,?,?,?,?,?,?,?,?)",tuple(p[k] for k in ("address","block_number","timestamp","tx_hash","dex_version","token0","token1","creator","factory")))
        await self.db.execute("INSERT OR IGNORE INTO factories(address,dex_version,first_seen) VALUES(?,?,?)",(p["factory"],p["dex_version"],p["block_number"])); await self.db.commit()
    async def release_pool(self,address):
        await self.db.execute("DELETE FROM pools WHERE address=?",(address,));await self.db.commit()
    async def feature(self,kind,fingerprint,address,block):
        cur=await self.db.execute("INSERT OR IGNORE INTO novelty_features(feature_type,fingerprint,first_address,first_block) VALUES(?,?,?,?)",(kind,fingerprint,address,block)); return cur.rowcount==1
    async def save_contract(self,address,creator,info,result,block,topics=()):
        await self.db.execute("INSERT OR IGNORE INTO contracts(address,creator,deployer,bytecode_hash,label,first_block,selectors,event_topics,implementation) VALUES(?,?,?,?,?,?,?,?,?)",(address,creator,creator,info["code_hash"],result.label,block,json.dumps(info["selectors"]),json.dumps(sorted(topics)),info["implementation"]))
        await self.db.execute("INSERT INTO classification_evidence(address,label,confidence,evidence) VALUES(?,?,?,?) ON CONFLICT(address) DO UPDATE SET label=excluded.label,confidence=excluded.confidence,evidence=excluded.evidence",(address,result.label,result.confidence,json.dumps(result.evidence)))
        features={
          "bytecode_hash":await self.feature("bytecode_hash",info["code_hash"],address,block),
          "template":await self.feature("template",info["code_hash"],address,block),
          "selector_set":await self.feature("selector_set","|".join(info["selectors"]),address,block),
          "event_topic_set":await self.feature("event_topic_set","|".join(sorted(topics)),address,block) if topics else False,
        }
        await self.db.execute("INSERT OR IGNORE INTO bytecode_hashes(hash,first_address,first_seen) VALUES(?,?,?)",(info["code_hash"],address,block))
        if info["implementation"]:
            await self.db.execute("INSERT OR IGNORE INTO proxy_implementations VALUES(?,?,?,?,CURRENT_TIMESTAMP)",(address,info["implementation"],"EIP-1167" if info["is_minimal_proxy"] else "EIP-1967",block))
            features["implementation"]=await self.feature("implementation",info["implementation"],address,block)
        await self.db.commit(); return features
    async def known_fingerprints(self,limit=500):
        rows=await (await self.db.execute("SELECT normalized_hash,selectors FROM contract_fingerprints ORDER BY created_at DESC LIMIT ?",(limit,))).fetchall()
        return [{"normalized_hash":r[0],"selectors":json.loads(r[1])} for r in rows]
    async def save_fingerprint(self,address,value):
        await self.db.execute("""INSERT OR IGNORE INTO contract_fingerprints(address,runtime_hash,normalized_hash,selectors,event_topics,proxy_implementation,eip1167_implementation,code_size,template_status,similarity,evidence)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)""",(address,value.runtime_hash,value.normalized_hash,json.dumps(value.selectors),json.dumps(value.event_topics),value.implementation,value.eip1167_implementation,value.code_size,value.template_status,value.similarity,json.dumps(value.evidence)));await self.db.commit()
    async def save_deployer_event(self,deployer,address,cid,label,level,block,tx_hash):
        await self.db.execute("INSERT OR IGNORE INTO deployer_events(deployer,contract_address,cluster_id,classification,level,block_number,tx_hash) VALUES(?,?,?,?,?,?,?)",(deployer,address,cid,label,level,block,tx_hash));await self.db.commit()
    async def deployer_stats(self,deployer):
        row=await (await self.db.execute("""SELECT COUNT(*),SUM(classification='ERC20'),SUM(level='SILENT'),SUM(level IN ('A','S')) FROM deployer_events WHERE deployer=?""",(deployer,))).fetchone()
        clone=await (await self.db.execute("""SELECT MAX(n) FROM (SELECT COUNT(*) n FROM deployer_events e JOIN contract_fingerprints f ON f.address=e.contract_address WHERE e.deployer=? GROUP BY f.normalized_hash)""",(deployer,))).fetchone()
        return {"contracts_created":row[0] or 0,"erc20_count":row[1] or 0,"silent_count":row[2] or 0,"high_grade_clusters":row[3] or 0,"max_same_template":clone[0] or 0}
    async def save_activity_snapshot(self,cid,timestamp,minutes,snapshot):
        await self.db.execute("""INSERT OR REPLACE INTO cluster_activity_snapshots(cluster_id,timestamp,window_minutes,native_value,token_flow_count,unique_users,tx_count,verified_action_counts,unknown_call_count,tvl_status) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (cid,timestamp,minutes,str(snapshot["native_value"]),snapshot["token_flow_count"],snapshot["unique_users"],snapshot["tx_count"],json.dumps(snapshot["verified_action_counts"]),snapshot["unknown_call_count"],snapshot["tvl"]));await self.db.commit()
    async def activity_candidates(self,limit):
        rows=await (await self.db.execute("""SELECT c.id,c.level FROM contract_clusters c
            JOIN observation_windows w ON w.cluster_id=c.id LEFT JOIN activity_trackers t ON t.cluster_id=c.id
            WHERE t.status='active' OR (t.cluster_id IS NULL AND w.status='active' AND
              (c.level IN ('A','S') OR EXISTS (SELECT 1 FROM cluster_members m WHERE m.cluster_id=c.id AND m.role IN ('Factory','Router','Vault','Registry','Hook','Proxy','Implementation'))))
            ORDER BY CASE WHEN t.status='active' THEN 0 ELSE 1 END,CASE c.level WHEN 'S' THEN 0 WHEN 'A' THEN 1 ELSE 2 END,w.started_at LIMIT ?""",(limit,))).fetchall()
        return [dict(row) for row in rows]
    async def activity_addresses(self,cid):
        rows=await (await self.db.execute("SELECT address FROM cluster_members WHERE cluster_id=? AND length(address)=42",(cid,))).fetchall();return {r[0].lower() for r in rows}
    async def activity_tracker(self,cid,head):
        row=await (await self.db.execute("SELECT * FROM activity_trackers WHERE cluster_id=?",(cid,))).fetchone()
        if not row:
            now=datetime.now(timezone.utc).isoformat();await self.db.execute("INSERT INTO activity_trackers(cluster_id,started_at,start_block,last_block,updated_at) VALUES(?,?,?,?,?)",(cid,now,head,head,now));await self.db.commit()
            row=await (await self.db.execute("SELECT * FROM activity_trackers WHERE cluster_id=?",(cid,))).fetchone()
        return dict(row)
    async def save_activity_event(self,cid,tx_hash,wallet,block,native_value,selector,token_flows):
        await self.db.execute("INSERT OR IGNORE INTO activity_events VALUES(?,?,?,?,?,?,?)",(cid,tx_hash,wallet,block,str(native_value),selector,token_flows))
    async def advance_activity_tracker(self,cid,block):
        await self.db.execute("UPDATE activity_trackers SET last_block=?,updated_at=? WHERE cluster_id=?",(block,datetime.now(timezone.utc).isoformat(),cid));await self.db.commit()
    async def activity_aggregate(self,cid):
        rows=await (await self.db.execute("SELECT tx_hash,wallet,native_value,selector,token_flow_count FROM activity_events WHERE cluster_id=?",(cid,))).fetchall();return rows
    async def activity_windows_saved(self,cid):
        rows=await (await self.db.execute("SELECT window_minutes FROM cluster_activity_snapshots WHERE cluster_id=?",(cid,))).fetchall();return {r[0] for r in rows}
    async def activity_snapshots(self,cid):
        rows=await (await self.db.execute("SELECT * FROM cluster_activity_snapshots WHERE cluster_id=? ORDER BY window_minutes",(cid,))).fetchall();return [dict(r) for r in rows]
    async def complete_activity_tracker(self,cid):
        await self.db.execute("UPDATE activity_trackers SET status='completed',updated_at=? WHERE cluster_id=?",(datetime.now(timezone.utc).isoformat(),cid));await self.db.commit()
    async def wallet_profile(self,address):
        await self.db.execute("INSERT OR IGNORE INTO wallet_profiles(address) VALUES(?)",(address,));await self.db.commit()
        return dict(await (await self.db.execute("SELECT * FROM wallet_profiles WHERE address=?",(address,))).fetchone())
    async def save_opportunity_score(self,cid,level,score):
        await self.db.execute("INSERT INTO opportunity_scores(cluster_id,structural_level,total,available_max,confidence,components,risk_flags,evidence) VALUES(?,?,?,?,?,?,?,?)",
            (cid,level,score.total,score.available_max,score.confidence,json.dumps(score.components),json.dumps(score.risk_flags),json.dumps(score.evidence)));await self.db.commit()
    async def latest_opportunity_score(self,cid):
        row=await (await self.db.execute("SELECT * FROM opportunity_scores WHERE cluster_id=? ORDER BY id DESC LIMIT 1",(cid,))).fetchone()
        if not row:return None
        value=dict(row);value["components"]=json.loads(value["components"]);value["risk_flags"]=json.loads(value["risk_flags"]);value["evidence"]=json.loads(value["evidence"]);return value
    async def get_or_create_cluster(self,key,deployer,block,minutes):
        new_deployer=await self.feature("deployer",deployer,deployer,block)
        await self.db.execute("INSERT OR IGNORE INTO deployers(address,first_seen) VALUES(?,?)",(deployer,block))
        now=datetime.now(timezone.utc); expires=now+timedelta(minutes=minutes)
        row=await (await self.db.execute("""SELECT c.id FROM contract_clusters c JOIN observation_windows w ON w.cluster_id=c.id
            WHERE c.deployer=? AND w.status='active' AND w.expires_at>? ORDER BY w.started_at DESC LIMIT 1""",(deployer,now.isoformat()))).fetchone()
        if row: return row[0],new_deployer
        key=f"{key}:from:{block}"
        await self.db.execute("INSERT OR IGNORE INTO contract_clusters(cluster_key,deployer,level) VALUES(?,?,'B')",(key,deployer))
        row=await (await self.db.execute("SELECT id FROM contract_clusters WHERE cluster_key=?",(key,))).fetchone(); cid=row[0]
        await self.db.execute("INSERT OR IGNORE INTO observation_windows(cluster_id,started_at,expires_at,initial_level,current_level,updated_at) VALUES(?,?,?,'B','B',?)",(cid,now.isoformat(),expires.isoformat(),now.isoformat()))
        await self.db.commit(); return cid,new_deployer
    async def add_member(self,cid,address,role,block):
        cur=await self.db.execute("INSERT OR IGNORE INTO cluster_members(cluster_id,address,role) VALUES(?,?,?)",(cid,address,role))
        new_role=await self.feature("first_seen_"+role.lower(),address,address,block) if role in {"Factory","Router","Hook","Vault","Oracle","Implementation","Proxy","Registry","Lending","AMM","Aggregator","Settlement"} else False
        await self.db.commit(); return cur.rowcount==1,new_role
    async def relationship(self,cid,source,target,relation,tx_hash,evidence):
        await self.db.execute("INSERT OR IGNORE INTO cluster_relationships VALUES(?,?,?,?,?,?)",(cid,source,target,relation,tx_hash or "",evidence))
    async def observation(self,cid,kind,address,block,detail):
        await self.db.execute("INSERT OR IGNORE INTO observations(cluster_id,kind,address,block_number,detail) VALUES(?,?,?,?,?)",(cid,kind,address,block,json.dumps(detail,ensure_ascii=False)))
    async def known_match(self,address,match):
        await self.db.execute("INSERT OR IGNORE INTO known_system_matches(address,system_name,kind) VALUES(?,?,?)",(address,match.system,match.role))
    async def cluster_snapshot(self,cid):
        members=await (await self.db.execute("SELECT address,role FROM cluster_members WHERE cluster_id=?",(cid,))).fetchall()
        relations=await (await self.db.execute("SELECT source,target,relation,evidence FROM cluster_relationships WHERE cluster_id=?",(cid,))).fetchall()
        window=await (await self.db.execute("SELECT * FROM observation_windows WHERE cluster_id=?",(cid,))).fetchone()
        return {"members":[dict(x) for x in members],"relationships":[dict(x) for x in relations],"window":dict(window)}
    async def set_level(self,cid,level):
        row=await (await self.db.execute("SELECT current_level FROM observation_windows WHERE cluster_id=?",(cid,))).fetchone(); old=row[0]
        now=datetime.now(timezone.utc).isoformat(); await self.db.execute("UPDATE observation_windows SET current_level=?,updated_at=? WHERE cluster_id=?",(level,now,cid)); await self.db.execute("UPDATE contract_clusters SET level=?,last_seen=? WHERE id=?",(level,now,cid)); await self.db.commit(); return old
    async def expire_windows(self):
        now=datetime.now(timezone.utc).isoformat(); rows=await (await self.db.execute("SELECT cluster_id FROM observation_windows WHERE status='active' AND expires_at<=?",(now,))).fetchall()
        for row in rows:
            snapshot=await self.cluster_snapshot(row[0]); summary=json.dumps(snapshot,ensure_ascii=False)
            await self.db.execute("UPDATE observation_windows SET status='completed',final_summary=?,updated_at=? WHERE cluster_id=?",(summary,now,row[0]))
        await self.db.commit(); return len(rows)
    async def active_windows(self): return await (await self.db.execute("SELECT * FROM observation_windows WHERE status='active'")).fetchall()
    async def reserve_alert(self,key,cid,level,payload):
        cur=await self.db.execute("INSERT OR IGNORE INTO alerts(dedupe_key,cluster_id,level,payload) VALUES(?,?,?,?)",(key,cid,level,payload)); await self.db.commit(); return cur.rowcount==1
    async def release_alert(self,key): await self.db.execute("DELETE FROM alerts WHERE dedupe_key=? AND telegram_message_id IS NULL",(key,)); await self.db.commit()
    async def mark_alert_sent(self,key,message_id): await self.db.execute("UPDATE alerts SET telegram_message_id=? WHERE dedupe_key=?",(str(message_id),key)); await self.db.commit()
