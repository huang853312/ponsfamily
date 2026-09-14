#!/usr/bin/env python3
import argparse
import os
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
AUTHORIZED_CHAT = os.getenv("TELEGRAM_CHAT_ID", "").strip()
TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "30"))
API = f"https://api.telegram.org/bot{TOKEN}" if TOKEN else ""

CHAIN_ALIASES = {
    "robinhood": "robinhood",
    "rh": "robinhood",
    "罗宾汉": "robinhood",
    "hyperevm": "hyperevm",
    "hyper": "hyperevm",
    "hype": "hyperevm",
    "bnb": "bnb",
    "bsc": "bnb",
}
CHAIN_ZH = {
    "robinhood": "Robinhood Chain",
    "hyperevm": "HyperEVM",
    "bnb": "BNB Chain",
}
CHAIN_ID = {
    "robinhood": 4663,
    "hyperevm": 999,
    "bnb": 56,
}
CHAIN_RPC = {
    "robinhood": tuple(x for x in (
        os.getenv("ROBINHOOD_RPC_URL", "").strip(),
        "https://robinhood-rpc.publicnode.com",
        "https://rpc.mainnet.chain.robinhood.com",
    ) if x),
    "hyperevm": tuple(x for x in (
        os.getenv("HYPEREVM_RPC_URL", "").strip(),
        "https://rpc.hyperliquid.xyz/evm",
    ) if x),
    "bnb": tuple(x for x in (
        os.getenv("BNB_RPC_URL", "").strip(),
        "https://bsc-rpc.publicnode.com",
        "https://bsc-dataseed.bnbchain.org",
    ) if x),
}
ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")

_busy_lock = threading.Lock()
_busy = {"running": False, "chain": "", "token": "", "started": 0.0}


def api_post(method: str, payload: dict, timeout: int = TIMEOUT):
    r = requests.post(f"{API}/{method}", json=payload, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(data)
    return data.get("result")


def send(text: str, chat_id: str = AUTHORIZED_CHAT):
    if not chat_id:
        return
    for part in split_text(text):
        api_post("sendMessage", {
            "chat_id": chat_id,
            "text": part,
            "disable_web_page_preview": True,
        })


def split_text(text: str, limit: int = 3900):
    if len(text) <= limit:
        return [text]
    parts, current = [], ""
    for line in text.splitlines(True):
        if current and len(current) + len(line) > limit:
            parts.append(current)
            current = ""
        current += line
    if current:
        parts.append(current)
    return parts


def help_text():
    return (
        "🛰 多链钱包雷达\n\n"
        "最简单的用法：\n"
        "直接粘贴代币 CA，系统自动识别 Robinhood / HyperEVM / BNB。\n"
        "例如：\n"
        "0xac46193ff2cb638bcfd02520e124a3e2c9228774\n\n"
        "也可以手动指定链：\n"
        "/查 robinhood 0x合约地址\n"
        "/查 hyperevm 0x合约地址\n"
        "/查 bnb 0x合约地址\n\n"
        "其它命令：\n"
        "/状态  查看当前是否正在分析\n"
        "/链    查看已内置支持的链\n"
        "/帮助  查看本说明\n\n"
        "报告字段全部使用中文；合约地址、钱包地址、交易哈希等链上原始字段保留原样。"
    )


def chain_text():
    return (
        "当前内置支持：\n"
        "✅ Robinhood Chain\n"
        "✅ HyperEVM\n"
        "✅ BNB Chain\n\n"
        "直接粘贴 CA 时会自动在以上三条链识别。\n"
        "底层分析器还支持自定义 EVM 链。"
    )


def status_text():
    with _busy_lock:
        if not _busy["running"]:
            return "✅ 当前空闲，可以直接粘贴代币 CA。"
        elapsed = int(time.time() - _busy["started"])
        chain_name = CHAIN_ZH.get(_busy["chain"], _busy["chain"] or "自动识别中")
        return (
            "⏳ 当前正在分析\n"
            f"链：{chain_name}\n"
            f"合约地址：{_busy['token']}\n"
            f"已运行：{elapsed} 秒"
        )


def parse_query(text: str):
    raw = (text or "").strip()
    if raw.startswith("/查"):
        raw = raw[2:].strip()
    elif raw.lower().startswith("/analyze"):
        raw = raw[len("/analyze"):].strip()
    parts = raw.split()
    if len(parts) == 1 and ADDRESS_RE.match(parts[0]):
        return "auto", parts[0].lower()
    if len(parts) != 2:
        return None
    chain = CHAIN_ALIASES.get(parts[0].lower()) or CHAIN_ALIASES.get(parts[0])
    token = parts[1]
    if not chain or not ADDRESS_RE.match(token):
        return None
    return chain, token.lower()


def rpc_call(url: str, method: str, params: list, timeout: int = 6):
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    r = requests.post(url, json=payload, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, dict) or data.get("error"):
        raise RuntimeError(data.get("error") if isinstance(data, dict) else "RPC 返回异常")
    return data.get("result")


def probe_token_on_chain(chain: str, token: str):
    errors = []
    for url in CHAIN_RPC.get(chain, ()):
        try:
            cid = int(rpc_call(url, "eth_chainId", []), 16)
            if cid != CHAIN_ID[chain]:
                continue
            code = rpc_call(url, "eth_getCode", [token, "latest"])
            if not isinstance(code, str) or code in ("0x", "0x0", ""):
                return None
            total_supply = rpc_call(url, "eth_call", [{"to": token, "data": "0x18160ddd"}, "latest"])
            decimals = rpc_call(url, "eth_call", [{"to": token, "data": "0x313ce567"}, "latest"])
            if not isinstance(total_supply, str) or total_supply in ("0x", ""):
                return None
            if not isinstance(decimals, str) or decimals in ("0x", ""):
                return None
            return chain
        except Exception as exc:
            errors.append(str(exc))
    return None


def detect_chain(token: str):
    matches = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(probe_token_on_chain, chain, token): chain for chain in CHAIN_ZH}
        for future in as_completed(futures):
            try:
                result = future.result()
                if result:
                    matches.append(result)
            except Exception:
                pass
    return sorted(matches)


