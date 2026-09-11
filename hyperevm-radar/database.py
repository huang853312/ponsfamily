import sqlite3
import time
from pathlib import Path

DB_PATH = Path("data/radar.db")

def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS contracts (
            address TEXT PRIMARY KEY,
            creator TEXT,
            block_number INTEGER,
            level TEXT,
            code_hash TEXT,
            template_key TEXT,
            implementation TEXT,
            token_symbol TEXT,
            token_name TEXT,
            first_seen INTEGER,
            updated_at INTEGER
        )
        """)
        conn.commit()

def seen(address: str) -> bool:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT 1 FROM contracts WHERE address=? LIMIT 1",
            (address.lower(),)
        ).fetchone()
        return row is not None

def save_contract(
    address,
    creator,
    block_number,
    level,
    code_hash,
    template_key="",
    implementation="",
    token_symbol="",
    token_name=""
):
    now = int(time.time())
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
        INSERT OR REPLACE INTO contracts (
            address, creator, block_number, level,
            code_hash, template_key, implementation,
            token_symbol, token_name,
            first_seen, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?,
            COALESCE(
                (SELECT first_seen FROM contracts WHERE address=?),
                ?
            ),
            ?
        )
        """, (
            address.lower(),
            creator.lower() if creator else "",
            block_number,
            level,
            code_hash,
            template_key,
            implementation,
            token_symbol,
            token_name,
            address.lower(),
            now,
            now
        ))
        conn.commit()

def init_cluster_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS cluster_members (
            creator TEXT,
            address TEXT,
            role TEXT,
            first_seen INTEGER,
            PRIMARY KEY (creator, address)
        )
        """)
        conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_cluster_creator
        ON cluster_members(creator)
        """)
        conn.commit()

def add_cluster_member(creator, address, role):
    now = int(time.time())
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
        INSERT OR IGNORE INTO cluster_members
        (creator, address, role, first_seen)
        VALUES (?, ?, ?, ?)
        """, (creator.lower(), address.lower(), role, now))
        conn.commit()

def recent_cluster(creator, seconds=1800):
    cutoff = int(time.time()) - seconds
    with sqlite3.connect(DB_PATH) as conn:
        return conn.execute("""
        SELECT address, role, first_seen
        FROM cluster_members
        WHERE creator=? AND first_seen>=?
        ORDER BY first_seen ASC
        """, (creator.lower(), cutoff)).fetchall()

def init_relation_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS contract_relations (
            parent_address TEXT,
            child_address TEXT,
            relation_type TEXT,
            tx_hash TEXT,
            block_number INTEGER,
            first_seen INTEGER,
            PRIMARY KEY (parent_address, child_address, relation_type)
        )
        """)
        conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_relation_parent
        ON contract_relations(parent_address)
        """)
        conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_relation_child
        ON contract_relations(child_address)
        """)
        conn.commit()

def save_relation(parent, child, relation_type, tx_hash="", block_number=0):
    now = int(time.time())
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
        INSERT OR IGNORE INTO contract_relations
        (parent_address, child_address, relation_type, tx_hash, block_number, first_seen)
        VALUES (?, ?, ?, ?, ?, ?)
        """, (
            parent.lower() if parent else "",
            child.lower() if child else "",
            relation_type,
            tx_hash,
            block_number,
            now
        ))
        conn.commit()

def get_relations_for_child(child):
    with sqlite3.connect(DB_PATH) as conn:
        return conn.execute("""
        SELECT parent_address, child_address, relation_type, tx_hash, block_number, first_seen
        FROM contract_relations
        WHERE child_address=?
        ORDER BY first_seen DESC
        """, (child.lower(),)).fetchall()

def get_relations_for_parent(parent):
    with sqlite3.connect(DB_PATH) as conn:
        return conn.execute("""
        SELECT parent_address, child_address, relation_type, tx_hash, block_number, first_seen
        FROM contract_relations
        WHERE parent_address=?
        ORDER BY first_seen DESC
        """, (parent.lower(),)).fetchall()

def save_token_creator(token_address, creator, block_number, tx_hash):
    now = int(time.time())
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
        INSERT OR REPLACE INTO token_creators (
            token_address, creator, block_number, tx_hash, first_seen
        )
        VALUES (?, ?, ?, ?, COALESCE(
            (SELECT first_seen FROM token_creators WHERE token_address=?),
            ?
        ))
        """, (
            token_address.lower(),
            creator.lower() if creator else "",
            block_number,
            tx_hash,
            token_address.lower(),
            now
        ))
        conn.commit()


