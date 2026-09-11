import asyncio
import time

from database import DB_PATH, init_db, init_cluster_db, init_relation_db, init_platform_brand_db, init_platform_token_match_db, init_platform_candidate_db, init_platform_family_db, init_platform_family_intelligence_db, save_platform_family_intelligence, save_platform_identity_investigation, list_standalone_identity_seeds, seen, save_contract, add_cluster_member, recent_cluster, save_relation, get_relations_for_child, get_relations_for_parent, save_token_creator, find_confirmed_platform_matches, save_platform_token_match, save_platform_candidate, get_creator_deployment_cluster, list_new_platform_candidates, replace_platform_families, list_platform_families_for_identity, update_platform_family_identity
from detectors.fingerprint import fingerprint
from detectors.hyperevm import extract_words, classify
from detectors.token import detect_erc20
from detectors.pools import detect_pool_event
from detectors.rwa_assets import get_rwa_asset
from monitors.blocks import HyperEVMBlockMonitor
from notifier import send_telegram, format_family_intelligence_message
from platform_token_radar import normalize_text, classify_platform_words
from address_book import load_deployers, save_deployer
from platform_family import build_platform_families



def refresh_platform_families():
    """
    Refresh independent Platform Discovery families.

    Uses existing NEW platform_candidates only.
    Does not change A/B/S, Funding, Address Book or Telegram.
    """
    candidates = list_new_platform_candidates(limit=5000)

    if not candidates:
        return

    families = build_platform_families(candidates)

    if not families:
        return

    replace_platform_families(families)