def run_analysis(chain: str, token: str, chat_id: str):
    try:
        reports = ROOT / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        out = reports / f"{chain}_{token[2:10]}_{stamp}.csv"
        cmd = [
            str(ROOT / ".venv" / "bin" / "python"),
            str(ROOT / "main.py"),
            "analyze",
            "--chain", chain,
            "--token", token,
            "--top", "50",
            "--scan-hours", "24",
            "--funding-minutes", "10",
            "--return-minutes", "10",
            "--max-groups", "5",
            "--output", str(out),
            "--telegram",
        ]
        proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, timeout=1800)
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "未知错误").strip()
            send(
                "❌ 分析失败\n"
                f"链：{CHAIN_ZH.get(chain, chain)}\n"
                f"合约地址：{token}\n"
                f"原因：{err[-1500:]}",
                chat_id,
            )
        else:
            send(
                "✅ 分析任务完成。完整中文报告已发送。\n"
                f"明细文件已保存在服务器：{out.name}",
                chat_id,
            )
    except subprocess.TimeoutExpired:
        send("❌ 分析超过 30 分钟，任务已停止。请缩短扫描范围后再试。", chat_id)
    except Exception as exc:
        send(f"❌ 分析任务异常：{type(exc).__name__}: {exc}", chat_id)
    finally:
        with _busy_lock:
            _busy.update({"running": False, "chain": "", "token": "", "started": 0.0})


