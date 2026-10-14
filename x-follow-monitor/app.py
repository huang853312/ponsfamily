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
MAX_FOLLOWING_PAGES = max(1, int(os.getenv("X_FOLLOW_MAX_PAGES", "100")))
USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")


def load():
    try:
        old = json.loads(STATE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        old = {}
    if not isinstance(old, dict):
        old = {}
    accounts = old.get("accounts") or ([] if not old.get("a") else [old["a"]])
    known = old.get("known_map") or old.get("known") or {}
    ids = old.get("ids") or {}
    notices = old.get("notified_follows") or {}
    errors = old.get("error_notices") or {}
    return {
        "accounts": list(dict.fromkeys(x for x in accounts if isinstance(x, str))),
        "running": bool(old.get("running", False)),
        "known": known if isinstance(known, dict) else {},
        "ids": ids if isinstance(ids, dict) else {},
        "offset": max(0, int(old.get("offset", 0))),
        "notified_follows": notices if isinstance(notices, dict) else {},
        "error_notices": errors if isinstance(errors, dict) else {},
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
        safe_send(str(message)[:700] + extra)
    return should_send


def clear_error(state, key):
    entry = state.setdefault("error_notices", {}).get(key)
    if isinstance(entry, dict) and entry.get("active"):
        entry["active"] = False
        entry["resolved_at"] = time.time()


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
        data = result.get("data") if isinstance(result, dict) else None
        if not isinstance(data, dict):
            raise RuntimeError(f"@{name} 的关注列表响应缺少 data 对象")
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
    for key in list(notices):
        if key.startswith(prefix) and key[len(prefix):] not in current:
            notices.pop(key, None)


async def scan_all(state):
    sem = asyncio.Semaphore(MAX_CONCURRENCY)
    results = await asyncio.gather(
        *(scan_one(name, state["ids"].get(name), sem) for name in list(state["accounts"]))
    )
    for name, current, uid, exc in results:
        error_key = f"query:{name}"
        if exc is not None:
            report_limited_error(state, error_key, f"xAPI 查询失败：@{name}：{str(exc)[:250]}")
            continue

        clear_error(state, error_key)
        state["ids"][name] = uid
        now = set(current)
        _prune_follow_notice_keys(state, name, now)

        if name not in state["known"]:
            state["known"][name] = sorted(now)
            save(state)
            print(f"BASELINE_ESTABLISHED account=@{name} follows={len(now)}", flush=True)
            continue

        old = set(state["known"][name])
        for target in sorted(now - old):
            notice_key = follow_notice_key(name, target)
            if notice_key in state["notified_follows"]:
                continue
            # Persist before sending: a restart cannot resend the same observed
            # relationship. Telegram has no idempotency key; ambiguous failures
            # are logged and not blindly retried.
            state["notified_follows"][notice_key] = int(time.time())
            if not persist_before_notice(state):
                continue
            if not safe_send(f"X关注提醒：@{name} 新关注了 @{current[target]}"):
                print(
                    f"FOLLOW_NOTICE_UNCONFIRMED key={notice_key}; "
                    "dedupe marker retained to prevent duplicate push",
                    flush=True,
                )

        state["known"][name] = sorted(now)
        save(state)
        print(f"SCAN_OK account=@{name} follows={len(now)} new={len(now - old)}", flush=True)


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
    scan_task, next_scan_at, retry_delay = None, 0.0, 5
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
                            safe_send(f"已移除：@{name}")
                        elif cmd == "/list":
                            body = "\n".join(f"{i + 1}. @{name}" for i, name in enumerate(state["accounts"]))
                            safe_send("当前监控账号：\n" + body if body else "当前没有监控账号")
                        elif cmd == "/start_monitor":
                            next_scan_at = 0.0
                            safe_send(start_monitor(state))
                        elif cmd == "/stop_monitor":
                            state["running"] = False
                            if scan_task and not scan_task.done():
                                scan_task.cancel()
                                with contextlib.suppress(asyncio.CancelledError):
                                    await scan_task
                                scan_task = None
                            safe_send("监控已停止")
                        elif cmd == "/scan_now":
                            next_scan_at = 0.0
                            safe_send("已安排立即扫描")
                        elif cmd == "/status":
                            task_state = "扫描中" if scan_task and not scan_task.done() else "等待中"
                            safe_send(
                                f"账号数：{len(state['accounts'])}\n"
                                f"状态：{'运行中' if state['running'] else '已停止'}\n"
                                f"任务：{task_state}\n"
                                f"间隔：{INTERVAL} 秒\n"
                                f"错误通知冷却：{ERROR_COOLDOWN} 秒\n"
                                "数据源：xAPI.to"
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
                next_scan_at = time.monotonic() + INTERVAL

            if state["running"] and state["accounts"] and scan_task is None and time.monotonic() >= next_scan_at:
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