def run_platform_identity_pipeline(
    *,
    limit=20,
    dry_run=False,
    engine=None,
    protocols=None,
    standalone_seeds=None,
):
    """
    Family -> address-first provider discovery -> cross-verification
    -> structured intelligence persistence -> optional brand promotion.

    No Telegram is sent here.

    Unknown/unverified families are never promoted.
    """

    from platform_identity_candidates import get_hyper_protocols, find_family_candidates
    from platform_family_intelligence import FamilyIntelligenceEngine

    from platform_brand_promoter import (
        promote_identity_to_brand,
    )

    family_rows = list_platform_families_for_identity(
        limit=limit
    )
    if standalone_seeds is None:
        standalone_seeds=list_standalone_identity_seeds(limit=limit)
        try:
            from platform_external_sources import discover_external_links
            import hashlib
            for item in discover_external_links()[:limit]:
                url=str(item.get("url") or "").strip()
                if not url:continue
                standalone_seeds.append({"id":None,"subject_key":"website:"+hashlib.sha256(url.encode()).hexdigest(),"creator":"","member_addresses":[],"member_count":0,"platform_types":"","brand_hint":"","token_names":[],"token_symbols":[],"identity_urls":[url]})
        except Exception as exc:
            print("Standalone identity source failed:",repr(exc))
    subjects=list(family_rows)+list(standalone_seeds or [])
    if not subjects:return {"families":0,"investigations":0,"candidates":0,"confirmed":0,"notifications":[]}

    # One DefiLlama request for the whole batch.
    if protocols is None:
        try:
            protocols = get_hyper_protocols()
        except Exception as e:
            print("Identity protocols fetch failed:", repr(e))
            protocols = []

    total_candidates = 0
    total_confirmed = 0
    notifications = []
    engine = engine or FamilyIntelligenceEngine()

    import sqlite3

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    try:
        for family in subjects:
            family.setdefault("identity_urls",[family.get("website_url")] if family.get("website_url") else [])

            members = conn.execute(
                """
                SELECT
                    m.candidate_address,
                    c.token_name,
                    c.token_symbol
                FROM platform_family_members m
                LEFT JOIN platform_candidates c
                  ON lower(c.address)=
                     lower(m.candidate_address)
                WHERE m.family_id=?
                ORDER BY m.block_number
                """,
                (family["id"],),
            ).fetchall() if family.get("id") else []

            if family.get("id"):
                family["member_addresses"] = [
                str(
                    x["candidate_address"] or ""
                ).strip()
                for x in members
                if str(
                    x["candidate_address"] or ""
                ).strip()
                ]

                family["token_names"] = [
                str(
                    x["token_name"] or ""
                ).strip()
                for x in members
                if str(
                    x["token_name"] or ""
                ).strip()
                ]

                family["token_symbols"] = [
                str(
                    x["token_symbol"] or ""
                ).strip()
                for x in members
                if str(
                    x["token_symbol"] or ""
                ).strip()
                ]

            # DefiLlama is retained only as an auxiliary URL hint. Empty names do
            # not prevent the engine's creator/member-address searches.
            auxiliary = find_family_candidates(family, protocols, limit=10)
            try:
                result = engine.enrich(family, auxiliary_candidates=auxiliary)
            except Exception as exc:
                result = {"family_id":family.get("id"),"subject_key":family.get("subject_key") or (f"family:{family.get('id')}" if family.get("id") else ""),"project_name":"","official_x":"","official_website":"","description":"","infrastructure_types":["Other"],"official_token_symbol":"","official_token_ca":"","token_status":"NONE","confidence":0,"verification_status":"NO_DATA","discovered_project_addresses":[],"discovered_candidates":0,"evidence":[{"source":"identity_engine","status":"NO_DATA","detail":type(exc).__name__}]}
            total_candidates += int(result.get("discovered_candidates", 0) or 0)
            if result["verification_status"] == "VERIFIED":total_confirmed += 1

            if dry_run:
                print("IDENTITY DRY RUN", "family=", family["id"], "status=", result["verification_status"], "token_status=", result["token_status"])
                continue

            if family.get("id"):save_platform_family_intelligence(result)
            save_platform_identity_investigation(result)

            if result["verification_status"] != "VERIFIED":
                continue

            notifications.append(format_family_intelligence_message(result, family))

            if family.get("id"):update_platform_family_identity(
                family["id"],
                "STRONG_CONFIRMED",
                result.get("official_website", ""),
                "",
                result.get("project_name", ""),
            )

            platform_address = ""

            if family.get("member_addresses"):
                platform_address = (
                    family["member_addresses"][0]
                )

            if family.get("id") and result.get("project_name"):
                from platform_token_radar import extract_domain_and_brand
                domain, brand = extract_domain_and_brand(result.get("official_website", ""))
                promote_identity_to_brand({
                    "status": "STRONG_CONFIRMED",
                    "confirmed": True,
                    "brand": brand,
                    "project_name": result.get("project_name", ""),
                    "domain": domain,
                    "website_url": result.get("official_website", ""),
                    "platform_types": result.get("infrastructure_types", []),
                    "official_token_name": "",
                    "official_token_symbol": result.get("official_token_symbol", ""),
                    "source": "FAMILY_INTELLIGENCE",
                },
                first_seen_block=int(
                    family.get(
                        "first_block",
                        0,
                    )
                    or 0
                ),
                platform_address=platform_address,
                )

            print(
                "Platform identity confirmed",
                "family=",
                family["id"],
                "brand=",
                result.get("project_name"),
                "status=",
                result.get("verification_status"),
            )

    finally:
        conn.close()

    return {
        "families": len(family_rows),
        "investigations": len(subjects),
        "candidates": total_candidates,
        "confirmed": total_confirmed,
        "notifications": notifications,
    }




# =========================================================
# Platform Identity realtime scheduler
#
# - Never blocks the block-monitor event loop with requests.
# - At most one Identity Pipeline run per cooldown window.
# - Multiple candidate events during the window are coalesced.
# =========================================================

IDENTITY_REFRESH_COOLDOWN = 600

_identity_last_refresh = 0.0
_identity_refresh_running = False