# =========================================================
# Platform -> Token Radar (independent module)
# =========================================================

def init_platform_brand_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS platform_brands (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform_name TEXT,
            normalized_brand TEXT NOT NULL,
            domain TEXT NOT NULL,
            website_url TEXT,
            platform_type TEXT,
            platform_address TEXT,
            first_seen_block INTEGER DEFAULT 0,
            first_seen_time INTEGER,
            confidence TEXT DEFAULT 'CANDIDATE',
            confirmed INTEGER DEFAULT 0,
            source TEXT,
            created_at INTEGER,
            UNIQUE(normalized_brand, domain)
        )
        """)

        columns = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(platform_brands)"
            ).fetchall()
        }

        if "official_token_name" not in columns:
            conn.execute(
                "ALTER TABLE platform_brands "
                "ADD COLUMN official_token_name TEXT DEFAULT ''"
            )

        if "official_token_symbol" not in columns:
            conn.execute(
                "ALTER TABLE platform_brands "
                "ADD COLUMN official_token_symbol TEXT DEFAULT ''"
            )

        conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_platform_brand_normalized
        ON platform_brands(normalized_brand)
        """)

        conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_platform_brand_block
        ON platform_brands(first_seen_block)
        """)

        conn.commit()


def save_platform_brand(
    platform_name,
    normalized_brand,
    domain,
    website_url="",
    platform_type="",
    platform_address="",
    first_seen_block=0,
    official_token_name="",
    official_token_symbol="",
    confidence="CANDIDATE",
    confirmed=False,
    source=""
):
    if not normalized_brand or not domain:
        return False

    now = int(time.time())

    normalized_brand = normalized_brand.lower()
    domain = domain.lower()

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
        INSERT INTO platform_brands (
            platform_name,
            normalized_brand,
            domain,
            website_url,
            platform_type,
            platform_address,
            first_seen_block,
            first_seen_time,
            official_token_name,
            official_token_symbol,
            confidence,
            confirmed,
            source,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)

        ON CONFLICT(normalized_brand, domain)
        DO UPDATE SET

            platform_name =
                CASE
                    WHEN excluded.platform_name != ''
                    THEN excluded.platform_name
                    ELSE platform_brands.platform_name
                END,

            website_url =
                CASE
                    WHEN excluded.website_url != ''
                    THEN excluded.website_url
                    ELSE platform_brands.website_url
                END,

            platform_type =
                CASE
                    WHEN excluded.platform_type != ''
                    THEN excluded.platform_type
                    ELSE platform_brands.platform_type
                END,

            platform_address =
                CASE
                    WHEN excluded.platform_address != ''
                    THEN excluded.platform_address
                    ELSE platform_brands.platform_address
                END,

            first_seen_block =
                CASE
                    WHEN platform_brands.first_seen_block <= 0
                         AND excluded.first_seen_block > 0
                    THEN excluded.first_seen_block

                    WHEN excluded.first_seen_block > 0
                         AND platform_brands.first_seen_block > 0
                    THEN MIN(
                        platform_brands.first_seen_block,
                        excluded.first_seen_block
                    )

                    ELSE platform_brands.first_seen_block
                END,

            official_token_name =
                CASE
                    WHEN excluded.official_token_name != ''
                    THEN excluded.official_token_name
                    ELSE platform_brands.official_token_name
                END,

            official_token_symbol =
                CASE
                    WHEN excluded.official_token_symbol != ''
                    THEN excluded.official_token_symbol
                    ELSE platform_brands.official_token_symbol
                END,

            confidence =
                CASE
                    WHEN excluded.confirmed = 1
                    THEN excluded.confidence
                    ELSE platform_brands.confidence
                END,

            confirmed =
                MAX(
                    platform_brands.confirmed,
                    excluded.confirmed
                ),

            source =
                CASE
                    WHEN excluded.source != ''
                    THEN excluded.source
                    ELSE platform_brands.source
                END
        """, (
            platform_name or "",
            normalized_brand,
            domain,
            website_url or "",
            platform_type or "",
            platform_address.lower() if platform_address else "",
            int(first_seen_block or 0),
            now,
            official_token_name or "",
            official_token_symbol or "",
            confidence or "CANDIDATE",
            1 if confirmed else 0,
            source or "",
            now
        ))

        conn.commit()

        row = conn.execute("""
            SELECT id, confirmed
            FROM platform_brands
            WHERE normalized_brand=?
              AND domain=?
        """, (
            normalized_brand,
            domain
        )).fetchone()

        return bool(row)

