import asyncio
import contextlib
import json
import os
import re
import subprocess
import time
from pathlib import Path
from urllib import error, parse, request

APP = Path(__file__).resolve().parent
STATE = APP / "state.json"
CLI = APP / "node_modules" / ".bin" / "xapi-to"
BOT = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT = os.getenv("TELEGRAM_CHAT_ID", "")
INTERVAL = max(300, int(os.getenv("X_FOLLOW_INTERVAL", "1800")))
MAX_CONCURRENCY = max(1, int(os.getenv("X_FOLLOW_CONCURRENCY", "3")))
ERROR_COOLDOWN = max(600, int(os.getenv("X_FOLLOW_ERROR_COOLDOWN", "21600")))
BILLING_PAUSE_SECONDS = max(1800, int(os.getenv("X_FOLLOW_BILLING_PAUSE", "21600")))
FOLLOW_SEND_INTERVAL = max(3.0, float(os.getenv("X_FOLLOW_SEND_INTERVAL", "3")))
MAX_FOLLOWING_PAGES = max(1, int(os.getenv("X_FOLLOW_MAX_PAGES", "100")))
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
    queue = old.get("pending_notifications")
    queue = queue if isinstance(queue, list) else []

    # Older releases queued one identical missing-data error per monitored account.
    # Collapse those persisted alerts during migration so the sender does not drain
    # hundreds of obsolete duplicate error messages after the next restart.
    compacted, shape_errors, existing_shape_notice = [], [], None
    for item in queue:
        if not isinstance(item, dict):
            continue
        key = item.get("key")
        message = str(item.get("text", ""))
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
        "api_pause_until": float(old.get("api_pause_until", 0) or 0),
        "api_pause_reason": str(old.get("api_pause_reason", "")),
        "last_scan_at": float(old.get("last_scan_at", 0) or 0),
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


def xapi(action, payload):
    if not CLI.is_file():
        raise RuntimeError(f"xapi-to CLI 不存在：{CLI}")
    process = subprocess.run(
        [str(CLI), "call", action, "--input", json.dumps(payload, ensure_ascii=False)],
        capture_output=True, text=True, timeout=90, check=False,
    )
    if process.returncode:
        raise RuntimeError((process.stderr or process.stdout or "xAPI failed")[-500:])
    try:
        return json.loads(process.stdout)
    except json.JSONDecodeError as exc:
        preview = process.stdout[-300:].replace("\n", " ")
        raise RuntimeError(f"xAPI 返回的不是 JSON：{preview}") from exc


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


def newest_following(name, cached_uid=None):
    """Read cursor pages to completion; reject malformed/truncated lists."""
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
                raise RuntimeError(f"xAPI PLATFORM_HTTP_402: {message}")
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
        if next_cursor == cursor or next_cursor in seen_cursors:
            raise RuntimeError(f"@{name} 的关注列表分页游标重复；本轮名单不完整")
        seen_cursors.add(next_cursor)
        cursor = next_cursor
    raise RuntimeError(
        f"@{name} 的关注列表超过 {MAX_FOLLOWING_PAGES} 页保护上限；"
        "本轮不更新历史基线，请核查 API 分页和 X_FOLLOW_MAX_PAGES"
    )


async def scan_one(name, cached_uid, sem):
    async with sem:
        try:
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


async def scan_all(state):
    sem = asyncio.Semaphore(MAX_CONCURRENCY)
    results = await asyncio.gather(
        *(scan_one(name, state["ids"].get(name), sem) for name in list(state["accounts"]))
    )
    state["last_scan_at"] = time.time()
    state["last_scan_ok_accounts"] = sum(1 for _name, _current, _uid, exc in results if exc is None)
    state["last_scan_failed_accounts"] = sum(1 for _name, _current, _uid, exc in results if exc is not None)

    # PLATFORM_HTTP_402 is explicitly non-transient. Stop the scan cycle before
    # changing baselines and pause future polls until a manual recheck.
    billing_failure = next(
        ((name, exc) for name, _current, _uid, exc in results
         if exc is not None and "PLATFORM_HTTP_402" in str(exc)),
        None,
    )
    if billing_failure:
        name, exc = billing_failure
        state["api_pause_until"] = time.time() + BILLING_PAUSE_SECONDS
        state["api_pause_reason"] = "PLATFORM_HTTP_402"
        state["last_scan_result"] = "billing_blocked"
        save(state)
        report_limited_error(
            state,
            "xapi:billing-limit",
            "xAPI.to 额度/余额不足或 API Key 请求限额已达到（PLATFORM_HTTP_402）。"
            f"自动扫描已暂停 {BILLING_PAUSE_SECONDS // 3600} 小时，避免重复请求；"
            "本轮已成功取得的数据仍会处理，失败账号的历史名单会保留。"
            "请充值或提高 API Key limit 后发送 /scan_now 恢复检查。"
            f"上游信息：{str(exc)[:250]}",
        )
        print(
            f"XAPI_BILLING_PAUSE reason=PLATFORM_HTTP_402 "
            f"account=@{name} pause_seconds={BILLING_PAUSE_SECONDS}",
            flush=True,
        )
    elif any(exc is None for _name, _current, _uid, exc in results):
        state.pop("api_pause_until", None)
        state.pop("api_pause_reason", None)
        clear_error(state, "xapi:billing-limit")

    if not any(
        exc is not None and "关注列表响应缺少 data 对象" in str(exc)
        for _name, _current, _uid, exc in results
    ):
        clear_error(state, "xapi:missing-data")

    for name, current, uid, exc in results:
        error_key = f"query:{name}"
        if exc is not None:
            error_text = str(exc)
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
        now = set(current)
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
        "billing_blocked" if billing_failure else
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
    while True:
        try:
            result = await asyncio.to_thread(
                tg, "getUpdates", {
                    "offset": state["offset"] + 1,
                    "timeout": 10,
                    "allowed_updates": json.dumps(["message", "channel_post"]),
                },
            )
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
                            state.pop("api_pause_until", None)
                            state.pop("api_pause_reason", None)
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