async def request_identity_refresh():
    global _identity_last_refresh
    global _identity_refresh_running

    now = time.monotonic()

    if _identity_refresh_running:
        return

    if (
        _identity_last_refresh > 0
        and
        now - _identity_last_refresh
        < IDENTITY_REFRESH_COOLDOWN
    ):
        return

    _identity_refresh_running = True

    try:
        result = await asyncio.to_thread(
            run_platform_identity_pipeline,
            limit=20,
            dry_run=False,
        )

        _identity_last_refresh = time.monotonic()

        print(
            "Platform identity refresh",
            {k:v for k,v in result.items() if k != "notifications"},
        )
        for message in result.get("notifications", []):
            try:
                await send_telegram(message)
            except Exception as e:
                print("Platform intelligence Telegram failed:", repr(e))

    except Exception as e:
        # Identity failure must never stop chain monitoring.
        print(
            "Platform identity refresh failed:",
            repr(e),
        )

    finally:
        _identity_refresh_running = False


async def handle_contract(monitor, event):
    address = event["address"]
    creator = event["creator"]
    block_number = event["block_number"]

    if seen(address):
        return

    try:
        code = await monitor.get_code(address)
        if not code:
            return

        fp = fingerprint(code)

        words = extract_words(code)
        result = classify(words)

        level = result["level"]
        stock_hits = result["stock_hits"]
        infra_hits = result["infra_hits"]

        # ---------------------------------------------------------
        # Funding graph:
        # important/core wallet -> fresh deployer -> new contract
        #
        # Funding alone never triggers Telegram.
        # Only when the funded wallet actually deploys a contract
        # do we connect the relationship.
        # ---------------------------------------------------------
        funding_sources = []
        now_ts = int(time.time())

        for (
            parent,
            child,
            relation_type,
            funding_tx,
            funding_block,
            first_seen,
        ) in get_relations_for_child(creator):

            if relation_type != "FUNDING":
                continue

            # Strong funding window: 2 hours
            if now_ts - int(first_seen) > 7200:
                continue

            funding_sources.append(parent.lower())

            # funded wallet -> deployed contract
            save_relation(
                parent=creator,
                child=address,
                relation_type="FUNDED_DEPLOYER_TO_CONTRACT",
                tx_hash=event.get("tx_hash", ""),
                block_number=block_number,
            )

            # upstream important wallet -> deployed contract
            save_relation(
                parent=parent,
                child=address,
                relation_type="FUNDER_TO_CONTRACT",
                tx_hash=event.get("tx_hash", ""),
                block_number=block_number,
            )

        funding_sources = sorted(set(funding_sources))

        token = await detect_erc20(monitor.w3, address)

        # =========================================================
        # Independent Platform Discovery
        #
        # Separate from old A/B/S grading.
        # Separate from address_book and Funding.
        #
        # A hit means ONLY:
        # "this contract deserves platform identity investigation".
        #
        # It does NOT confirm a real platform.
        # It does NOT trigger PT_MATCH.
        # =========================================================
        # Independent Platform Discovery gets its own enriched word set.
        #
        # Old A/B/S continues using the original bytecode-only `words`.
        # This enrichment affects Platform Discovery ONLY.
        platform_words = set(words or set())

        if token:
            token_name = str(token.get("name", "") or "").lower()
            token_symbol = str(token.get("symbol", "") or "").lower()

            import re

            platform_words.update(
                x for x in re.findall(
                    r"[a-z0-9]+",
                    token_name
                )
                if x
            )

            if token_symbol:
                platform_words.add(token_symbol)

        platform_discovery = classify_platform_words(platform_words)

        # ---------------------------------------------------------
        # Deployment Cluster signal
        #
        # Same creator + short block window + multiple structurally
        # different deployments => deserves platform investigation.
        #
        # Current contract is not yet in `contracts`, so add it
        # explicitly to the historical counts below.
        #
        # Candidate signal ONLY:
        # - no Telegram
        # - no A/B/S change
        # - no Funding dependency
        # - no Address Book dependency
        # ---------------------------------------------------------
        deployment_cluster = get_creator_deployment_cluster(
            creator=creator,
            current_block=block_number,
            block_window=5000,
        )

        cluster_contracts = deployment_cluster["contracts"] + 1

        prior_templates = deployment_cluster["templates"]
        cluster_templates = prior_templates

        # Current template may be new. Query helper intentionally
        # sees only earlier contracts, so check current template
        # against prior creator templates only when cluster threshold
        # is otherwise reachable.
        if cluster_contracts >= 3:
            import sqlite3
            from database import DB_PATH

            min_cluster_block = max(0, int(block_number) - 5000)

            with sqlite3.connect(DB_PATH) as cluster_conn:
                template_seen = cluster_conn.execute("""
                    SELECT 1
                    FROM contracts
                    WHERE lower(creator)=lower(?)
                      AND block_number >= ?
                      AND block_number < ?
                      AND template_key=?
                    LIMIT 1
                """, (
                    creator,
                    min_cluster_block,
                    block_number,
                    fp["template_key"],
                )).fetchone()

            if not template_seen:
                cluster_templates += 1

        deployment_cluster_hit = (
            cluster_contracts >= 3
            and cluster_templates >= 2
        )

        if deployment_cluster_hit and not platform_discovery["eligible"]:
            save_platform_candidate(
                address=address,
                creator=creator,
                block_number=block_number,
                tx_hash=event.get("tx_hash", ""),
                token_name=token.get("name", "") if token else "",
                token_symbol=token.get("symbol", "") if token else "",
                infra_roles=[],
                discovery_source=(
                    "DEPLOYMENT_CLUSTER:"
                    f"contracts={cluster_contracts},"
                    f"templates={cluster_templates}"
                ),
            )

            refresh_platform_families()

            asyncio.create_task(
                request_identity_refresh()
            )

            print(
                f"Platform cluster candidate recorded "
                f"address={address} "
                f"creator={creator} "
                f"contracts={cluster_contracts} "
                f"templates={cluster_templates}"
            )

        if platform_discovery["eligible"]:
            platform_roles = platform_discovery["types"]
            platform_hits = platform_discovery["matched_keywords"]

            save_platform_candidate(
                address=address,
                creator=creator,
                block_number=block_number,
                tx_hash=event.get("tx_hash", ""),
                token_name=token.get("name", "") if token else "",
                token_symbol=token.get("symbol", "") if token else "",
                infra_roles=platform_roles,
                discovery_source=(
                    "PLATFORM_DISCOVERY:"
                    + ",".join(platform_hits)
                ),
            )

            refresh_platform_families()

            asyncio.create_task(
                request_identity_refresh()
            )

            print(
                f"Platform candidate recorded "
                f"address={address} "
                f"types={','.join(platform_roles)} "
                f"hits={','.join(platform_hits)}"
            )

        # =========================================================
        # Platform -> Token Radar
        #
        # Independent from address_book / Funding / A-S grading.
        # Any deployer can trigger PT_MATCH.
        #
        # Requirements:
        # 1. Real platform is already confirmed in platform_brands.
        # 2. Platform existed before this token.
        # 3. Token matches platform brand or verified official-token mapping.
        # =========================================================
        if token:
            pt_matches = find_confirmed_platform_matches(
                token_name=token.get("name", ""),
                token_symbol=token.get("symbol", ""),
                token_block=block_number,
                normalize_func=normalize_text,
            )

            for platform in pt_matches:
                is_new = save_platform_token_match(
                    platform_id=platform["id"],
                    token_address=address,
                    token_name=token.get("name", ""),
                    token_symbol=token.get("symbol", ""),
                    deployer=creator,
                    match_type=platform["match_type"],
                    block_number=block_number,
                    tx_hash=event.get("tx_hash", ""),
                )

                if not is_new:
                    continue

                match_type = platform["match_type"]

                official_mapping_hit = (
                    "OFFICIAL_TOKEN_NAME" in match_type
                    or "OFFICIAL_TOKEN_SYMBOL" in match_type
                )

                if official_mapping_hit:
                    signal_text = (
                        "Signal: 平台先存在 → 官方 Token 名称/Symbol 映射命中\n"
                        "⚠️ 名称/符号与官方平台 Token 映射一致，"
                        "当前新 CA 尚未确认官方关系"
                    )
                else:
                    signal_text = (
                        "Signal: 平台先存在 → 后出现同品牌 Token\n"
                        "⚠️ 疑似平台相关 Token，尚未确认官方关系"
                    )

                pt_message = (
                    f"🚨 HyperEVM｜PT_MATCH\n\n"
                    f"Platform: {platform.get('platform_name') or platform.get('normalized_brand')}\n"
                    f"Website: {platform.get('website_url') or platform.get('domain')}\n"
                    f"Platform Type: {platform.get('platform_type') or 'UNKNOWN'}\n\n"
                    f"Token Name: {token.get('name', 'UNKNOWN')}\n"
                    f"Token Symbol: {token.get('symbol', 'UNKNOWN')}\n"
                    f"Token CA: {address}\n"
                    f"Deployer: {creator}\n\n"
                    f"Match Type: {match_type}\n"
                    f"Block: {block_number}\n"
                    f"TX: {event.get('tx_hash', '')}\n\n"
                    f"{signal_text}"
                )

                ok = await send_telegram(pt_message)
                print(
                    f"PT_MATCH token={address} "
                    f"platform={platform.get('domain')} "
                    f"type={platform['match_type']} "
                    f"telegram={ok}"
                )

        if token:
            cluster = recent_cluster(creator, seconds=86400)
            prior_core = [
                a for a, r, _ in cluster
                if a.lower() != address.lower()
                and (r == "A" or r.startswith("A:"))
            ]

            for parent in prior_core:
                save_relation(
                    parent=parent,
                    child=address,
                    relation_type="DEPLOYER_TO_TOKEN",
                    tx_hash=event.get("tx_hash", ""),
                    block_number=block_number,
                )

        role = (
            "A:" + ",".join(sorted(infra_hits))
            if level == "A_CANDIDATE"
            else "B"
        )
        add_cluster_member(creator, address, role)
        recent_count = len(recent_cluster(creator, seconds=1800))

        # ---------------------------------------------------------
        # S grade:
        # Do NOT promote because one wallet deployed many contracts.
        #
        # Strong pattern:
        # upstream core deployer -> funds another deployer ->
        # second deployer builds a complementary A-grade infra module.
        # ---------------------------------------------------------
        if level == "A_CANDIDATE" and funding_sources:
            upstream_roles = set()

            for funder in funding_sources:
                upstream_cluster = recent_cluster(
                    funder,
                    seconds=86400
                )

                for _, upstream_role, _ in upstream_cluster:
                    if upstream_role.startswith("A:"):
                        upstream_roles.update(
                            x
                            for x in upstream_role[2:].split(",")
                            if x
                        )

            current_roles = set(infra_hits)

            if (
                upstream_roles
                and current_roles
                and len(upstream_roles | current_roles) >= 2
                and upstream_roles != current_roles
            ):
                level = "S_CANDIDATE"

        token_symbol = token["symbol"] if token else ""
        token_name = token["name"] if token else ""

        save_contract(
            address=address,
            creator=creator,
            block_number=block_number,
            level=level,
            code_hash=fp["code_hash"],
            template_key=fp["template_key"],
            implementation=fp["implementation"],
            token_symbol=token_symbol,
            token_name=token_name,
        )

        address_book_hit = creator.lower() in load_deployers()
        if level in ("A_CANDIDATE", "S_CANDIDATE"):
            if level in ("A_CANDIDATE", "S_CANDIDATE"):
                save_deployer(creator)
            token_ca = token["address"] if token else "暂未发现"
            token_symbol_text = token["symbol"] if token else "UNKNOWN"
            token_name_text = token["name"] if token else "UNKNOWN"

            pool_text = "暂未发现"
            pair_text = "暂未发现"
            factory_text = "暂未发现"

            if token:
                token_rels = get_relations_for_child(address)

                for parent, child, relation_type, tx_hash, rel_block, first_seen in token_rels:
                    if relation_type in ("POOL_TOKEN0", "POOL_TOKEN1"):
                        pool_text = parent

                        pool_rels = get_relations_for_parent(parent)

                        for p2, c2, r2, tx2, b2, f2 in pool_rels:
                            if r2 in ("POOL_TOKEN0", "POOL_TOKEN1") and c2.lower() != address.lower():
                                pair_text = c2

                        pool_parent_rels = get_relations_for_child(parent)
                        for p3, c3, r3, tx3, b3, f3 in pool_parent_rels:
                            if r3 in ("V2_PAIR", "V3_POOL"):
                                factory_text = p3
                                break

                        break

            reason_text = f"Stock/RWA: {' , '.join(stock_hits) if stock_hits else 'None'} | Infra: {' , '.join(infra_hits) if infra_hits else 'None'}"
            message = (
                f"🚨 {'HyperEVM｜地址库命中' if address_book_hit and level not in ('A_CANDIDATE', 'S_CANDIDATE') else 'HyperEVM Radar ' + ('S' if level == 'S_CANDIDATE' else 'A')}\n\n"
                f"Contract Address:\n{address}\n\n"
                f"Token CA: {token_ca}\n"
                f"Token Symbol: {token_symbol_text}\n"
                f"Token Name: {token_name_text}\n"
                f"Pool: {pool_text}\n"
                f"Pair Asset: {pair_text}\n"
                f"Factory: {factory_text}\n\n"
                f"Deployer: {creator}\n"
                f"Funding Source: {', '.join(funding_sources[:3]) if funding_sources else '未发现'}\n"
                f"Match Reason: {reason_text}\n"
                f"Block: {block_number}\n"
                f"Code size: {fp['code_size']}\n"
                f"EIP-1167 Proxy: {fp['is_eip1167']}\n"
                f"EIP-1967 hint: {fp['eip1967_hint']}\n"
                f"Grade: {'ADDRESS_BOOK' if address_book_hit and level not in ('A_CANDIDATE', 'S_CANDIDATE') else ('S' if level == 'S_CANDIDATE' else 'A')} (early candidate, not investment advice)"
            )

            ok = await send_telegram(message)
            print(f"A candidate {address} telegram={ok}")

        else:
            print(f"B-level contract recorded without Telegram alert: {address}")

    except Exception as e:
        print(f"contract error {address}: {e}")


