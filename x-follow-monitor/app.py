import asyncio
import contextlib
import json
import os
import re
import socket
import subprocess
import time
from pathlib import Path
from urllib import error, parse, request

APP = Path(__file__).resolve().parent
STATE = APP / "state.json"
CLI = APP / "node_modules" / ".bin" / "xapi-to"
BOT = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT = os.getenv("TELEGRAM_CHAT_ID", "")
INTERVAL = max(300, int(os.getenv("X_FOLLOW_INTERVAL", "3600")))
MAX_CONCURRENCY = max(1, int(os.getenv("X_FOLLOW_CONCURRENCY", "1")))
ERROR_COOLDOWN = max(600, int(os.getenv("X_FOLLOW_ERROR_COOLDOWN", "21600")))
BILLING_PAUSE_SECONDS = max(1800, int(os.getenv("X_FOLLOW_BILLING_PAUSE", "1800")))
FOLLOW_SEND_INTERVAL = max(3.0, float(os.getenv("X_FOLLOW_SEND_INTERVAL", "3")))
MAX_FOLLOWING_PAGES = max(1, int(os.getenv("X_FOLLOW_MAX_PAGES", "100")))
# Cost control (new follows only): X lists the newest follows first, so each scan
# reads only the top page(s) until one overlaps the saved baseline. Older follows
# and unfollows are deliberately ignored, so the full list is never paginated.
# A new account's baseline is its first page only.
NEW_FOLLOW_MAX_PAGES = max(1, int(os.getenv("X_FOLLOW_NEW_PAGES", "3")))
USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")


