from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "database.py"
MAIN = ROOT / "main.py"


def replace_once(text, old, new, label):
    if new in text:
        return text
    if old not in text:
        raise SystemExit(f"patch target not found: {label}")
    return text.replace(old, new, 1)


def patch_database():
    text = DB.read_text()
    old = '''            CREATE TABLE IF NOT EXISTS platform_identity_investigations (\n                subject_key TEXT PRIMARY KEY,\n                family_id INTEGER,\n                source_url TEXT NOT NULL DEFAULT '',\n                verification_status TEXT NOT NULL DEFAULT 'NO_DATA',\n                result TEXT NOT NULL DEFAULT '{}',\n                updated_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))\n            )\n'''
    new = '''            CREATE TABLE IF NOT EXISTS platform_identity_investigations (\n                subject_key TEXT PRIMARY KEY,\n                family_id INTEGER,\n                source_url TEXT NOT NULL DEFAULT '',\n                verification_status TEXT NOT NULL DEFAULT 'NO_DATA',\n                result TEXT NOT NULL DEFAULT '{}',\n                attempt_count INTEGER NOT NULL DEFAULT 0,\n                next_retry_at INTEGER NOT NULL DEFAULT 0,\n                backoff_seconds INTEGER NOT NULL DEFAULT 0,\n                last_failure_reason TEXT NOT NULL DEFAULT '',\n                last_success_at INTEGER NOT NULL DEFAULT 0,\n                updated_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))\n            )\n'''
    text = replace_once(text, old, new, "investigation table columns")

    marker = '''        existing = {row[1] for row in conn.execute("PRAGMA table_info(platform_family_intelligence)")}\n'''
    migration = '''        investigation_columns = {row[1] for row in conn.execute("PRAGMA table_info(platform_identity_investigations)")}\n        for name, definition in {\n            "attempt_count": "INTEGER NOT NULL DEFAULT 0",\n            "next_retry_at": "INTEGER NOT NULL DEFAULT 0",\n            "backoff_seconds": "INTEGER NOT NULL DEFAULT 0",\n            "last_failure_reason": "TEXT NOT NULL DEFAULT ''",\n            "last_success_at": "INTEGER NOT NULL DEFAULT 0",\n        }.items():\n            if name not in investigation_columns:\n                conn.execute(f"ALTER TABLE platform_identity_investigations ADD COLUMN {name} {definition}")\n        conn.execute("""\n            CREATE INDEX IF NOT EXISTS idx_identity_retry_due\n            ON platform_identity_investigations(next_retry_at, verification_status)\n        """)\n\n'''
    if migration not in text:
        if marker not in text:
            raise SystemExit("patch target not found: investigation migration marker")
        text = text.replace(marker, migration + marker, 1)

    old_func = '''def save_platform_identity_investigation(result):\n    """Persist family-less results and reverse address evidence without inventing a Family."""\n    import json\n    subject_key = str(result.get("subject_key") or "").strip()\n    if not subject_key:\n        return False\n    family_id = int(result.get("family_id") or 0) or None\n    with sqlite3.connect(DB_PATH) as conn:\n        conn.execute("""INSERT INTO platform_identity_investigations\n            (subject_key,family_id,source_url,verification_status,result,updated_at)\n            VALUES(?,?,?,?,?,strftime('%s','now'))\n            ON CONFLICT(subject_key) DO UPDATE SET family_id=excluded.family_id,\n            source_url=excluded.source_url,verification_status=excluded.verification_status,\n            result=excluded.result,updated_at=strftime('%s','now')""",\n            (subject_key,family_id,result.get("source_url", ""),result.get("verification_status", "NO_DATA"),json.dumps(result)))\n'''
    new_func = '''def _identity_failure_reason(result):\n    status = str((result or {}).get("verification_status") or "NO_DATA").upper()\n    evidence = list((result or {}).get("evidence") or [])\n    for item in reversed(evidence):\n        detail = str((item or {}).get("detail") or "").strip()\n        source = str((item or {}).get("source") or "").strip()\n        item_status = str((item or {}).get("status") or "").upper()\n        if detail and item_status in {"NO_DATA", "ERROR", "FAILED"}:\n            return f"{source or 'identity'}:{detail}"[:240]\n    if status == "PARTIAL":\n        return "cross_verification_incomplete"\n    if status == "NO_DATA":\n        return "no_official_identity_sources"\n    return ""\n\n\ndef save_platform_identity_investigation(result):\n    """Persist identity results plus explicit retry/backoff state."""\n    import json\n    subject_key = str(result.get("subject_key") or "").strip()\n    if not subject_key:\n        return False\n    family_id = int(result.get("family_id") or 0) or None\n    status = str(result.get("verification_status") or "NO_DATA").upper()\n    now = int(time.time())\n    with sqlite3.connect(DB_PATH) as conn:\n        previous = conn.execute(\n            "SELECT attempt_count FROM platform_identity_investigations WHERE subject_key=?",\n            (subject_key,),\n        ).fetchone()\n        prior_attempts = int(previous[0] or 0) if previous else 0\n        if status == "VERIFIED":\n            attempt_count = 0\n            backoff_seconds = 0\n            next_retry_at = 0\n            failure_reason = ""\n            last_success_at = now\n        else:\n            attempt_count = prior_attempts + 1\n            base = 300 if status == "PARTIAL" else 600\n            cap = 7200 if status == "PARTIAL" else 21600\n            backoff_seconds = min(cap, base * (2 ** min(attempt_count - 1, 8)))\n            next_retry_at = now + backoff_seconds\n            failure_reason = _identity_failure_reason(result)\n            last_success_at = 0\n        conn.execute("""INSERT INTO platform_identity_investigations\n            (subject_key,family_id,source_url,verification_status,result,attempt_count,\n             next_retry_at,backoff_seconds,last_failure_reason,last_success_at,updated_at)\n            VALUES(?,?,?,?,?,?,?,?,?,?,strftime('%s','now'))\n            ON CONFLICT(subject_key) DO UPDATE SET family_id=excluded.family_id,\n            source_url=excluded.source_url,verification_status=excluded.verification_status,\n            result=excluded.result,attempt_count=excluded.attempt_count,\n            next_retry_at=excluded.next_retry_at,backoff_seconds=excluded.backoff_seconds,\n            last_failure_reason=excluded.last_failure_reason,\n            last_success_at=CASE WHEN excluded.last_success_at>0 THEN excluded.last_success_at ELSE platform_identity_investigations.last_success_at END,\n            updated_at=strftime('%s','now')""",\n            (subject_key,family_id,result.get("source_url", ""),status,json.dumps(result),\n             attempt_count,next_retry_at,backoff_seconds,failure_reason,last_success_at))\n'''
    text = replace_once(text, old_func, new_func, "save investigation retry state")

    old_tail = '''        conn.commit()\n    return True\n\n\ndef replace_platform_families(families):\n'''
    new_tail = '''        conn.commit()\n    return {\n        "attempt_count": attempt_count,\n        "next_retry_at": next_retry_at,\n        "backoff_seconds": backoff_seconds,\n        "last_failure_reason": failure_reason,\n        "verification_status": status,\n    }\n\n\ndef replace_platform_families(families):\n'''
    # Only replace the tail after our function, not an earlier generic return True.
    start = text.find('def save_platform_identity_investigation(result):')
    end = text.find('def replace_platform_families(families):', start)
    block = text[start:end]
    if '"attempt_count": attempt_count' not in block:
        if '        conn.commit()\n    return True\n\n\n' not in block:
            raise SystemExit("patch target not found: save investigation return")
        block = block.replace('        conn.commit()\n    return True\n\n\n', '        conn.commit()\n    return {\n        "attempt_count": attempt_count,\n        "next_retry_at": next_retry_at,\n        "backoff_seconds": backoff_seconds,\n        "last_failure_reason": failure_reason,\n        "verification_status": status,\n    }\n\n\n', 1)
        text = text[:start] + block + text[end:]

    DB.write_text(text)


