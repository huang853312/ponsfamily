#!/usr/bin/env python3
"""Telegram 私聊授权与中文错误适配层。

保留 telegram_bot.py 的分析逻辑，只扩展两件事：
- 原先配置的 TELEGRAM_CHAT_ID 继续直接允许；
- 如果用户在机器人私聊中操作，并且该用户是已配置群组/频道的管理员，也允许；
- 电报端不再显示 Python/HTTP 英文堆栈，只显示中文可操作原因。
"""
import time

import telegram_bot as bot

CONFIGURED_CHAT = bot.AUTHORIZED_CHAT
_original_handle = bot.handle_message
_original_send = bot.send
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

    # 保留链和 CA 等已经是中文的头部，不把英文 traceback 发给用户。
    prefix = raw.split("原因：", 1)[0].rstrip()
    if "403" in low or "forbidden" in low or "archive requests require" in low:
        reason = "链上历史数据节点拒绝了历史查询。系统会切换备用历史节点，请重新提交同一个合约地址。"
    elif "rate limited" in low or "limit exceeded" in low or "-32005" in low or "429" in low:
        reason = "链上节点当前限流。系统会尝试备用节点；如果全部节点都在限流，请稍后重新提交。"
    elif "timeout" in low or "timed out" in low or "超过 30 分钟" in raw:
        reason = "链上查询超时。该代币交易量较大或节点较慢，请稍后重新提交。"
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

    # 原处理器内部还有旧的单 chat_id 检查；仅在本次已授权调用期间关闭。
    old = bot.AUTHORIZED_CHAT
    bot.AUTHORIZED_CHAT = ""
    try:
        _original_handle(message)
    finally:
        bot.AUTHORIZED_CHAT = old


def main():
    if bot.check_config() != 0:
        raise SystemExit(2)
    bot.handle_message = handle_message
    bot.send = safe_send
    print("电报私聊授权适配已启用。", flush=True)
    print("电报中文错误适配已启用。", flush=True)
    bot.poll_forever()


if __name__ == "__main__":
    main()