def get_platform_brand_matches(normalized_brand):
    if not normalized_brand:
        return []

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row

        rows = conn.execute("""
        SELECT
            id,
            platform_name,
            normalized_brand,
            domain,
            website_url,
            platform_type,
            platform_address,
            first_seen_block,
            first_seen_time,
            confidence,
            confirmed,
            source,
            created_at
        FROM platform_brands
        WHERE normalized_brand=?
        ORDER BY confirmed DESC, first_seen_time ASC
        """, (normalized_brand.lower(),)).fetchall()

        return [dict(row) for row in rows]


def init_platform_token_match_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS platform_token_matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform_id INTEGER NOT NULL,
            token_address TEXT NOT NULL,
            token_name TEXT,
            token_symbol TEXT,
            deployer TEXT,
            match_type TEXT NOT NULL,
            block_number INTEGER,
            tx_hash TEXT,
            first_seen INTEGER,
            UNIQUE(platform_id, token_address, match_type)
        )
        """)

        conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_pt_match_token
        ON platform_token_matches(token_address)
        """)

        conn.commit()


def find_confirmed_platform_matches(
    token_name,
    token_symbol,
    token_block,
    normalize_func
):
    name_norm = normalize_func(token_name)
    symbol_norm = normalize_func(token_symbol)
    token_block = int(token_block or 0)

    if token_block <= 0:
        return []

    if not name_norm and not symbol_norm:
        return []

    results_by_platform = {}

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row

        rows = conn.execute("""
            SELECT
                id,
                platform_name,
                normalized_brand,
                domain,
                website_url,
                platform_type,
                platform_address,
                first_seen_block,
                first_seen_time,
                confidence,
                confirmed,
                source,
                official_token_name,
                official_token_symbol
            FROM platform_brands
            WHERE confirmed=1
        """).fetchall()

        for row in rows:
            item = dict(row)

            first_seen_block = int(
                item.get("first_seen_block") or 0
            )

            # Platform must exist before this new token.
            if first_seen_block <= 0:
                continue

            if first_seen_block >= token_block:
                continue

            brand_norm = normalize_func(
                item.get("normalized_brand") or ""
            )

            official_name_norm = normalize_func(
                item.get("official_token_name") or ""
            )

            official_symbol_norm = normalize_func(
                item.get("official_token_symbol") or ""
            )

            match_types = []

            # Generic same-brand detection
            if name_norm and brand_norm and name_norm == brand_norm:
                match_types.append("EXACT_NAME")

            if symbol_norm and brand_norm and symbol_norm == brand_norm:
                match_types.append("EXACT_SYMBOL")

            # Generic verified official-token mapping
            if (
                name_norm
                and official_name_norm
                and name_norm == official_name_norm
            ):
                match_types.append("OFFICIAL_TOKEN_NAME")

            if (
                symbol_norm
                and official_symbol_norm
                and symbol_norm == official_symbol_norm
            ):
                match_types.append("OFFICIAL_TOKEN_SYMBOL")

            if not match_types:
                continue

            item["match_type"] = "+".join(match_types)

            results_by_platform[item["id"]] = item

    return list(results_by_platform.values())