async def main():
    init_db()
    init_cluster_db()
    init_relation_db()
    init_platform_brand_db()
    init_platform_token_match_db()
    init_platform_candidate_db()
    init_platform_family_db()
    init_platform_family_intelligence_db()

    monitor = HyperEVMBlockMonitor()

    chain_id = await monitor.chain_id()
    if chain_id != 999:
        raise RuntimeError(f"Wrong chain ID: {chain_id}")

    async def block_handler(monitor_obj, event):
        await handle_contract(monitor_obj, event)

    original_get_contract_creations = monitor.get_contract_creations

    async def get_contract_creations_with_pools(block_number):
        events = await original_get_contract_creations(block_number)

        # Avoid duplicate events if the same address is discovered twice.
        event_addresses = {
            str(event.get("address", "")).lower()
            for event in events
        }

        logs = await monitor.get_logs(block_number)

        for log in logs:
            # Verified HyperEVM Factory internal token creation signal.
            #
            # Factory:
            # 0x6Bfd6C6B7225985d01d4ACf02575c2226Cc1F531
            #
            # topic0:
            # ebd2e66d2e2b280137b70daae53fa239997c8bbdaa13877c6b39e99892414749
            #
            # topics[1] = token
            # topics[2] = creator
            try:
                log_address = str(log.get("address", "")).lower()
                topics = log.get("topics", [])

                if (
                    log_address
                    == "0x6bfd6c6b7225985d01d4acf02575c2226cc1f531"
                    and len(topics) >= 3
                    and bytes(topics[0]).hex()
                    == "ebd2e66d2e2b280137b70daae53fa239997c8bbdaa13877c6b39e99892414749"
                ):
                    token_address = "0x" + bytes(topics[1])[-20:].hex()
                    creator = "0x" + bytes(topics[2])[-20:].hex()

                    tx_hash = ""
                    try:
                        tx_hash = log["transactionHash"].hex()
                    except Exception:
                        pass

                    if token_address.lower() not in event_addresses:
                        save_token_creator(
                            token_address,
                            creator,
                            block_number,
                            tx_hash,
                        )

                        events.append({
                            "address": token_address,
                            "creator": creator,
                            "block_number": block_number,
                            "tx_hash": tx_hash,
                        })

                        event_addresses.add(token_address.lower())

                        print(
                            f"Factory token creation detected "
                            f"token={token_address} creator={creator} "
                            f"block={block_number}"
                        )

            except Exception as e:
                print(f"factory token log error block={block_number}: {e}")

            # Existing V2/V3 pool detection.
            pool_info = detect_pool_event(log)
            if not pool_info:
                continue

            factory = pool_info["factory"]
            pool = pool_info["pool"]
            token0 = pool_info["token0"]
            token1 = pool_info["token1"]

            tx_hash = ""
            try:
                tx_hash = log["transactionHash"].hex()
            except Exception:
                pass

            save_relation(
                parent=factory,
                child=pool,
                relation_type=pool_info["type"],
                tx_hash=tx_hash,
                block_number=block_number,
            )

            save_relation(
                parent=pool,
                child=token0,
                relation_type="POOL_TOKEN0",
                tx_hash=tx_hash,
                block_number=block_number,
            )

            save_relation(
                parent=pool,
                child=token1,
                relation_type="POOL_TOKEN1",
                tx_hash=tx_hash,
                block_number=block_number,
            )

            # Evaluate RWA evidence independently for every detected pool.
            rwa0 = get_rwa_asset(token0)
            rwa1 = get_rwa_asset(token1)

            if rwa0 or rwa1:
                candidate = token1 if rwa0 else token0
                pair_asset = rwa0 if rwa0 else rwa1

                candidate_token = await detect_erc20(monitor.w3, candidate)
                candidate_symbol = (
                    candidate_token.get("symbol", "UNKNOWN")
                    if candidate_token else "UNKNOWN"
                )
                candidate_name = (
                    candidate_token.get("name", "UNKNOWN")
                    if candidate_token else "UNKNOWN"
                )

                print(
                    f"RWA Pair detected type={pool_info['type']} "
                    f"factory={factory} pool={pool} "
                    f"candidate={candidate} pair_asset={pair_asset}"
                )

                message = (
                    f"🚨 HyperEVM Radar A\n\n"
                    f"Strong Signal: STOCK/RWA PAIR\n"
                    f"Token CA: {candidate}\n"
                    f"Token Symbol: {candidate_symbol}\n"
                    f"Token Name: {candidate_name}\n"
                    f"Pool: {pool}\n"
                    f"Pair Asset: {pair_asset}\n"
                    f"Factory: {factory}\n"
                    f"Block: {block_number}\n"
                    f"Grade: A (strong pair evidence, not investment advice)"
                )

                ok = await send_telegram(message)
                print(f"RWA pair A candidate {candidate} telegram={ok}")

            else:
                print(
                    f"Pool detected type={pool_info['type']} "
                    f"factory={factory} pool={pool} "
                    f"token0={token0} token1={token1}"
                )

        return events

    monitor.get_contract_creations = get_contract_creations_with_pools

    await monitor.run(block_handler)


if __name__ == "__main__":
    asyncio.run(main())
