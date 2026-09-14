#!/usr/bin/env python3
"""Telegram 私聊授权、中文错误、低内存分析入口与真实阶段进度适配层。

只扩展交互与执行方式，不改 main.py 的分析口径：
- 配置聊天继续允许；授权群管理员可私聊机器人；
- 电报端隐藏 Python/HTTP 英文堆栈；
- analyze 子进程统一走 main_fast.py；
- 读取 main_fast.py 的真实阶段标记，显示 25%/50%/75%/90%；
- 报告发送回“发起查询的当前聊天”，不会私聊查询却跑到群里；
- 长任务有中文心跳，避免看起来像卡死。
"""
import queue
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
_progress_chat_id = ""


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
        reason = "链上查询出现内部异常。详细技术日志已保存在服务器，电报端不显示英文错误堆栈。"
    else:
        reason = "分析过程中出现链上查询异常。详细技术日志已保存在服务器。"
    if not prefix:
        prefix = "❌ 分析失败"
    return prefix + "\n原因：" + reason


def safe_send(text: str, chat_id: str = ""):
    cleaned = _chinese_error_message(text)
    return _original_send(cleaned, chat_id or bot.AUTHORIZED_CHAT)


def _progress_send(text: str):
    chat_id = _progress_chat_id or bot.AUTHORIZED_CHAT
    if not chat_id:
        return
    try:
        safe_send(text, chat_id)
    except Exception as exc:
        print(f"发送阶段进度失败：{type(exc).__name__}: {exc}", flush=True)


def _extract_report(output: str) -> str:
    marker = "🛰 多链钱包雷达报告"
    if marker not in output:
        return ""
    report = output[output.index(marker):]
    for end_marker in ("\n明细文件：", "\n实际扫描起始区块：", "\n本次实际使用 RPC："):
        if end_marker in report:
            report = report.split(end_marker, 1)[0]
    return report.strip()


def _send_stage_marker(text: str, sent: set):
    if text.startswith("进度标记：25") and 25 not in sent:
        _progress_send(
            "✅ 已完成 25%：历史转账读取完成。\n"
            "📊 正在重建持币结构、识别外部账户和主动买卖……"
        )
        sent.add(25)
    elif text.startswith("进度标记：50") and 50 not in sent:
        _progress_send(
            "✅ 已完成 50%：持币结构、外部账户和主动买卖初筛完成。\n"
            "🔗 正在分析共同资金来源……"
        )
        sent.add(50)
    elif text.startswith("进度标记：75") and 75 not in sent:
        _progress_send(
            "✅ 已完成 75%：共同资金来源分析完成。\n"
            "↩️ 正在检查卖出后资金回流……"
        )
        sent.add(75)
    elif text.startswith("进度标记：90") and 90 not in sent:
        _progress_send(
            "✅ 已完成 90%：资金回流检查完成。\n"
            "🧩 正在汇总关联持仓比例、同步行为和综合风险……"
        )
        sent.add(90)


def low_memory_subprocess_run(cmd, *args, **kwargs):
    """analyze 统一走 main_fast.py，并把真实阶段输出实时发到当前查询聊天。"""
    if not (isinstance(cmd, (list, tuple)) and "analyze" in cmd):
        return _original_subprocess_run(cmd, *args, **kwargs)

    rewritten = list(cmd)
    for i, item in enumerate(rewritten):
        if str(item).endswith("/main.py") or str(item) == "main.py":
            fast = ROOT / "main_fast.py"
            if not fast.exists():
                raise RuntimeError("低内存分析入口 main_fast.py 不存在，拒绝回退到重扫描模式")
            rewritten[i] = str(fast)
            break

    # 子进程不直接发到固定 TELEGRAM_CHAT_ID；由这里把报告发回真正的请求聊天。
    rewritten = [item for item in rewritten if str(item) != "--telegram"]

    timeout = kwargs.get("timeout")
    cwd = kwargs.get("cwd")
    env = kwargs.get("env")
    started = time.time()
    lines = []
    sent = set()

    _progress_send("📊 正在读取持币结构……\n正在读取真实链上历史数据，请稍候。")

    proc = bot.subprocess.Popen(
        rewritten,
        cwd=cwd,
        env=env,
        text=True,
        stdout=bot.subprocess.PIPE,
        stderr=bot.subprocess.STDOUT,
        bufsize=1,
    )
    q = queue.Queue()

    def reader():
        try:
            for line in proc.stdout:
                q.put(line)
        finally:
            q.put(None)

    threading.Thread(target=reader, daemon=True).start()
    reader_done = False

    while True:
        if timeout is not None and time.time() - started > float(timeout):
            proc.kill()
            proc.wait()
            output = "".join(lines)
            raise bot.subprocess.TimeoutExpired(rewritten, timeout, output=output)

        try:
            item = q.get(timeout=0.5)
        except queue.Empty:
            item = "__NO_LINE__"

        if item is None:
            reader_done = True
        elif item != "__NO_LINE__":
            lines.append(item)
            text = item.strip()
            print(text, flush=True)
            _send_stage_marker(text, sent)

        if proc.poll() is not None and reader_done and q.empty():
            break

    rc = proc.wait()
    output = "".join(lines)
    if rc == 0:
        report = _extract_report(output)
        if report:
            _progress_send(report)

    return bot.subprocess.CompletedProcess(
        rewritten,
        rc,
        stdout=output,
        stderr="" if rc == 0 else output,
    )


def _heartbeat(chat_id: str, started: float):
    time.sleep(45)
    count = 0
    while count < 20:
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
                "系统仍在读取真实链上数据，没有卡死；阶段变化会自动提示。",
                chat_id,
            )
        except Exception as exc:
            print(f"发送分析进度失败：{type(exc).__name__}: {exc}", flush=True)
        count += 1
        time.sleep(90)


def handle_message(message: dict):
    global _progress_chat_id
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
    if chat_id:
        _progress_chat_id = chat_id

    with bot._busy_lock:
        was_running = bool(bot._busy.get("running"))

    # 原处理器内部还有固定 chat_id 检查；仅在本次已授权调用期间关闭。
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
    print("真实阶段中文进度提示已启用。", flush=True)
    print("查询报告回传当前聊天已启用。", flush=True)
    bot.poll_forever()


if __name__ == "__main__":
    main()