def save_platform_token_match(
    platform_id,
    token_address,
    token_name,
    token_symbol,
    deployer,
    match_type,
    block_number,
    tx_hash=""
):
    now = int(time.time())

    token_address = (token_address or "").lower()

    if not token_address:
        return False

    with sqlite3.connect(DB_PATH) as conn:

        # One platform + one token CA = one alert.
        existing = conn.execute("""
            SELECT id
            FROM platform_token_matches
            WHERE platform_id=?
              AND lower(token_address)=?
            LIMIT 1
        """, (
            int(platform_id),
            token_address
        )).fetchone()

        if existing:
            return False

        try:
            conn.execute("""
            INSERT INTO platform_token_matches (
                platform_id,
                token_address,
                token_name,
                token_symbol,
                deployer,
                match_type,
                block_number,
                tx_hash,
                first_seen
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                int(platform_id),
                token_address,
                token_name or "",
                token_symbol or "",
                deployer.lower() if deployer else "",
                match_type or "",
                int(block_number or 0),
                tx_hash or "",
                now
            ))

            conn.commit()
            return True

        except sqlite3.IntegrityError:
            return False

# =========================================================
# Platform Discovery Candidates
# Independent from address_book / Funding Radar.
# =========================================================

def init_platform_candidate_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS platform_candidates (
            address TEXT PRIMARY KEY,
            creator TEXT,
            block_number INTEGER,
            tx_hash TEXT,
            token_name TEXT,
            token_symbol TEXT,
            infra_roles TEXT,
            discovery_source TEXT,
            status TEXT DEFAULT 'NEW',
            website_url TEXT DEFAULT '',
            domain TEXT DEFAULT '',
            normalized_brand TEXT DEFAULT '',
            first_seen INTEGER,
            updated_at INTEGER
        )
        """)

        conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_platform_candidate_status
        ON platform_candidates(status)
        """)

        conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_platform_candidate_creator
        ON platform_candidates(creator)
        """)

        conn.commit()


def save_platform_candidate(
    address,
    creator="",
    block_number=0,
    tx_hash="",
    token_name="",
    token_symbol="",
    infra_roles=None,
    discovery_source="ONCHAIN"
):
    if not address:
        return False

    roles = sorted(set(infra_roles or []))
    roles_text = ",".join(roles)
    now = int(time.time())

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
        INSERT INTO platform_candidates (
            address,
            creator,
            block_number,
            tx_hash,
            token_name,
            token_symbol,
            infra_roles,
            discovery_source,
            status,
            first_seen,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'NEW', ?, ?)

        ON CONFLICT(address) DO UPDATE SET
            creator=excluded.creator,
            block_number=excluded.block_number,
            tx_hash=excluded.tx_hash,
            token_name=excluded.token_name,
            token_symbol=excluded.token_symbol,
            infra_roles=excluded.infra_roles,
            discovery_source=excluded.discovery_source,
            updated_at=excluded.updated_at
        """, (
            address.lower(),
            creator.lower() if creator else "",
            int(block_number or 0),
            tx_hash or "",
            token_name or "",
            token_symbol or "",
            roles_text,
            discovery_source,
            now,
            now
        ))

        conn.commit()
        return True


def list_new_platform_candidates(limit=100):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row

        rows = conn.execute("""
        SELECT *
        FROM platform_candidates
        WHERE status='NEW'
        ORDER BY block_number DESC
        LIMIT ?
        """, (int(limit),)).fetchall()

        return [dict(row) for row in rows]


# =========================================================
# Platform Families
#
# Candidate contracts are grouped into a project-family layer.
# This does NOT confirm project identity or website ownership.
# =========================================================

def init_platform_family_db():
    conn = sqlite3.connect(DB_PATH)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS platform_families (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            creator TEXT NOT NULL,
            first_block INTEGER NOT NULL DEFAULT 0,
            last_block INTEGER NOT NULL DEFAULT 0,
            member_count INTEGER NOT NULL DEFAULT 0,
            platform_types TEXT NOT NULL DEFAULT '',
            brand_hint TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'NEW',
            website_url TEXT NOT NULL DEFAULT '',
            domain TEXT NOT NULL DEFAULT '',
            confirmed_brand TEXT NOT NULL DEFAULT '',
            created_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            updated_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            UNIQUE(creator, first_block, last_block)
        )
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_platform_family_creator
        ON platform_families(creator)
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_platform_family_status
        ON platform_families(status)
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS platform_family_members (
            family_id INTEGER NOT NULL,
            candidate_address TEXT NOT NULL,
            block_number INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(family_id, candidate_address)
        )
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_platform_family_member_address
        ON platform_family_members(candidate_address)
    """)

    conn.commit()
    conn.close()