def patch_main():
    text = MAIN.read_text()
    old_where = '''            WHERE f.status IN ('NEW', 'DISCOVERED', 'WEBSITE_PENDING', 'VERIFYING')\n            ORDER BY\n                CASE WHEN i.updated_at IS NULL THEN 0 ELSE 1 END ASC,\n                CASE WHEN i.updated_at IS NULL THEN f.last_block END DESC,\n                COALESCE(i.updated_at, 0) ASC,\n                f.id ASC\n'''
    new_where = '''            WHERE f.status IN ('NEW', 'DISCOVERED', 'WEBSITE_PENDING', 'VERIFYING')\n              AND (i.subject_key IS NULL OR COALESCE(i.next_retry_at, 0) <= strftime('%s','now'))\n            ORDER BY\n                CASE WHEN i.updated_at IS NULL THEN 0 ELSE 1 END ASC,\n                CASE WHEN i.updated_at IS NULL THEN f.last_block END DESC,\n                COALESCE(i.next_retry_at, i.updated_at, 0) ASC,\n                f.id ASC\n'''
    text = replace_once(text, old_where, new_where, "retry due queue filter")

    old_counts = '''    total_candidates = 0\n    total_confirmed = 0\n    notifications = []\n'''
    new_counts = '''    total_candidates = 0\n    total_confirmed = 0\n    websites_found = 0\n    x_found = 0\n    status_counts = {"VERIFIED": 0, "PARTIAL": 0, "NO_DATA": 0}\n    retry_scheduled = 0\n    notifications = []\n'''
    text = replace_once(text, old_counts, new_counts, "round counters")

    old_after = '''            total_candidates += int(result.get("discovered_candidates", 0) or 0)\n            if result["verification_status"] == "VERIFIED":total_confirmed += 1\n\n            if dry_run:\n'''
    new_after = '''            total_candidates += int(result.get("discovered_candidates", 0) or 0)\n            status = str(result.get("verification_status") or "NO_DATA").upper()\n            status_counts[status if status in status_counts else "NO_DATA"] += 1\n            if result.get("official_website"): websites_found += 1\n            if result.get("official_x"): x_found += 1\n            if status == "VERIFIED": total_confirmed += 1\n\n            if dry_run:\n'''
    text = replace_once(text, old_after, new_after, "status observability counters")

    old_save = '''            if family.get("id"):save_platform_family_intelligence(result)\n            save_platform_identity_investigation(result)\n\n            if result["verification_status"] != "VERIFIED":\n                continue\n'''
    new_save = '''            if family.get("id"):save_platform_family_intelligence(result)\n            retry_state = save_platform_identity_investigation(result)\n\n            if result["verification_status"] != "VERIFIED":\n                if isinstance(retry_state, dict):\n                    retry_scheduled += 1\n                    print(\n                        "Identity retry scheduled",\n                        "subject=", result.get("subject_key"),\n                        "status=", retry_state.get("verification_status"),\n                        "attempt=", retry_state.get("attempt_count"),\n                        "backoff=", retry_state.get("backoff_seconds"),\n                        "next_retry_at=", retry_state.get("next_retry_at"),\n                        "reason=", retry_state.get("last_failure_reason"),\n                    )\n                continue\n'''
    text = replace_once(text, old_save, new_save, "retry state logging")

    old_return = '''    return {\n        "families": len(family_rows),\n        "investigations": len(subjects),\n        "candidates": total_candidates,\n        "confirmed": total_confirmed,\n        "notifications": notifications,\n    }\n'''
    new_return = '''    return {\n        "families": len(family_rows),\n        "investigations": len(subjects),\n        "candidates": total_candidates,\n        "confirmed": total_confirmed,\n        "websites_found": websites_found,\n        "x_found": x_found,\n        "verified": status_counts["VERIFIED"],\n        "partial": status_counts["PARTIAL"],\n        "no_data": status_counts["NO_DATA"],\n        "retry_scheduled": retry_scheduled,\n        "notifications": notifications,\n    }\n'''
    text = replace_once(text, old_return, new_return, "round return stats")

    old_send = '''        for message in result.get("notifications", []):\n            try:\n                await send_telegram(message)\n            except Exception as e:\n                print("Platform intelligence Telegram failed:", repr(e))\n'''
    new_send = '''        telegram_success = 0\n        telegram_failed = 0\n        for message in result.get("notifications", []):\n            try:\n                sent = await send_telegram(message)\n                if sent:\n                    telegram_success += 1\n                else:\n                    telegram_failed += 1\n                    print("Platform intelligence Telegram failed: send returned false")\n            except Exception as e:\n                telegram_failed += 1\n                print("Platform intelligence Telegram failed:", repr(e))\n        print(\n            "Platform identity round summary",\n            {\n                **{k:v for k,v in result.items() if k != "notifications"},\n                "telegram_success": telegram_success,\n                "telegram_failed": telegram_failed,\n            },\n        )\n'''
    text = replace_once(text, old_send, new_send, "telegram round observability")
    MAIN.write_text(text)


if __name__ == "__main__":
    patch_database()
    patch_main()
    print("retry/observability patch applied")
