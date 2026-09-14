#!/usr/bin/env python3
"""Telegram 私聊授权适配层。

保留 telegram_bot.py 的分析/中文回复逻辑，只扩展授权：
- 原先配置的 TELEGRAM_CHAT_ID 继续直接允许；
- 如果用户在机器人私聊中操作，并且该用户是已配置群组/频道的管理员，也允许。
这样不会把 RPC 查询开放给陌生人。
"""
import time

import telegram_bot as bot

CONFIGURED_CHAT = bot.AUTHORIZED_CHAT
_original_handle = bot.handle_message
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

    # 原处理器内部还有旧的单 chat_id 检查；仅在本次已授权调用期间关闭，
    # 其余逻辑（命令、任务锁、中文回复、分析）全部沿用原实现。
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
    print("电报私聊授权适配已启用。", flush=True)
    bot.poll_forever()


if __name__ == "__main__":
    main()