def init_platform_family_intelligence_db():
    """Migration-safe structured intelligence storage, separate from on-chain Family facts."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS platform_family_intelligence (
                family_id INTEGER PRIMARY KEY,
                project_name TEXT NOT NULL DEFAULT '',
                official_x TEXT NOT NULL DEFAULT '',
                official_website TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                infrastructure_types TEXT NOT NULL DEFAULT '[]',
                official_token_symbol TEXT NOT NULL DEFAULT '',
                official_token_ca TEXT NOT NULL DEFAULT '',
                token_status TEXT NOT NULL DEFAULT 'NONE',
                confidence INTEGER NOT NULL DEFAULT 0,
                verification_status TEXT NOT NULL DEFAULT 'NO_DATA',
                verification_method TEXT NOT NULL DEFAULT '',
                hostname TEXT NOT NULL DEFAULT '',
                root_domain TEXT NOT NULL DEFAULT '',
                subdomain TEXT NOT NULL DEFAULT '',
                docs TEXT NOT NULL DEFAULT '[]',
                github TEXT NOT NULL DEFAULT '[]',
                source_url TEXT NOT NULL DEFAULT '',
                core_word TEXT NOT NULL DEFAULT '',
                derived_words TEXT NOT NULL DEFAULT '[]',
                discovered_token_name TEXT NOT NULL DEFAULT '',
                discovered_token_symbol TEXT NOT NULL DEFAULT '',
                discovered_ca TEXT NOT NULL DEFAULT '',
                evidence_source TEXT NOT NULL DEFAULT '',
                token_verification_status TEXT NOT NULL DEFAULT 'NO_DATA',
                token_evidence TEXT NOT NULL DEFAULT '[]',
                token_source_urls TEXT NOT NULL DEFAULT '[]',
                evidence TEXT NOT NULL DEFAULT '[]',
                updated_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS platform_identity_investigations (
                subject_key TEXT PRIMARY KEY,
                family_id INTEGER,
                source_url TEXT NOT NULL DEFAULT '',
                verification_status TEXT NOT NULL DEFAULT 'NO_DATA',
                result TEXT NOT NULL DEFAULT '{}',
                updated_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS platform_identity_address_evidence (
                subject_key TEXT NOT NULL,
                family_id INTEGER,
                address TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT '',
                source_url TEXT NOT NULL DEFAULT '',
                evidence_source TEXT NOT NULL DEFAULT '',
                updated_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                PRIMARY KEY(subject_key, address, source_url)
            )
        """)
        existing = {row[1] for row in conn.execute("PRAGMA table_info(platform_family_intelligence)")}
        for name, definition in {
            "verification_method": "TEXT NOT NULL DEFAULT ''",
            "hostname": "TEXT NOT NULL DEFAULT ''",
            "root_domain": "TEXT NOT NULL DEFAULT ''",
            "subdomain": "TEXT NOT NULL DEFAULT ''",
            "docs": "TEXT NOT NULL DEFAULT '[]'",
            "github": "TEXT NOT NULL DEFAULT '[]'",
            "source_url": "TEXT NOT NULL DEFAULT ''",
            "core_word": "TEXT NOT NULL DEFAULT ''",
            "derived_words": "TEXT NOT NULL DEFAULT '[]'",
            "discovered_token_name": "TEXT NOT NULL DEFAULT ''",
            "discovered_token_symbol": "TEXT NOT NULL DEFAULT ''",
            "discovered_ca": "TEXT NOT NULL DEFAULT ''",
            "evidence_source": "TEXT NOT NULL DEFAULT ''",
            "token_verification_status": "TEXT NOT NULL DEFAULT 'NO_DATA'",
            "token_evidence": "TEXT NOT NULL DEFAULT '[]'",
            "token_source_urls": "TEXT NOT NULL DEFAULT '[]'",
        }.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE platform_family_intelligence ADD COLUMN {name} {definition}")
        conn.commit()


