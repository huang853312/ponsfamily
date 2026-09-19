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
USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")


def load():
    try:
        old = json.loads(STATE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        old = {}

    accounts = old.get("accounts") or ([] if not old.get("a") else [old["a"]])
    return {
        "accounts": list(dict.fromkeys(accounts)),
        "running": bool(old.get("running", False)),
        "known": old.get("known_map") or old.get("known") or {},
        "ids": old.get("ids") or {},
        "offset": int(old.get("offset", 0)),
    }


def save(state):
    """Write state atomically so a restart cannot leave half-written JSON."""
    STATE.parent.mkdir(parents=True, exist_ok=True)
    temp = STATE.with_suffix(".json.tmp")
    temp.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temp, STATE)


def tg(method, data=None):
    encoded = parse.urlencode(data or {}).encode("utf-8")
    call = request.Request(
        f"https://api.telegram.org/bot{BOT}/{method}",
        data=encoded,
        method="POST",
    )
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
    except Exception as exc:
        print(f"Telegram send failed: {exc}", flush=True)


def xapi(action, payload):
    if not CLI.is_file():
        raise RuntimeError(f"xapi-to CLI 不存在：{CLI}")

    process = subprocess.run(
        [str(CLI), "call", action, "--input", json.dumps(payload, ensure_ascii=False)],
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
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

    data = xapi(
        "twitter.user_by_screen_name",
        {"screen_name": name, "provider": "x"},
    ).get("data") or {}
    if not isinstance(data, dict):
        raise RuntimeError(f"无法解析 @{name}")
    uid = data.get("rest_id") or data.get("id")
    if not uid:
        raise RuntimeError(f"找不到 @{name}")
    return str(uid)


def newest_following(name, cached_uid=None):
    uid = resolve_id(name, cached_uid)
    data = xapi(
        "twitter.following",
        {"user_id": uid, "provider": "x"},
    ).get("data") or {}
    if not isinstance(data, dict):
        raise RuntimeError(f"@{name} 的关注列表结构异常")

    rows = data.get("users") or []
    if not isinstance(rows, list):
        raise RuntimeError(f"@{name} 的 users 字段不是列表")

    following = {
        row["screen_name"].lower(): row["screen_name"]
        for row in rows
        if isinstance(row, dict) and row.get("screen_name")
    }
    return following, uid


async def scan_one(name, cached_uid, sem):
    async with sem:
        current, uid = await asyncio.to_thread(newest_following, name, cached_uid)
        return name, current, uid


async def scan_all(state):
    sem = asyncio.Semaphore(MAX_CONCURRENCY)
    results = await asyncio.gather(
        *(
            scan_one(name, state["ids"].get(name), sem)
            for name in list(state["accounts"])
        ),
        return_exceptions=True,
    )

    for result in results:
        if isinstance(result, Exception):
            safe_send(f"xAPI 查询失败：{str(result)[:250]}")
            continue

        name, current, uid = result
        state["ids"][name] = uid
        now = set(current)

        if name not in state["known"]:
            state["known"][name] = sorted(now)
            safe_send(f"@{name} 基线已建立：当前读取到 {len(now)} 个关注账号")
            continue

        old = set(state["known"][name])
        for key in sorted(now - old):
            safe_send(f"X关注提醒：@{name} 新关注了 @{current[key]}")
        state["known"][name] = sorted(now)


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
    scan_task = None
    next_scan_at = 0.0

    while True:
        try:
            result = await asyncio.to_thread(
                tg,
                "getUpdates",
                {"offset": state["offset"] + 1, "timeout": 10},
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
                            state["known"].pop(name, None)
                            next_scan_at = 0.0
                            safe_send(f"已添加监控账号：@{name}")
                        elif cmd == "/remove" and len(parts) > 1:
                            name = clean(parts[1])
                            state["accounts"] = [
                                item for item in state["accounts"] if item != name
                            ]
                            state["known"].pop(name, None)
                            state["ids"].pop(name, None)
                            safe_send(f"已移除：@{name}")
                        elif cmd == "/list":
                            body = "\n".join(
                                f"{index + 1}. @{name}"
                                for index, name in enumerate(state["accounts"])
                            )
                            safe_send("当前监控账号：\n" + body if body else "当前没有监控账号")
                        elif cmd == "/start_monitor":
                            state["running"] = True
                            state["known"] = {}
                            next_scan_at = 0.0
                            safe_send("监控已开始：先建立基线，只提醒之后的新关注")
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
                            scan_state = "扫描中" if scan_task and not scan_task.done() else "等待中"
                            safe_send(
                                f"账号数：{len(state['accounts'])}\n"
                                f"状态：{'运行中' if state['running'] else '已停止'}\n"
                                f"任务：{scan_state}\n"
                                f"间隔：{INTERVAL} 秒\n"
                                "数据源：xAPI.to"
                            )
                        elif cmd.startswith("/"):
                            safe_send("无法识别这个命令。\n\n" + help_text())
                    except ValueError as exc:
                        safe_send(str(exc))

            if scan_task and scan_task.done():
                try:
                    await scan_task
                except Exception as exc:
                    safe_send(f"扫描任务失败：{str(exc)[:250]}")
                scan_task = None
                next_scan_at = time.monotonic() + INTERVAL

            if (
                state["running"]
                and state["accounts"]
                and scan_task is None
                and time.monotonic() >= next_scan_at
            ):
                scan_task = asyncio.create_task(scan_all(state))

            state["accounts"] = list(dict.fromkeys(state["accounts"]))
            save(state)
        except Exception as exc:
            save(state)
            if state["running"]:
                safe_send(f"监控主循环失败：{str(exc)[:250]}")
            await asyncio.sleep(5)

        await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())