def handle_message(message: dict):
    chat = message.get("chat") or {}
    chat_id = str(chat.get("id") or "")
    text = str(message.get("text") or "").strip()
    if not chat_id or not text:
        return

    # 严格只接受配置中的聊天，避免机器人被其他人拿来消耗 RPC。
    if AUTHORIZED_CHAT and chat_id != AUTHORIZED_CHAT:
        return

    command = text.split()[0].split("@")[0].lower()
    if command in {"/start", "/help", "/帮助"}:
        send(help_text(), chat_id)
        return
    if command in {"/状态", "/status"}:
        send(status_text(), chat_id)
        return
    if command in {"/链", "/chains"}:
        send(chain_text(), chat_id)
        return

    query = parse_query(text)
    if not query:
        send("格式不正确。\n\n" + help_text(), chat_id)
        return
    chain, token = query

    with _busy_lock:
        if _busy["running"]:
            send("当前已有分析任务在运行。\n\n" + status_text(), chat_id)
            return
        _busy.update({"running": True, "chain": chain if chain != "auto" else "", "token": token, "started": time.time()})

    if chain == "auto":
        send(
            "🔎 正在自动识别代币所在链\n"
            f"合约地址：{token}\n"
            "正在检查 Robinhood Chain、HyperEVM、BNB Chain……",
            chat_id,
        )
        matches = detect_chain(token)
        if len(matches) == 0:
            with _busy_lock:
                _busy.update({"running": False, "chain": "", "token": "", "started": 0.0})
            send(
                "❌ 在当前三条内置链没有识别到这个 ERC-20 代币。\n"
                "如果你知道所在链，请手动发送：\n"
                "/查 robinhood 0x...\n"
                "/查 hyperevm 0x...\n"
                "/查 bnb 0x...",
                chat_id,
            )
            return
        if len(matches) > 1:
            with _busy_lock:
                _busy.update({"running": False, "chain": "", "token": "", "started": 0.0})
            names = "、".join(CHAIN_ZH[x] for x in matches)
            send(
                f"⚠️ 这个地址在多条链都有代币合约：{names}\n"
                "请手动指定链后再查询。",
                chat_id,
            )
            return
        chain = matches[0]
        with _busy_lock:
            _busy["chain"] = chain
        send(f"✅ 已自动识别：{CHAIN_ZH[chain]}", chat_id)

    send(
        "🔎 已开始链上分析\n"
        f"链：{CHAIN_ZH[chain]}\n"
        f"合约地址：{token}\n\n"
        "系统将检查持币结构、外部账户、共同资金来源、资金源类型、集中买入、集中卖出、资金回流和关联持仓比例。\n"
        "完成后会自动发送中文报告。",
        chat_id,
    )
    threading.Thread(target=run_analysis, args=(chain, token, chat_id), daemon=True).start()


def check_config() -> int:
    if not TOKEN:
        print("缺少 TELEGRAM_BOT_TOKEN")
        return 2
    if not AUTHORIZED_CHAT:
        print("缺少 TELEGRAM_CHAT_ID")
        return 2
    try:
        me = api_post("getMe", {})
        print(f"电报机器人连接正常：@{me.get('username', '')}")
        print(f"授权聊天ID已配置：{AUTHORIZED_CHAT}")
        return 0
    except Exception as exc:
        print(f"电报机器人连接失败：{exc}")
        return 3


def poll_forever():
    if check_config() != 0:
        raise SystemExit(2)
    offset = None
    print("电报交互控制台已启动。", flush=True)
    while True:
        try:
            payload = {"timeout": 25, "allowed_updates": ["message"]}
            if offset is not None:
                payload["offset"] = offset
            updates = api_post("getUpdates", payload, timeout=35) or []
            for update in updates:
                offset = int(update.get("update_id", 0)) + 1
                message = update.get("message")
                if isinstance(message, dict):
                    try:
                        handle_message(message)
                    except Exception as exc:
                        print(f"处理消息失败：{type(exc).__name__}: {exc}", flush=True)
        except requests.RequestException as exc:
            print(f"电报网络异常：{exc}", flush=True)
            time.sleep(3)
        except Exception as exc:
            print(f"电报轮询异常：{type(exc).__name__}: {exc}", flush=True)
            time.sleep(3)


def main():
    p = argparse.ArgumentParser(description="多链钱包雷达电报中文交互控制台")
    p.add_argument("--check", action="store_true", help="只检查电报配置，不启动轮询")
    args = p.parse_args()
    if args.check:
        raise SystemExit(check_config())
    poll_forever()


if __name__ == "__main__":
    main()