def save_platform_family_intelligence(result):
    """Persist an engine result without changing Family membership or scanner grades."""
    import json
    family_id = int((result or {}).get("family_id") or 0)
    if family_id <= 0:
        return False
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            INSERT INTO platform_family_intelligence (
                family_id, project_name, official_x, official_website,
                description, infrastructure_types, official_token_symbol,
                official_token_ca, token_status, confidence,
                verification_status, verification_method, hostname, root_domain,
                subdomain, docs, github, source_url, core_word, derived_words,
                discovered_token_name, discovered_token_symbol, discovered_ca,
                evidence_source, token_verification_status, token_evidence,
                token_source_urls, evidence, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, strftime('%s','now'))
            ON CONFLICT(family_id) DO UPDATE SET
                project_name=excluded.project_name,
                official_x=excluded.official_x,
                official_website=excluded.official_website,
                description=excluded.description,
                infrastructure_types=excluded.infrastructure_types,
                official_token_symbol=excluded.official_token_symbol,
                official_token_ca=excluded.official_token_ca,
                token_status=excluded.token_status,
                confidence=excluded.confidence,
                verification_status=excluded.verification_status,
                verification_method=excluded.verification_method,
                hostname=excluded.hostname,
                root_domain=excluded.root_domain,
                subdomain=excluded.subdomain,
                docs=excluded.docs,
                github=excluded.github,
                source_url=excluded.source_url,
                core_word=excluded.core_word,
                derived_words=excluded.derived_words,
                discovered_token_name=excluded.discovered_token_name,
                discovered_token_symbol=excluded.discovered_token_symbol,
                discovered_ca=excluded.discovered_ca,
                evidence_source=excluded.evidence_source,
                token_verification_status=excluded.token_verification_status,
                token_evidence=excluded.token_evidence,
                token_source_urls=excluded.token_source_urls,
                evidence=excluded.evidence,
                updated_at=strftime('%s','now')
        """, (
            family_id, result.get("project_name", ""), result.get("official_x", ""),
            result.get("official_website", ""), result.get("description", ""),
            json.dumps(result.get("infrastructure_types", [])),
            result.get("official_token_symbol", ""), result.get("official_token_ca", ""),
            result.get("token_status", "NONE"), int(result.get("confidence", 0) or 0),
            result.get("verification_status", "NO_DATA"),
            result.get("verification_method", ""), result.get("hostname", ""),
            result.get("root_domain", ""), result.get("subdomain", ""),
            json.dumps(result.get("docs", [])), json.dumps(result.get("github", [])),
            result.get("source_url", ""), result.get("core_word", ""),
            json.dumps(result.get("derived_words", [])),
            result.get("discovered_token_name", ""), result.get("discovered_token_symbol", ""),
            result.get("discovered_ca", ""), result.get("evidence_source", ""),
            result.get("token_verification_status", "NO_DATA"),
            json.dumps(result.get("token_evidence", [])),json.dumps(result.get("token_source_urls", [])),
            json.dumps(result.get("evidence", [])),
        ))
        conn.commit()
    return True


def get_platform_family_intelligence(family_id):
    import json
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM platform_family_intelligence WHERE family_id=?", (int(family_id),)).fetchone()
    if not row:
        return None
    value = dict(row)
    value["infrastructure_types"] = json.loads(value["infrastructure_types"])
    value["evidence"] = json.loads(value["evidence"])
    value["docs"] = json.loads(value["docs"])
    value["github"] = json.loads(value["github"])
    value["derived_words"] = json.loads(value["derived_words"])
    value["token_evidence"] = json.loads(value["token_evidence"])
    value["token_source_urls"] = json.loads(value["token_source_urls"])
    return value


def list_standalone_identity_seeds(limit=100):
    """Candidates not represented by a Family remain eligible for investigation."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT p.* FROM platform_candidates p
            LEFT JOIN platform_family_members m
              ON lower(m.candidate_address)=lower(p.address)
            WHERE p.status='NEW' AND m.family_id IS NULL
            ORDER BY p.block_number DESC LIMIT ?
        """, (int(limit),)).fetchall()
    return [{
        "id": None,
        "subject_key": "candidate:" + row["address"].lower(),
        "creator": row["creator"] or "",
        "member_addresses": [row["address"]],
        "member_count": 1,
        "platform_types": row["infra_roles"] or "",
        "brand_hint": "",
        "token_names": [row["token_name"]] if row["token_name"] else [],
        "token_symbols": [row["token_symbol"]] if row["token_symbol"] else [],
        "identity_urls": [],
    } for row in rows]


def save_platform_identity_investigation(result):
    """Persist family-less results and reverse address evidence without inventing a Family."""
    import json
    subject_key = str(result.get("subject_key") or "").strip()
    if not subject_key:
        return False
    family_id = int(result.get("family_id") or 0) or None
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""INSERT INTO platform_identity_investigations
            (subject_key,family_id,source_url,verification_status,result,updated_at)
            VALUES(?,?,?,?,?,strftime('%s','now'))
            ON CONFLICT(subject_key) DO UPDATE SET family_id=excluded.family_id,
            source_url=excluded.source_url,verification_status=excluded.verification_status,
            result=excluded.result,updated_at=strftime('%s','now')""",
            (subject_key,family_id,result.get("source_url", ""),result.get("verification_status", "NO_DATA"),json.dumps(result)))
        for item in result.get("discovered_project_addresses", []):
            address=str(item.get("address") or "").lower()
            if not address:continue
            row=conn.execute("SELECT family_id FROM platform_family_members WHERE lower(candidate_address)=? LIMIT 1",(address,)).fetchone()
            linked_family=int(row[0]) if row else family_id
            if family_id is None and linked_family is not None:
                family_id=linked_family
            conn.execute("""INSERT INTO platform_identity_address_evidence
                (subject_key,family_id,address,role,source_url,evidence_source,updated_at)
                VALUES(?,?,?,?,?,?,strftime('%s','now'))
                ON CONFLICT(subject_key,address,source_url) DO UPDATE SET
                family_id=excluded.family_id,role=excluded.role,evidence_source=excluded.evidence_source,
                updated_at=strftime('%s','now')""",
                (subject_key,linked_family,address,item.get("role", ""),item.get("source_url", ""),item.get("evidence_source", "")))
        if family_id is not None:
            conn.execute("UPDATE platform_identity_investigations SET family_id=? WHERE subject_key=?",(family_id,subject_key))
        conn.commit()
    return True