def load():
    try:
        old = json.loads(STATE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        old = {}
    except (json.JSONDecodeError, OSError) as exc:
        # Fail closed instead of silently overwriting accounts/history with empty state.
        raise RuntimeError(
            f"拒绝重置无法读取的状态文件 {STATE}；请先检查/备份文件：{exc}"
        ) from exc
    if not isinstance(old, dict):
        raise RuntimeError(
            f"拒绝重置无效的状态文件 {STATE}：顶层 JSON 必须是对象"
        )
    accounts = old.get("accounts") or ([] if not old.get("a") else [old["a"]])
    known = old.get("known_map") or old.get("known") or {}
    ids = old.get("ids") or {}
    notices = old.get("notified_follows") or {}
    errors = old.get("error_notices") or {}
    errors = errors if isinstance(errors, dict) else {}
    # Remove per-account billing records from old releases. They must never
    # re-enter the notification cooldown and recreate a flood after restart.
    stale_billing_error_keys = [
        key for key, entry in errors.items()
        if str(key).startswith("query:")
        and isinstance(entry, dict)
        and (
            "PLATFORM_HTTP_402" in str(entry.get("last_error", ""))
            or "Insufficient balance or API Key limit exceeded" in str(entry.get("last_error", ""))
        )
    ]
    for key in stale_billing_error_keys:
        errors.pop(key, None)

    def is_stale_telegram_timeout(value):
        value = str(value or "").lower()
        return (
            "the read operation timed out" in value
            or "read timed out" in value
            or "telegram getupdates" in value and "timed out" in value
        )

    stale_telegram_error_keys = [
        key for key, entry in errors.items()
        if key in ("main-loop", "telegram-poll-timeout")
        and isinstance(entry, dict)
        and is_stale_telegram_timeout(entry.get("last_error", ""))
    ]
    for key in stale_telegram_error_keys:
        errors.pop(key, None)

    # Prior releases mislabeled proxy ECONNRESET as a malformed/missing-data
    # response. Purge those legacy error records and queued messages at startup.
    stale_legacy_proxy_error_keys = [
        key for key, entry in errors.items()
        if isinstance(entry, dict)
        and (str(key).startswith("query:") or key == "xapi:missing-data")
        and is_transient_network_error("", entry.get("last_error", ""))
    ]
    for key in stale_legacy_proxy_error_keys:
        errors.pop(key, None)

    queue = old.get("pending_notifications")
    queue = queue if isinstance(queue, list) else []
    purged_billing_notices = 0
    purged_telegram_timeout_notices = 0
    purged_legacy_proxy_notices = 0
    stale_legacy_proxy_notice_keys = {
        "error:" + key for key in stale_legacy_proxy_error_keys
    }

    # Older releases queued billing failures separately for each account.
    # Purge that backlog on load even while the global billing pause is active.
    # Never remove genuine follow alerts or unrelated errors.
    # Older releases queued one identical missing-data error per monitored account.
    # Collapse those persisted alerts during migration so the sender does not drain
    # hundreds of obsolete duplicate error messages after the next restart.
    compacted, shape_errors, existing_shape_notice = [], [], None
    for item in queue:
        if not isinstance(item, dict):
            continue
        key = item.get("key")
        message = str(item.get("text", ""))
        stale_account_billing_notice = (
            item.get("category") == "error"
            and str(key).startswith("error:query:")
            and (
                "PLATFORM_HTTP_402" in message
                or "Insufficient balance or API Key limit exceeded" in message
            )
        )
        if stale_account_billing_notice:
            purged_billing_notices += 1
            continue
        stale_telegram_timeout_notice = (
            item.get("category") == "error"
            and key in ("error:main-loop", "error:telegram-poll-timeout")
            and is_stale_telegram_timeout(message)
        )
        if stale_telegram_timeout_notice:
            purged_telegram_timeout_notices += 1
            continue
        if (
            item.get("category") == "error"
            and (
                key in stale_legacy_proxy_notice_keys
                or (
                    key == "error:xapi:missing-data"
                    and is_transient_network_error("", message)
                )
                or (
                    str(key).startswith("error:query:")
                    and is_transient_network_error("", message)
                )
            )
        ):
            purged_legacy_proxy_notices += 1
            continue
        if key == "error:xapi:missing-data":
            existing_shape_notice = item
        elif item.get("category") == "error" and "关注列表响应缺少 data 对象" in message:
            shape_errors.append(item)
        else:
            compacted.append(item)
    if shape_errors or existing_shape_notice:
        summary = existing_shape_notice or {
            "key": "error:xapi:missing-data",
            "category": "error",
            "created_at": min((item.get("created_at", time.time()) for item in shape_errors), default=time.time()),
            "attempts": 0,
            "next_attempt_at": 0,
        }
        summary["key"] = "error:xapi:missing-data"
        summary["category"] = "error"
        summary["text"] = (
            "xAPI 上游响应异常：多个账号的关注列表响应缺少 data 对象。"
            "本轮保留历史名单，没有把异常响应覆盖为新名单；同类错误已合并。"
            "请查看服务日志中的 XAPI_RESPONSE_DIAGNOSTIC 摘要。"
        )
        summary["attempts"] = max(
            [int(summary.get("attempts", 0) or 0)]
            + [int(item.get("attempts", 0) or 0) for item in shape_errors]
        )
        summary["next_attempt_at"] = max(
            [float(summary.get("next_attempt_at", 0) or 0)]
            + [float(item.get("next_attempt_at", 0) or 0) for item in shape_errors]
        )
        compacted.append(summary)
    if purged_billing_notices or stale_billing_error_keys:
        print(
            "STARTUP_PURGED_DUPLICATE_BILLING_ALERTS "
            f"queued={purged_billing_notices} error_records={len(stale_billing_error_keys)}",
            flush=True,
        )
    if purged_telegram_timeout_notices or stale_telegram_error_keys:
        print(
            "STARTUP_PURGED_STALE_TELEGRAM_TIMEOUT_ALERTS "
            f"queued={purged_telegram_timeout_notices} error_records={len(stale_telegram_error_keys)}",
            flush=True,
        )
    if purged_legacy_proxy_notices or stale_legacy_proxy_error_keys:
        print(
            "STARTUP_PURGED_LEGACY_PROXY_ALERTS "
            f"queued={purged_legacy_proxy_notices} error_records={len(stale_legacy_proxy_error_keys)}",
            flush=True,
        )
    api_pause_until = float(old.get("api_pause_until", 0) or 0)
    api_pause_reason = str(old.get("api_pause_reason", ""))
    last_scan_at = float(old.get("last_scan_at", 0) or 0)
    billing_policy_version = int(old.get("billing_policy_version", 0) or 0)
    # The previous policy paused the entire monitor after any single-account 402.
    # Clear that legacy pause once during migration so the fixed per-account policy
    # can resume immediately. New global pauses are retained across restarts.
    if billing_policy_version < 2:
        if api_pause_reason == "PLATFORM_HTTP_402":
            api_pause_until = 0
            api_pause_reason = ""
            print(
                "BILLING_PAUSE_CLEARED_AFTER_POLICY_MIGRATION "
                "previous_policy=single_account_stopped_all",
                flush=True,
            )
        billing_policy_version = 2
    # Retain automatic recheck as a safety net if an old longer pause remains.
    elif api_pause_reason == "PLATFORM_HTTP_402" and api_pause_until > time.time():
        scheduled_recheck = last_scan_at + BILLING_PAUSE_SECONDS
        if api_pause_until > scheduled_recheck:
            api_pause_until = max(time.time(), scheduled_recheck)
            print(
                "BILLING_PAUSE_MIGRATED_TO_AUTO_RECHECK "
                f"seconds={BILLING_PAUSE_SECONDS}",
                flush=True,
            )

    # New-follows-only policy (v3): drop the follow-alert backlog queued by the
    # old full-list comparison once, so the switch does not flood Telegram.
    if int(old.get("follow_policy_version", 0) or 0) < 3:
        before = len(compacted)
        compacted = [q for q in compacted if not (isinstance(q, dict) and q.get("category") == "follow")]
        if before - len(compacted):
            print(f"STARTUP_DROPPED_FOLLOW_BACKLOG count={before - len(compacted)} policy=new_follows_only", flush=True)
    accounts = list(dict.fromkeys(x for x in accounts if isinstance(x, str)))
    # Pre-v2 state may contain a single page only. Reconcile before alerting.
    version = int(old.get("schema_version", 0) or 0)
    pending = old.get("baseline_reconcile")
    if not isinstance(pending, list):
        pending = list(accounts) if version < 2 else []
    return {
        "schema_version": 2,
        "baseline_reconcile": list(dict.fromkeys(x for x in pending if isinstance(x, str))),
        "accounts": accounts,
        "running": bool(old.get("running", False)),
        "known": known if isinstance(known, dict) else {},
        "ids": ids if isinstance(ids, dict) else {},
        "offset": max(0, int(old.get("offset", 0))),
        "notified_follows": notices if isinstance(notices, dict) else {},
        "error_notices": errors if isinstance(errors, dict) else {},
        "pending_notifications": compacted,
        "last_follow_sent_at": float(old.get("last_follow_sent_at", 0) or 0),
        # Persist the circuit-breaker timer across deploys/restarts.
        "api_pause_until": api_pause_until,
        "api_pause_reason": api_pause_reason,
        "billing_policy_version": billing_policy_version,
        "follow_policy_version": 3,
        "last_scan_at": last_scan_at,
        "last_scan_result": str(old.get("last_scan_result", "")),
        "last_scan_ok_accounts": int(old.get("last_scan_ok_accounts", 0) or 0),
        "last_scan_failed_accounts": int(old.get("last_scan_failed_accounts", 0) or 0),
    }


def save(state):
    """Atomically persist scan state and deduplication records."""
    STATE.parent.mkdir(parents=True, exist_ok=True)
    temp = STATE.with_suffix(".json.tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, STATE)


def persist_before_notice(state):
    try:
        save(state)
        return True
    except Exception as exc:
        print(f"State save failed; notification withheld to avoid duplicate delivery: {exc}", flush=True)
        return False


def tg(method, data=None):
    encoded = parse.urlencode(data or {}).encode("utf-8")
    call = request.Request(f"https://api.telegram.org/bot{BOT}/{method}", data=encoded, method="POST")
    try:
        with request.urlopen(call, timeout=45) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[-300:]
        raise RuntimeError(f"Telegram HTTP {exc.code}: {detail}") from exc
    if not payload.get("ok"):
        raise RuntimeError(payload.get("description") or "Telegram API failed")
    return payload


def send(text):
    if BOT and CHAT:
        tg("sendMessage", {"chat_id": CHAT, "text": text})


def safe_send(text):
    try:
        send(text)
        return True
    except Exception as exc:
        print(f"Telegram send failed (delivery may be uncertain): {exc}", flush=True)
        return False


def queue_notification(state, key, message, category="system", source="", target=""):
    """Durably queue a Telegram message; a paced worker owns its delivery."""
    queue = state.setdefault("pending_notifications", [])
    if any(isinstance(item, dict) and item.get("key") == key for item in queue):
        return False
    queue.append({
        "key": key,
        "text": str(message)[:3500],
        "category": category,
        "source": source,
        "target": target,
        "created_at": time.time(),
        "attempts": 0,
        "next_attempt_at": 0,
    })
    save(state)
    print(f"NOTICE_QUEUED key={key} pending={len(queue)}", flush=True)
    return True


def is_telegram_poll_timeout(exc):
    """Recognize transient socket/read timeouts from Telegram long polling only."""
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return True
    if isinstance(exc, error.URLError):
        reason = exc.reason
        if isinstance(reason, (TimeoutError, socket.timeout)):
            return True
        return "timed out" in str(reason).lower()
    message = str(exc).lower()
    return "the read operation timed out" in message or "read timed out" in message


def telegram_retry_after(exc):
    """Return Telegram's explicit retry_after for a 429 rejection, if present."""
    message = str(exc)
    if "Telegram HTTP 429" not in message:
        return None
    start = message.find("{")
    if start < 0:
        return None
    try:
        payload = json.loads(message[start:])
        return max(1, int(payload.get("parameters", {}).get("retry_after", 1)))
    except (json.JSONDecodeError, TypeError, ValueError):
        return 1


def report_limited_error(state, key, message, now=None):
    """Throttle repeated failures by stable category, not changing error text."""
    now = time.time() if now is None else now
    records = state.setdefault("error_notices", {})
    entry = records.get(key, {})
    if not isinstance(entry, dict):
        entry = {}
    entry["count"] = int(entry.get("count", 0)) + 1
    entry["last_error"] = str(message)[:400]
    entry["active"] = True
    should_send = now - float(entry.get("last_sent", 0) or 0) >= ERROR_COOLDOWN
    if should_send:
        entry["last_sent"] = now
    records[key] = entry
    if should_send:
        if not persist_before_notice(state):
            return False
        extra = f"\n同类错误累计 {entry['count']} 次；重复通知已限频。" if entry["count"] > 1 else ""
        queue_notification(state, f"error:{key}", str(message)[:700] + extra, category="error")
    return should_send


def clear_error(state, key):
    entry = state.setdefault("error_notices", {}).get(key)
    if isinstance(entry, dict) and entry.get("active"):
        entry["active"] = False
        entry["resolved_at"] = time.time()
    # A recovered error should not be delivered later as a stale alert.
    queue_key = f"error:{key}"
    state["pending_notifications"] = [
        item for item in state.setdefault("pending_notifications", [])
        if not (isinstance(item, dict) and item.get("key") == queue_key)
    ]


def is_transient_network_error(code, message):
    """Identify reset/timeout failures that may recover on a bounded retry."""
    code = str(code or "").upper()
    message = str(message or "").lower()
    network_markers = (
        "econnreset", "connection reset", "read timed out", "the read operation timed out",
        "etimedout", "upstream timeout", "socket hang up", "econnrefused", "eai_again",
        "connection aborted", "remote end closed connection", "broken pipe",
    )
    if not any(marker in message for marker in network_markers):
        return False
    return code in ("", "PROXY_ERROR", "NETWORK_ERROR", "UPSTREAM_TIMEOUT",
                    "ECONNRESET", "ETIMEDOUT", "ECONNREFUSED", "EAI_AGAIN") or bool(
                        any(marker in message for marker in network_markers)
                    )


def _xapi_error_fields(payload):
    if not isinstance(payload, dict):
        return "", ""
    err = payload.get("error")
    if isinstance(err, dict):
        return str(err.get("code") or ""), str(err.get("message") or "")
    return str(payload.get("code") or ""), str(payload.get("message") or "")


def xapi(action, payload):
    """Call xAPI, retrying only connection-reset/timeout failures; never retry billing 402."""
    if not CLI.is_file():
        raise RuntimeError(f"xapi-to CLI 不存在：{CLI}")
    args = [str(CLI), "call", action, "--input", json.dumps(payload, ensure_ascii=False)]
    last_network_detail = ""
    billing_retry_used = False
    # Three bounded attempts protect against transient proxy resets without retrying
    # normal API, auth, or billing errors that can cost money.
    for attempt in range(1, 4):
        try:
            process = subprocess.run(
                args,
                capture_output=True, text=True, timeout=90, check=False,
            )
        except subprocess.TimeoutExpired:
            last_network_detail = "xAPI CLI request timed out"
            if attempt < 3:
                delay = attempt
                print(
                    f"XAPI_TRANSIENT_RETRY action={action} attempt={attempt}/3 "
                    f"reason=client_timeout wait={delay}s",
                    flush=True,
                )
                time.sleep(delay)
                continue
            raise RuntimeError(
                f"XAPI_TRANSIENT_NETWORK: request timed out after {attempt} attempts"
            )

        output = process.stdout or ""
        detail = (process.stderr or output or "xAPI failed")[-500:]
        if process.returncode:
            if is_transient_network_error("", detail):
                last_network_detail = detail
                if attempt < 3:
                    delay = attempt
                    print(
                        f"XAPI_TRANSIENT_RETRY action={action} attempt={attempt}/3 "
                        f"reason=process_network_error wait={delay}s",
                        flush=True,
                    )
                    time.sleep(delay)
                    continue
                raise RuntimeError(
                    f"XAPI_TRANSIENT_NETWORK: connection failed after {attempt} attempts: {detail}"
                )
            raise RuntimeError(detail)

        try:
            result = json.loads(output)
        except json.JSONDecodeError as exc:
            preview = output[-300:].replace("\n", " ")
            if is_transient_network_error("", detail + " " + preview):
                last_network_detail = (detail + " " + preview)[-300:]
                if attempt < 3:
                    delay = attempt
                    print(
                        f"XAPI_TRANSIENT_RETRY action={action} attempt={attempt}/3 "
                        f"reason=invalid_network_response wait={delay}s",
                        flush=True,
                    )
                    time.sleep(delay)
                    continue
                raise RuntimeError(
                    f"XAPI_TRANSIENT_NETWORK: invalid network response after {attempt} attempts: "
                    f"{last_network_detail}"
                ) from exc
            raise RuntimeError(f"xAPI 返回的不是 JSON：{preview}") from exc

        code, message = _xapi_error_fields(result)
        if (
            code == "PLATFORM_HTTP_402"
            and action in ("twitter.following", "twitter.user_by_screen_name")
            and not billing_retry_used
            and attempt < 3
        ):
            billing_retry_used = True
            print(
                f"XAPI_BILLING_RETRY action={action} attempt={attempt}/3 wait=1s",
                flush=True,
            )
            time.sleep(1)
            continue
        if is_transient_network_error(code, message):
            last_network_detail = f"{code}: {message}".strip(": ")
            if attempt < 3:
                delay = attempt
                print(
                    f"XAPI_TRANSIENT_RETRY action={action} attempt={attempt}/3 "
                    f"reason={code or 'network_error'} wait={delay}s",
                    flush=True,
                )
                time.sleep(delay)
                continue
            raise RuntimeError(
                f"XAPI_TRANSIENT_NETWORK: {last_network_detail} after {attempt} attempts"
            )
        return result

    raise RuntimeError(f"XAPI_TRANSIENT_NETWORK: {last_network_detail or 'unknown error'}")


def clean(value):
    name = value.strip().lstrip("@").split()[0] if value.strip() else ""
    if not USERNAME_RE.fullmatch(name):
        raise ValueError("X 用户名格式不正确")
    return name.lower()


def resolve_id(name, cached_uid=None):
    if cached_uid:
        return str(cached_uid)
    result = xapi("twitter.user_by_screen_name", {"screen_name": name, "provider": "x"})
    if isinstance(result, dict):
        upstream_error = result.get("error")
        if isinstance(upstream_error, dict) and upstream_error.get("code") == "PLATFORM_HTTP_402":
            message = str(upstream_error.get("message") or "Insufficient balance or API Key limit exceeded")[:300]
            raise RuntimeError(f"xAPI PLATFORM_HTTP_402: {message}")
    data = result.get("data") if isinstance(result, dict) else None
    if not isinstance(data, dict):
        raise RuntimeError(f"无法解析 @{name}：用户资料响应结构异常")
    uid = data.get("rest_id") or data.get("id")
    if not uid:
        raise RuntimeError(f"找不到 @{name}")
    return str(uid)


def _next_cursor(data):
    cursor = data.get("cursor_bottom")
    if cursor:
        return str(cursor)
    cursors = data.get("cursors")
    if isinstance(cursors, dict) and cursors.get("bottom"):
        return str(cursors["bottom"])
    return ""


class PartialFollowing(dict):
    """Top-of-list read that stopped at the saved baseline (no unfollow info)."""


def newest_following(name, cached_uid=None, stop_at=None):
    """Read cursor pages to completion; reject malformed/truncated lists.

    With stop_at (the saved baseline), stop after the first page that overlaps it
    and return a PartialFollowing: enough to detect new follows at minimal cost.
    """
    uid = resolve_id(name, cached_uid)
    following, cursor, seen_cursors = {}, "", set()
    for page_number in range(1, MAX_FOLLOWING_PAGES + 1):
        payload = {"user_id": uid, "provider": "x"}
        if cursor:
            payload["cursor"] = cursor
        result = xapi("twitter.following", payload)
        if isinstance(result, dict):
            upstream_error = result.get("error")
            if isinstance(upstream_error, dict) and upstream_error.get("code") == "PLATFORM_HTTP_402":
                message = str(upstream_error.get("message") or "Insufficient balance or API Key limit exceeded")[:300]
                raise RuntimeError(
                    f"xAPI PLATFORM_HTTP_402 at twitter.following page={page_number}: {message}"
                )
        data = result.get("data") if isinstance(result, dict) else None
        if not isinstance(data, dict):
            keys = sorted(str(key) for key in result.keys())[:25] if isinstance(result, dict) else []
            detail = ""
            if isinstance(result, dict):
                for field in ("error", "message", "detail", "description", "code", "status"):
                    value = result.get(field)
                    if value is not None:
                        detail = f"{field}={str(value)[:180]}"
                        break
            detail = re.sub(
                r"(?i)(api[_ -]?key|token|authorization|secret)(\s*[:=]\s*)[^,;\s]+",
                r"\1\2<redacted>",
                detail,
            )
            summary = f"top_level_keys={keys}"
            if detail:
                summary += f"; upstream={detail}"
            raise RuntimeError(
                f"@{name} 的关注列表响应缺少 data 对象；{summary}"
            )
        if "users" not in data or not isinstance(data["users"], list):
            raise RuntimeError(f"@{name} 的关注列表响应缺少有效 users 列表；为保护历史基线，本轮不更新名单")
        rows = data["users"]
        for row in rows:
            if not isinstance(row, dict):
                raise RuntimeError(f"@{name} 第 {page_number} 页存在无效用户记录")
            handle = row.get("screen_name")
            if not isinstance(handle, str) or not handle.strip():
                raise RuntimeError(f"@{name} 第 {page_number} 页存在缺少 screen_name 的记录；本轮不更新名单")
            following[handle.lower()] = handle
        next_cursor = _next_cursor(data)
        if not rows or not next_cursor:
            return following, uid
        if stop_at is not None and not stop_at:
            # No baseline yet: the first page is enough; older follows are ignored.
            return PartialFollowing(following), uid
        if stop_at and any(
            isinstance(row.get("screen_name"), str) and row["screen_name"].lower() in stop_at
            for row in rows
        ):
            print(f"XAPI_INCREMENTAL_STOP account=@{name} pages={page_number}", flush=True)
            return PartialFollowing(following), uid
        if stop_at is not None and page_number >= NEW_FOLLOW_MAX_PAGES:
            print(f"XAPI_NEW_PAGE_CAP account=@{name} pages={page_number}", flush=True)
            return PartialFollowing(following), uid
        if next_cursor == cursor or next_cursor in seen_cursors:
            raise RuntimeError(f"@{name} 的关注列表分页游标重复；本轮名单不完整")
        seen_cursors.add(next_cursor)
        cursor = next_cursor
    raise RuntimeError(
        f"@{name} 的关注列表超过 {MAX_FOLLOWING_PAGES} 页保护上限；"
        "本轮不更新历史基线，请核查 API 分页和 X_FOLLOW_MAX_PAGES"
    )


async def scan_one(name, cached_uid, sem, stop_at=None):
    async with sem:
        try:
            if stop_at is not None:
                current, uid = await asyncio.to_thread(newest_following, name, cached_uid, stop_at)
            else:
                current, uid = await asyncio.to_thread(newest_following, name, cached_uid)
            return name, current, uid, None
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return name, None, None, exc


def follow_notice_key(source, followed):
    return f"{source}|{followed}"


def _prune_follow_notice_keys(state, source, current):
    notices = state.setdefault("notified_follows", {})
    prefix = source + "|"
    state["pending_notifications"] = [
        item for item in state.setdefault("pending_notifications", [])
        if not (
            isinstance(item, dict)
            and item.get("category") == "follow"
            and item.get("source") == source
            and item.get("target") not in current
        )
    ]
    for key in list(notices):
        if key.startswith(prefix) and key[len(prefix):] not in current:
            notices.pop(key, None)


async def deliver_one_pending_notification(state):
    queue = state.setdefault("pending_notifications", [])
    if not queue:
        return False
    item = queue[0]
    now = time.time()
    next_attempt = float(item.get("next_attempt_at", 0) or 0)
    spacing_wait = FOLLOW_SEND_INTERVAL - (now - float(state.get("last_follow_sent_at", 0) or 0))
    wait_for = max(next_attempt - now, spacing_wait)
    if wait_for > 0:
        await asyncio.sleep(min(wait_for, 2.0))
        return False

    try:
        await asyncio.to_thread(send, item["text"])
    except Exception as exc:
        attempts = int(item.get("attempts", 0)) + 1
        item["attempts"] = attempts
        item["last_error"] = str(exc)[:400]
        retry_after = telegram_retry_after(exc)
        delay = retry_after + 1 if retry_after is not None else min(300, 30 * (2 ** min(attempts - 1, 4)))
        item["next_attempt_at"] = time.time() + delay
        try:
            save(state)
        except Exception as save_exc:
            print(f"Could not persist notification retry state: {save_exc}", flush=True)
        print(f"NOTICE_SEND_FAILED key={item.get('key')} attempts={attempts} retry_in={delay}s error={str(exc)[:220]}", flush=True)
        return False

    sent_at = time.time()
    if item.get("category") == "follow":
        state.setdefault("notified_follows", {})[item["key"]] = int(sent_at)
    state["last_follow_sent_at"] = sent_at
    sent_key = item.get("key")
    state["pending_notifications"] = [
        queued for queued in queue
        if not (isinstance(queued, dict) and queued.get("key") == sent_key)
    ]
    save(state)
    print(f"NOTICE_SENT key={sent_key} pending={len(state['pending_notifications'])}", flush=True)
    return True


async def notification_worker(state):
    while True:
        delivered = await deliver_one_pending_notification(state)
        if not delivered:
            await asyncio.sleep(1)


BILLING_PAUSE_MAX_SECONDS = max(
    BILLING_PAUSE_SECONDS, int(os.getenv("X_FOLLOW_BILLING_PAUSE_MAX", "21600"))
)


def billing_pause_seconds(streak):
    """Exponential backoff for a confirmed billing outage: 30m, 1h, 2h ... capped."""
    return min(BILLING_PAUSE_MAX_SECONDS, BILLING_PAUSE_SECONDS * (2 ** max(0, streak)))


async def billing_probe(state):
    """While a global 402 outage is active, recheck with ONE account instead of all.

    Returns True when the outage is still active (scan must stop here), False when
    the probe succeeded or failed for a non-billing reason (full scan continues).
    """
    if state.get("api_pause_reason") != "PLATFORM_HTTP_402" or not state.get("accounts"):
        return False
    name = state["accounts"][0]
    sem = asyncio.Semaphore(1)
    _name, _current, _uid, exc = await scan_one(name, state["ids"].get(name), sem)
    if exc is not None and "PLATFORM_HTTP_402" in str(exc):
        streak = int(state.get("billing_pause_streak", 0) or 0) + 1
        pause = billing_pause_seconds(streak)
        state["billing_pause_streak"] = streak
        state["api_pause_until"] = time.time() + pause
        state["last_scan_at"] = time.time()
        state["last_scan_result"] = "billing_blocked"
        entry = state.setdefault("error_notices", {}).get("xapi:billing-limit")
        if isinstance(entry, dict):
            entry["count"] = int(entry.get("count", 0)) + 1
        save(state)
        print(
            f"XAPI_BILLING_PROBE_STILL_BLOCKED account=@{name} streak={streak} "
            f"next_pause_seconds={pause} alert_suppressed=true",
            flush=True,
        )
        return True
    # Recovered (or a different, non-billing failure): resume normal full scans.
    entry = state.get("error_notices", {}).get("xapi:billing-limit")
    was_alerted = isinstance(entry, dict) and bool(entry.get("active"))
    state.pop("api_pause_until", None)
    state.pop("api_pause_reason", None)
    state.pop("billing_pause_streak", None)
    clear_error(state, "xapi:billing-limit")
    save(state)
    if was_alerted:
        queue_notification(
            state, f"recovered:xapi-billing:{int(time.time())}",
            "xAPI.to 计费已恢复：探测请求成功，监控恢复全量扫描。",
            category="system",
        )
    print(f"XAPI_BILLING_PROBE_RECOVERED account=@{name}", flush=True)
    return False


async def scan_all(state):
    if await billing_probe(state):
        return
    sem = asyncio.Semaphore(MAX_CONCURRENCY)
    reconcile = set(state.get("baseline_reconcile") or [])

    def stop_set(name):
        known = state.get("known", {}).get(name)
        if not known or name in reconcile:
            return set()  # baseline from the first page only
        return {str(x).lower() for x in known}

    results = await asyncio.gather(
        *(scan_one(name, state["ids"].get(name), sem, stop_set(name)) for name in list(state["accounts"]))
    )
    state["last_scan_at"] = time.time()
    state["last_scan_ok_accounts"] = sum(1 for _name, _current, _uid, exc in results if exc is None)
    state["last_scan_failed_accounts"] = sum(1 for _name, _current, _uid, exc in results if exc is not None)

    # Isolate occasional per-account 402s. A single failed query must not stop
    # all other monitored accounts; declare a global billing outage only when at
    # least half of accounts (and at least two when possible) return the same code.
    billing_failures = [
        (name, exc) for name, _current, _uid, exc in results
        if exc is not None and "PLATFORM_HTTP_402" in str(exc)
    ]
    billing_threshold = (
        min(len(results), max(2, (len(results) + 1) // 2))
        if results else 1
    )
    global_billing_failure = len(billing_failures) >= billing_threshold
    state["billing_policy_version"] = 2

    if global_billing_failure:
        name, exc = billing_failures[0]
        state["billing_pause_streak"] = 0
        state["api_pause_until"] = time.time() + billing_pause_seconds(0)
        state["api_pause_reason"] = "PLATFORM_HTTP_402"
        state["last_scan_result"] = "billing_blocked"
        # Remove stale account-level 402 alerts when a broad outage is confirmed.
        state["pending_notifications"] = [
            item for item in state.setdefault("pending_notifications", [])
            if not (
                isinstance(item, dict)
                and str(item.get("key", "")).startswith("error:query:")
                and "PLATFORM_HTTP_402" in str(item.get("text", ""))
            )
        ]
        save(state)
        report_limited_error(
            state,
            "xapi:billing-limit",
            "xAPI.to 疑似全局计费故障："
            f"{len(billing_failures)}/{len(results)} 个账号返回 PLATFORM_HTTP_402，"
            f"已达到全局暂停阈值 {billing_threshold}。"
            f"监控将暂停 {BILLING_PAUSE_SECONDS // 60} 分钟后用单个账号探测，"
            "仍失败则按 30分钟→1→2→4→6小时 退避且不再重复告警，恢复后会通知；"
            "已成功取得的数据会继续处理，失败账号历史基线会保留。"
            f"首个错误账号：@{name}；上游信息：{str(exc)[:220]}",
        )
        print(
            f"XAPI_BILLING_PAUSE reason=PLATFORM_HTTP_402 "
            f"failed={len(billing_failures)}/{len(results)} threshold={billing_threshold} "
            f"account=@{name} pause_seconds={BILLING_PAUSE_SECONDS}",
            flush=True,
        )
    else:
        # Clear legacy/global state whenever this scan does not meet the global threshold.
        state.pop("api_pause_until", None)
        state.pop("api_pause_reason", None)
        clear_error(state, "xapi:billing-limit")
        if billing_failures:
            print(
                f"XAPI_ACCOUNT_402_ISOLATED count={len(billing_failures)}/{len(results)} "
                f"global_threshold={billing_threshold}; other_accounts_continue=true",
                flush=True,
            )

    has_transient_network_failures = any(
        exc is not None and "XAPI_TRANSIENT_NETWORK" in str(exc)
        for _name, _current, _uid, exc in results
    )
    if not has_transient_network_failures:
        clear_error(state, "xapi:proxy-network")

    if not any(
        exc is not None and "关注列表响应缺少 data 对象" in str(exc)
        for _name, _current, _uid, exc in results
    ):
        clear_error(state, "xapi:missing-data")

    for name, current, uid, exc in results:
        error_key = f"query:{name}"
        if exc is not None:
            error_text = str(exc)
            if "PLATFORM_HTTP_402" in error_text:
                if not global_billing_failure:
                    detail = error_text.replace("\\n", " ")[:220]
                    report_limited_error(
                        state,
                        error_key,
                        f"xAPI 单账号请求返回 PLATFORM_HTTP_402：@{name} 本轮已隔离，"
                        "其他账号继续扫描；当前未达到全局暂停阈值。"
                        f"上游摘要：{detail}",
                    )
                    print(
                        f"XAPI_ACCOUNT_402 account=@{name} "
                        "baseline_preserved=true global_pause=false",
                        flush=True,
                    )
                continue
            if "XAPI_TRANSIENT_NETWORK" in error_text:
                report_limited_error(
                    state,
                    "xapi:proxy-network",
                    "xAPI 代理连接暂时失败（ECONNRESET/timeout）。每次调用已自动重试 3 次；"
                    "本轮失败账号的历史名单已保留，其余成功账号仍正常处理。"
                    "程序会在下个扫描周期继续尝试，不会按账号重复告警。"
                    f"上游摘要：{error_text[-220:]}",
                )
                print(
                    f"XAPI_TRANSIENT_NETWORK account=@{name} "
                    "action_retries=3 baseline_preserved=true",
                    flush=True,
                )
                continue
            if "关注列表响应缺少 data 对象" in error_text:
                diagnostic = error_text.split("响应缺少 data 对象", 1)[1][:240].strip(" ：;")
                message = (
                    "xAPI 上游响应异常：twitter.following 返回缺少 data 对象。"
                    "同类账号错误已合并；本轮保留历史名单，没有用异常响应覆盖数据。"
                )
                if diagnostic:
                    message += f" 响应摘要：{diagnostic}"
                report_limited_error(state, "xapi:missing-data", message)
                print(
                    f"XAPI_RESPONSE_DIAGNOSTIC account=@{name} {diagnostic}",
                    flush=True,
                )
            else:
                report_limited_error(state, error_key, f"xAPI 查询失败：@{name}：{error_text[:250]}")
            continue

        clear_error(state, error_key)
        state["ids"][name] = uid
        partial = isinstance(current, PartialFollowing)
        if partial and name in state["known"] and name not in state.get("baseline_reconcile", []):
            # Incremental read: only additions are knowable; keep every saved entry.
            now = set(state["known"][name]) | set(current)
        else:
            partial = False
            now = set(current)
            if not isinstance(current, PartialFollowing):
                _prune_follow_notice_keys(state, name, now)

        # Legacy known lists may cover only the first page. Refresh a complete
        # baseline once after upgrade, without generating a notification flood.
        pending = state.setdefault("baseline_reconcile", [])
        if name in pending:
            state["known"][name] = sorted(now)
            state["baseline_reconcile"] = [item for item in pending if item != name]
            save(state)
            print(f"BASELINE_RECONCILED account=@{name} follows={len(now)}; alerts_suppressed=upgrade", flush=True)
            continue

        if name not in state["known"]:
            state["known"][name] = sorted(now)
            save(state)
            print(f"BASELINE_ESTABLISHED account=@{name} follows={len(now)}", flush=True)
            continue

        old = set(state["known"][name])
        pending_keys = {
            item.get("key") for item in state.setdefault("pending_notifications", [])
            if isinstance(item, dict)
        }
        for target in sorted(now - old):
            notice_key = follow_notice_key(name, target)
            if notice_key in state["notified_follows"] or notice_key in pending_keys:
                continue
            queue_notification(
                state, notice_key,
                f"X关注提醒：@{name} 新关注了 @{current[target]}",
                category="follow", source=name, target=target,
            )
            pending_keys.add(notice_key)

        state["known"][name] = sorted(now)
        save(state)
        print(f"SCAN_OK account=@{name} follows={len(now)} new={len(now - old)}", flush=True)

    state["last_scan_result"] = (
        "billing_blocked" if global_billing_failure else
        ("success" if state["last_scan_failed_accounts"] == 0 else "partial_error")
    )
    save(state)
    print(
        f"SCAN_SUMMARY result={state['last_scan_result']} "
        f"ok={state['last_scan_ok_accounts']} failed={state['last_scan_failed_accounts']}",
        flush=True,
    )


def scan_completion_deadline(scan_requested_after_current, now=None):
    """A /scan_now received during a running/already-finished-but-unhandled task wins."""
    if scan_requested_after_current:
        return 0.0
    current = time.monotonic() if now is None else now
    return current + INTERVAL


def start_monitor(state):
    """Enable monitoring without clearing the persisted follow baseline."""
    state["running"] = True
    return "监控已开始：保留已有历史基线，只为尚未建立基线的账号初始化"


def help_text():
    return (
        "X 关注监控命令：\n"
        "/add 用户名 - 添加账号\n"
        "/remove 用户名 - 移除账号\n"
        "/list - 查看账号\n"
        "/start_monitor - 开始监控\n"
        "/stop_monitor - 停止监控\n"
        "/scan_now - 立即扫描\n"
        "/status - 查看状态"
    )


def update_message(update):
    """Accept commands from either a private/group chat or a Telegram channel."""
    return update.get("message") or update.get("channel_post") or {}


async def main():
    if not BOT or not CHAT or not os.getenv("XAPI_KEY"):
        raise SystemExit("Missing Telegram or XAPI_KEY config")
    state = load()
    notification_task = asyncio.create_task(notification_worker(state))
    scan_task, next_scan_at, retry_delay = None, 0.0, 5
    scan_requested_after_current = False
    telegram_poll_timeout_streak = 0
    while True:
        try:
            try:
                result = await asyncio.to_thread(
                    tg, "getUpdates", {
                        "offset": state["offset"] + 1,
                        "timeout": 10,
                        "allowed_updates": json.dumps(["message", "channel_post"]),
                    },
                )
            except Exception as exc:
                if not is_telegram_poll_timeout(exc):
                    raise
                telegram_poll_timeout_streak += 1
                print(
                    "TELEGRAM_POLL_TIMEOUT "
                    f"consecutive={telegram_poll_timeout_streak}; recovering_without_main_loop_failure=true",
                    flush=True,
                )
                if telegram_poll_timeout_streak >= 3:
                    report_limited_error(
                        state,
                        "telegram-poll-timeout",
                        "Telegram getUpdates 连续发生读取超时，机器人仍在自动重试；"
                        "该网络超时不会中断监控主循环，也不会重复发送每次超时告警。",
                    )
                result = {"result": []}
            else:
                if telegram_poll_timeout_streak:
                    print(
                        "TELEGRAM_POLL_RECOVERED "
                        f"after_consecutive_timeouts={telegram_poll_timeout_streak}",
                        flush=True,
                    )
                telegram_poll_timeout_streak = 0
                clear_error(state, "telegram-poll-timeout")
                clear_error(state, "main-loop")

            for update in result.get("result", []):
                state["offset"] = update["update_id"]
                msg = update_message(update)
                if str(msg.get("chat", {}).get("id")) != str(CHAT):
                    continue
                for line in (msg.get("text") or "").splitlines():
                    parts = line.strip().split()
                    raw_cmd = parts[0] if parts else ""
                    cmd = raw_cmd.split("@", 1)[0].lower()
                    try:
                        if cmd in ("/start", "/help"):
                            safe_send(help_text())
                        elif cmd in ("/add", "/set_a") and len(parts) > 1:
                            name = clean(parts[1])
                            if name not in state["accounts"]:
                                state["accounts"].append(name)
                            next_scan_at = 0.0
                            safe_send(f"已添加监控账号：@{name}")
                        elif cmd == "/remove" and len(parts) > 1:
                            name = clean(parts[1])
                            state["accounts"] = [item for item in state["accounts"] if item != name]
                            state["known"].pop(name, None)
                            state["ids"].pop(name, None)
                            prefix = name + "|"
                            state["notified_follows"] = {
                                key: value for key, value in state["notified_follows"].items()
                                if not key.startswith(prefix)
                            }
                            state["error_notices"].pop(f"query:{name}", None)
                            state["pending_notifications"] = [
                                item for item in state["pending_notifications"]
                                if not (isinstance(item, dict) and item.get("source") == name)
                            ]
                            safe_send(f"已移除：@{name}")
                        elif cmd == "/list":
                            body = "\n".join(f"{i + 1}. @{name}" for i, name in enumerate(state["accounts"]))
                            safe_send("当前监控账号：\n" + body if body else "当前没有监控账号")
                        elif cmd == "/start_monitor":
                            next_scan_at = 0.0
                            safe_send(start_monitor(state))
                        elif cmd == "/stop_monitor":
                            state["running"] = False
                            scan_requested_after_current = False
                            if scan_task and not scan_task.done():
                                scan_task.cancel()
                                with contextlib.suppress(asyncio.CancelledError):
                                    await scan_task
                                scan_task = None
                            safe_send("监控已停止")
                        elif cmd == "/scan_now":
                            # Keep api_pause_reason so the manual recheck probes one
                            # account first instead of spending a request per account.
                            state.pop("api_pause_until", None)
                            print("COMMAND_RECEIVED command=/scan_now", flush=True)
                            if scan_task is not None:
                                # The task may already be done but not yet handled below.
                                scan_requested_after_current = True
                                safe_send("当前扫描任务结算后会立即再扫描一次")
                            else:
                                next_scan_at = 0.0
                                safe_send("已安排立即扫描（如 API 额度仍不足，会再次暂停并保留历史名单）")
                        elif cmd == "/status":
                            task_state = "扫描中" if scan_task and not scan_task.done() else "等待中"
                            safe_send(
                                f"账号数：{len(state['accounts'])}\n"
                                f"状态：{'运行中' if state['running'] else '已停止'}\n"
                                f"任务：{task_state}\n"
                                f"间隔：{INTERVAL} 秒\n"
                                f"错误通知冷却：{ERROR_COOLDOWN} 秒\n"
                                f"待发送通知：{len(state['pending_notifications'])}\n"
                                + (f"API 暂停至：{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(state['api_pause_until']))}\n"
                                   if float(state.get("api_pause_until", 0) or 0) > time.time() else "")
                                + (f"最近扫描：{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(state['last_scan_at']))}\n"
                                   if float(state.get('last_scan_at', 0) or 0) > 0 else "最近扫描：暂无记录\n")
                                + (f"扫描结果：{state['last_scan_result']}（成功 {state['last_scan_ok_accounts']} / 失败 {state['last_scan_failed_accounts']}）\n"
                                   if state.get('last_scan_result') else "")
                                + "数据源：xAPI.to"
                            )
                        elif cmd.startswith("/"):
                            safe_send("无法识别这个命令。\n\n" + help_text())
                    except ValueError as exc:
                        safe_send(str(exc))

            if scan_task and scan_task.done():
                try:
                    await scan_task
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    report_limited_error(state, "scan-task", f"扫描任务失败：{str(exc)[:250]}")
                scan_task = None
                next_scan_at = scan_completion_deadline(scan_requested_after_current)
                if scan_requested_after_current:
                    scan_requested_after_current = False
                    print("SCAN_NOW_SCHEDULED reason=command_during_scan", flush=True)

            if (
                state["running"]
                and state["accounts"]
                and scan_task is None
                and time.monotonic() >= next_scan_at
                and time.time() >= float(state.get("api_pause_until", 0) or 0)
            ):
                scan_task = asyncio.create_task(scan_all(state))
            state["accounts"] = list(dict.fromkeys(state["accounts"]))
            save(state)
            retry_delay = 5
        except Exception as exc:
            try:
                report_limited_error(state, "main-loop", f"监控主循环失败：{str(exc)[:250]}")
            except Exception as report_exc:
                print(f"Unable to record main-loop error: {report_exc}", flush=True)
            try:
                save(state)
            except Exception as save_exc:
                print(f"Unable to persist state after main-loop error: {save_exc}", flush=True)
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 300)
        await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())
