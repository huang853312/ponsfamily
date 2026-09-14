#!/usr/bin/env python3
"""Telegram 私聊授权、中文错误、低内存分析入口与进度提示适配层。

保留 telegram_bot.py 的命令和中文报告逻辑，只扩展：
- 原先配置的 TELEGRAM_CHAT_ID 继续直接允许；
- 授权群管理员可在机器人私聊中操作；
- 电报端不显示 Python/HTTP 英文堆栈；
- 分析子进程改走 main_fast.py，避免活跃链完整区块批量扫描拖死小服务器；
- 长分析期间定时发送中文进度，不再长时间无反应。
"""
import threading
import time
from pathlib import Path

import telegram_bot as bot

CONFIGURED_CHAT = bot.AUTHORIZED_CHAT
ROOT = Path(__file__).resolve().parent
_original_handle = bot.handle_message
_original_send = bot.send
_original_subprocess_run = bot.subprocess.run
_admin_cache = {"expires": 0.0, "ids": set()}


def _admin_ids():
    now = time.time()
    if now < _admin_cache["expires"]:
        return _admin_cache["ids"]
    ids = set()
    if CONFIGURED_CHAT:
        try:
            admins = bot.api_post("getChatAdministrators", {"chat_id": CONFIGURED_CHAT}) or []
            for item in admins:
                user = (item or {}).get("user") or {}
                uid = user.get("id")
                if uid is not None:
                    ids.add(str(uid))
        except Exception as exc:
            print(f"读取授权聊天管理员失败：{type(exc).__name__}: {exc}", flush=True)
    _admin_cache["ids"] = ids
    _admin_cache["expires"] = now + 300
    return ids


def _allowed(message: dict) -> bool:
    chat = message.get("chat") or {}
    chat_id = str(chat.get("id") or "")
    chat_type = str(chat.get("type") or "")
    sender = message.get("from") or {}
    sender_id = str(sender.get("id") or "")

    if CONFIGURED_CHAT and chat_id == CONFIGURED_CHAT:
        return True
    if chat_type == "private" and sender_id and sender_id in _admin_ids():
        return True
    return False


def _chinese_error_message(text: str) -> str:
    """把底层英文异常压缩成电报端中文原因；完整异常仍保留在服务日志。"""
    raw = str(text or "")
    low = raw.lower()
    if not ("分析失败" in raw or "分析任务异常" in raw):
        return raw

    prefix = raw.split("原因：", 1)[0].rstrip()
    if "403" in low or "forbidden" in low or "archive requests require" in low:
        reason = "链上历史数据节点拒绝了历史查询。系统已配置备用历史节点，请重新提交同一个合约地址。"
    elif "rate limited" in low or "limit exceeded" in low or "-32005" in low or "429" in low:
        reason = "链上节点当前限流。系统会尝试备用节点；如果全部节点都在限流，请稍后重新提交。"
    elif "timeout" in low or "timed out" in low or "超过 30 分钟" in raw:
        reason = "链上查询超时。该代币交易量较大或节点较慢，任务已安全停止，请重新提交。"
    elif "traceback" in low or 'file "/opt/wallet-radar/' in low or "rpc 请求失败" in raw:
        reason = "链上查询出现内部异常。详细技术日志已保存在服务器，电报端不再显示英文错误堆栈。"
    else:
        reason = "分析过程中出现链上查询异常。详细技术日志已保存在服务器。"
    if not prefix:
        prefix = "❌ 分析失败"
    return prefix + "\n原因：" + reason


def safe_send(text: str, chat_id: str = ""):
    cleaned = _chinese_error_message(text)
    return _original_send(cleaned, chat_id or bot.AUTHORIZED_CHAT)


def low_memory_subprocess_run(cmd, *args, **kwargs):
    """只把 wallet-radar 的 analyze 子进程切到低内存入口，其它 subprocess 调用保持原样。"""
    if isinstance(cmd, (list, tuple)) and "analyze" in cmd:
        rewritten = list(cmd)
        for i, item in enumerate(rewritten):
            if str(item).endswith("/main.py") or str(item) == "main.py":
                fast = ROOT / "main_fast.py"
                if fast.exists():
                    rewritten[i] = str(fast)
                break
        cmd = rewritten
    return _original_subprocess_run(cmd, *args, **kwargs)


def _heartbeat(chat_id: str, started: float):
    # 给短任务留时间；只有真正变成长任务才提示。
    time.sleep(45)
    count = 0
    while count < 12:
        with bot._busy_lock:
            running = bool(bot._busy.get("running"))
            same = abs(float(bot._busy.get("started") or 0.0) - float(started)) < 0.01
            chain = bot._busy.get("chain") or ""
            token = bot._busy.get("token") or ""
        if not running or not same:
            return
        elapsed = int(time.time() - started)
        chain_name = bot.CHAIN_ZH.get(chain, chain or "自动识别中")
        try:
            safe_send(
                "⏳ 分析仍在进行\n"
                f"链：{chain_name}\n"
                f"合约地址：{token}\n"
                f"已运行：{elapsed // 60} 分 {elapsed % 60} 秒\n"
                "正在深挖持币钱包、共同资金来源和资金回流；完成后会自动发送中文报告。",
                chat_id,
            )
        except Exception as exc:
            print(f"发送分析进度失败：{type(exc).__name__}: {exc}", flush=True)
        count += 1
        time.sleep(90)


def handle_message(message: dict):
    if not _allowed(message):
        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        print(
            "忽略未授权消息："
            f"chat_id={chat.get('id')} type={chat.get('type')} "
            f"user_id={sender.get('id')} username={sender.get('username', '')}",
            flush=True,
        )
        return

    chat = message.get("chat") or {}
    chat_id = str(chat.get("id") or "")
    with bot._busy_lock:
        was_running = bool(bot._busy.get("running"))

    # 原处理器内部还有旧的单 chat_id 检查；仅在本次已授权调用期间关闭。
    old = bot.AUTHORIZED_CHAT
    bot.AUTHORIZED_CHAT = ""
    try:
        _original_handle(message)
    finally:
        bot.AUTHORIZED_CHAT = old

    with bot._busy_lock:
        now_running = bool(bot._busy.get("running"))
        started = float(bot._busy.get("started") or 0.0)
    if (not was_running) and now_running and started and chat_id:
        threading.Thread(target=_heartbeat, args=(chat_id, started), daemon=True).start()


def main():
    if bot.check_config() != 0:
        raise SystemExit(2)
    bot.handle_message = handle_message
    bot.send = safe_send
    bot.subprocess.run = low_memory_subprocess_run
    print("电报私聊授权适配已启用。", flush=True)
    print("电报中文错误适配已启用。", flush=True)
    print("低内存分析入口已启用。", flush=True)
    print("长任务中文进度提示已启用。", flush=True)
    bot.poll_forever()


if __name__ == "__main__":
    main()