def replace_platform_families(families):
    """
    Incrementally refresh platform families.

    Identity / verification state is preserved.

    Family identity key:
        creator + first_block

    Updated automatically:
        last_block
        member_count
        platform_types
        brand_hint
        members

    Preserved:
        status
        website_url
        domain
        confirmed_brand
        created_at
    """

    conn = sqlite3.connect(DB_PATH)

    for family in families:
        creator = (family.get("creator", "") or "").lower()
        first_block = int(family.get("first_block", 0) or 0)
        last_block = int(family.get("last_block", 0) or 0)

        if not creator or first_block <= 0:
            continue

        platform_types = ",".join(
            sorted(set(family.get("platform_types", [])))
        )

        brand_hint = family.get("brand_hint", "") or ""

        row = conn.execute("""
            SELECT id
            FROM platform_families
            WHERE creator = ?
              AND first_block = ?
            ORDER BY id ASC
            LIMIT 1
        """, (
            creator,
            first_block,
        )).fetchone()

        if row:
            family_id = int(row[0])

            conn.execute("""
                UPDATE platform_families
                SET
                    last_block = ?,
                    member_count = ?,
                    platform_types = ?,
                    brand_hint = ?,
                    updated_at = strftime('%s','now')
                WHERE id = ?
            """, (
                last_block,
                int(family.get("member_count", 0) or 0),
                platform_types,
                brand_hint,
                family_id,
            ))

        else:
            cur = conn.execute("""
                INSERT INTO platform_families (
                    creator,
                    first_block,
                    last_block,
                    member_count,
                    platform_types,
                    brand_hint,
                    status,
                    website_url,
                    domain,
                    confirmed_brand,
                    updated_at
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?,
                    'NEW', '', '', '',
                    strftime('%s','now')
                )
            """, (
                creator,
                first_block,
                last_block,
                int(family.get("member_count", 0) or 0),
                platform_types,
                brand_hint,
            ))

            family_id = cur.lastrowid

        # Refresh only this family's membership.
        conn.execute("""
            DELETE FROM platform_family_members
            WHERE family_id = ?
        """, (family_id,))

        for member in family.get("members", []):
            address = (
                member.get("address", "") or ""
            ).lower()

            if not address:
                continue

            conn.execute("""
                INSERT OR REPLACE INTO platform_family_members (
                    family_id,
                    candidate_address,
                    block_number
                )
                VALUES (?, ?, ?)
            """, (
                family_id,
                address,
                int(member.get("block_number", 0) or 0),
            ))

    conn.commit()
    conn.close()



def update_platform_family_identity(
    family_id,
    status,
    website_url="",
    domain="",
    confirmed_brand="",
):
    """
    Persist Identity Resolver result to one platform family.

    Does not change creator / first_block / members.
    Does not confirm anything by itself.
    """

    family_id = int(family_id)

    if family_id <= 0:
        return False

    allowed_statuses = {
        "NEW",
        "DISCOVERED",
        "WEBSITE_PENDING",
        "VERIFYING",
        "PLATFORM_CONFIRMED",
        "STRONG_CONFIRMED",
        "REJECTED",
    }

    if status not in allowed_statuses:
        raise ValueError(
            f"invalid platform family status: {status}"
        )

    conn = sqlite3.connect(DB_PATH)

    row = conn.execute("""
        SELECT id
        FROM platform_families
        WHERE id = ?
    """, (family_id,)).fetchone()

    if not row:
        conn.close()
        return False

    conn.execute("""
        UPDATE platform_families
        SET
            status = ?,
            website_url = ?,
            domain = ?,
            confirmed_brand = ?,
            updated_at = strftime('%s','now')
        WHERE id = ?
    """, (
        status,
        website_url or "",
        domain or "",
        confirmed_brand or "",
        family_id,
    ))

    conn.commit()
    conn.close()

    return True


def list_platform_families():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    rows = conn.execute("""
        SELECT
            id,
            creator,
            first_block,
            last_block,
            member_count,
            platform_types,
            brand_hint,
            status,
            website_url,
            domain,
            confirmed_brand,
            created_at,
            updated_at
        FROM platform_families
        ORDER BY first_block ASC
    """).fetchall()

    conn.close()

    return [dict(row) for row in rows]

# =========================================================
# Platform Discovery - Creator Deployment Cluster
#
# Read-only helper for independent Platform Discovery.
# Does NOT modify old A/B/S, Funding, Address Book or Telegram.
# =========================================================
def get_creator_deployment_cluster(
    creator,
    current_block,
    block_window=5000
):
    if not creator:
        return {
            "contracts": 0,
            "templates": 0,
            "code_hashes": 0,
        }

    current_block = int(current_block or 0)
    min_block = max(0, current_block - int(block_window))

    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("""
            SELECT
                COUNT(*) AS contracts,
                COUNT(DISTINCT template_key) AS templates,
                COUNT(DISTINCT code_hash) AS code_hashes
            FROM contracts
            WHERE lower(creator)=lower(?)
              AND block_number >= ?
              AND block_number < ?
        """, (
            creator,
            min_block,
            current_block
        )).fetchone()

    return {
        "contracts": int(row[0] or 0),
        "templates": int(row[1] or 0),
        "code_hashes": int(row[2] or 0),
    }



def list_platform_families_for_identity(limit=100):
    """
    Return unresolved platform families for Identity Discovery.

    Read only.
    NEW and VERIFYING are eligible.
    Confirmed families are excluded.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        """
        SELECT *
        FROM platform_families
        WHERE status IN ('NEW', 'DISCOVERED', 'WEBSITE_PENDING', 'VERIFYING')
        ORDER BY updated_at ASC, id ASC
        LIMIT ?
        """,
        (int(limit),),
    ).fetchall()

    conn.close()

    return [dict(row) for row in rows]
