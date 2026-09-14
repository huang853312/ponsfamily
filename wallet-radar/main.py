#!/usr/bin/env python3
import argparse
import csv
import json
import os
import statistics
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import requests
from dotenv import load_dotenv

load_dotenv()

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
ZERO = "0x0000000000000000000000000000000000000000"
DEAD = "0x000000000000000000000000000000000000dead"
TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "30"))
DEFAULT_BATCH = int(os.getenv("RPC_BATCH_SIZE", "50"))


@dataclass(frozen=True)
class ChainConfig:
    key: str
    name_zh: str
    chain_id: int
    native_symbol: str
    rpc_urls: Tuple[str, ...]
    avg_block_time: float
    log_chunk: int


CHAINS = {
    "robinhood": ChainConfig(
        "robinhood", "Robinhood Chain", 4663, "ETH",
        tuple(x for x in (
            os.getenv("ROBINHOOD_RPC_URL", "").strip(),
            "https://rpc.mainnet.chain.robinhood.com",
            "https://robinhood-rpc.publicnode.com",
        ) if x),
        float(os.getenv("ROBINHOOD_BLOCK_TIME", "0.10")),
        int(os.getenv("ROBINHOOD_LOG_CHUNK", "2000")),
    ),
    "hyperevm": ChainConfig(
        "hyperevm", "HyperEVM", 999, "HYPE",
        tuple(x for x in (
            os.getenv("HYPEREVM_RPC_URL", "").strip(),
            "https://rpc.hyperliquid.xyz/evm",
        ) if x),
        float(os.getenv("HYPEREVM_BLOCK_TIME", "1.0")),
        int(os.getenv("HYPEREVM_LOG_CHUNK", "50")),
    ),
    "bnb": ChainConfig(
        "bnb", "BNB Chain", 56, "BNB",
        tuple(x for x in (
            os.getenv("BNB_RPC_URL", "").strip(),
            # NodeReal 官方文档提供的公开共享 Key。实测支持 BSC 深历史 eth_getLogs。
            "https://bsc-mainnet.nodereal.io/v1/f8728a3265504b998a2f09c83493d76a",
            "https://bsc-mainnet.nodereal.io/v1/64a9df0874fb4a93b9d0a3849de012d3",
            # PublicNode 仅作近端备用；深历史请求会要求个人归档令牌。
            "https://bsc-rpc.publicnode.com",
        ) if x),
        float(os.getenv("BNB_BLOCK_TIME", "0.45")),
        int(os.getenv("BNB_LOG_CHUNK", "500")),
    ),
}


def norm(v: Optional[str]) -> str:
    return (v or "").lower()


def valid_address(v: str) -> bool:
    if not isinstance(v, str) or len(v) != 42 or not v.startswith("0x"):
        return False
    try:
        int(v[2:], 16)
        return True
    except ValueError:
        return False


def qhex(n: int) -> str:
    return hex(int(n))


def h2i(v) -> int:
    if v in (None, "", "0x"):
        return 0
    if isinstance(v, int):
        return v
    return int(str(v), 16)


def utc_text(ts: int) -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


class RpcError(RuntimeError):
    pass


class RpcClient:
    def __init__(self, chain: ChainConfig, explicit_url: Optional[str] = None):
        self.chain = chain
        # 去重并保持优先级，避免环境变量与内置地址重复。
        raw_urls = (explicit_url,) if explicit_url else chain.rpc_urls
        self.urls = tuple(dict.fromkeys(x for x in raw_urls if x))
        self.url = ""
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "WalletRadar/2.1",
            "Accept": "application/json",
            "Content-Type": "application/json",
        })
        self._next_id = 1
        self._select_endpoint()

    def _post(self, url: str, payload):
        last = None
        for attempt in range(4):
            try:
                r = self.session.post(url, json=payload, timeout=TIMEOUT)
                r.raise_for_status()
                return r.json()
            except Exception as exc:
                last = exc
                if attempt < 3:
                    time.sleep(0.5 * (attempt + 1))
        raise RpcError(f"RPC 请求失败: {url}: {last}")

    def _single_on(self, url: str, method: str, params: list):
        rid = self._next_id
        self._next_id += 1
        data = self._post(url, {"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        if isinstance(data, dict) and data.get("error"):
            raise RpcError(f"{method}: {data['error']}")
        if not isinstance(data, dict):
            raise RpcError(f"{method}: 返回格式异常")
        return data.get("result")

    def _select_endpoint(self):
        errors = []
        for url in self.urls:
            try:
                cid = h2i(self._single_on(url, "eth_chainId", []))
                if cid != self.chain.chain_id:
                    errors.append(f"{url}: 链ID={cid}")
                    continue
                latest = h2i(self._single_on(url, "eth_blockNumber", []))
                # BNB 必须探测真正的历史日志能力，不能只测最新 2 个区块。
                # PublicNode 在近端可用，但深一点的 eth_getLogs 会 403 要求 Archive Token。
                if self.chain.key == "bnb":
                    probe_end = max(0, latest - 10000)
                    probe_start = max(0, probe_end - 49)
                else:
                    probe_end = latest
                    probe_start = max(0, latest - 2)
                self._single_on(url, "eth_getLogs", [{
                    "fromBlock": qhex(probe_start),
                    "toBlock": qhex(probe_end),
                    "address": DEAD,
                }])
                self.url = url
                return
            except Exception as exc:
                errors.append(f"{url}: {exc}")
        raise RpcError(f"{self.chain.name_zh} 没有可用 RPC。{' | '.join(errors)}")

    def call(self, method: str, params: list):
        # 当前端点临时限流/故障时自动切换备用端点；不把单一免费 RPC 当成唯一依赖。
        errors = []
        ordered = [self.url] + [u for u in self.urls if u != self.url]
        for url in ordered:
            try:
                result = self._single_on(url, method, params)
                self.url = url
                return result
            except Exception as exc:
                errors.append(f"{url}: {exc}")
        raise RpcError(f"{method} 所有 RPC 均失败。{' | '.join(errors)}")

    def batch(self, calls: Sequence[Tuple[str, list]], batch_size: int = DEFAULT_BATCH) -> List:
        results = []
        for pos in range(0, len(calls), batch_size):
            part = calls[pos:pos + batch_size]
            payload = []
            order = []
            for method, params in part:
                rid = self._next_id
                self._next_id += 1
                order.append(rid)
                payload.append({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
            last_error = None
            data = None
            ordered = [self.url] + [u for u in self.urls if u != self.url]
            for url in ordered:
                try:
                    data = self._post(url, payload)
                    if not isinstance(data, list):
                        raise RpcError("批量 RPC 返回格式异常")
                    self.url = url
                    break
                except Exception as exc:
                    last_error = exc
                    data = None
            if data is None:
                raise RpcError(f"批量 RPC 所有端点均失败：{last_error}")
            by_id = {x.get("id"): x for x in data if isinstance(x, dict)}
            for rid in order:
                item = by_id.get(rid)
                if not item:
                    results.append(None)
                elif item.get("error"):
                    results.append({"__error__": item["error"]})
                else:
                    results.append(item.get("result"))
        return results

    def latest_block(self) -> int:
        return h2i(self.call("eth_blockNumber", []))

    def get_logs(self, address: str, start: int, end: int, topics: Optional[list] = None) -> List[dict]:
        params = {"fromBlock": qhex(start), "toBlock": qhex(end), "address": address}
        if topics is not None:
            params["topics"] = topics
        try:
            return self.call("eth_getLogs", [params]) or []
        except Exception:
            if start >= end:
                raise
            mid = (start + end) // 2
            return self.get_logs(address, start, mid, topics) + self.get_logs(address, mid + 1, end, topics)


def resolve_chain(args) -> ChainConfig:
    if args.chain != "custom":
        return CHAINS[args.chain]
    if not args.rpc_url or not args.chain_id:
        raise SystemExit("自定义 EVM 链必须提供 --rpc-url 和 --chain-id")
    return ChainConfig(
        "custom", args.chain_name or f"自定义 EVM 链 {args.chain_id}", int(args.chain_id),
        args.native_symbol or "原生币", (args.rpc_url,), float(args.block_time or 2.0), int(args.log_chunk or 500)
    )


def decode_abi_string(data: str) -> str:
    if not data or data == "0x":
        return ""
    raw = bytes.fromhex(data[2:])
    try:
        if len(raw) >= 64:
            offset = int.from_bytes(raw[:32], "big")
            if offset + 32 <= len(raw):
                ln = int.from_bytes(raw[offset:offset + 32], "big")
                return raw[offset + 32:offset + 32 + ln].decode("utf-8", errors="replace").strip("\x00")
        if len(raw) >= 32:
            return raw[:32].rstrip(b"\x00").decode("utf-8", errors="replace")
    except Exception:
        pass
    return ""


def eth_call(client: RpcClient, to: str, data: str) -> str:
    return client.call("eth_call", [{"to": to, "data": data}, "latest"]) or "0x"


def token_metadata(client: RpcClient, token: str) -> dict:
    out = {"name": "", "symbol": "", "decimals": 18, "total_supply": 0}
    try:
        out["name"] = decode_abi_string(eth_call(client, token, "0x06fdde03"))
    except Exception:
        pass
    try:
        out["symbol"] = decode_abi_string(eth_call(client, token, "0x95d89b41"))
    except Exception:
        pass
    try:
        out["decimals"] = h2i(eth_call(client, token, "0x313ce567"))
    except Exception:
        pass
    try:
        out["total_supply"] = h2i(eth_call(client, token, "0x18160ddd"))
    except Exception:
        pass
    return out


def parse_transfer(log: dict) -> Optional[dict]:
    topics = log.get("topics") or []
    if len(topics) < 3 or norm(topics[0]) != TRANSFER_TOPIC:
        return None
    try:
        return {
            "from": norm("0x" + topics[1][-40:]),
            "to": norm("0x" + topics[2][-40:]),
            "value": h2i(log.get("data")),
            "block": h2i(log.get("blockNumber")),
            "tx_hash": log.get("transactionHash") or "",
            "log_index": h2i(log.get("logIndex")),
        }
    except Exception:
        return None


def scan_transfer_history(client: RpcClient, token: str, scan_hours: float, start_block: Optional[int] = None):
    latest = client.latest_block()
    if start_block is not None:
        lower = max(0, int(start_block))
    else:
        estimated = int((scan_hours * 3600) / max(client.chain.avg_block_time, 0.05))
        lower = max(0, latest - estimated)
    end = latest
    chunk = max(1, client.chain.log_chunk)
    raw_logs: List[dict] = []
    saw_mint = False
    quiet_after_mint = 0
    quiet_needed = 12
    reached_lower = False

    while end >= lower:
        start = max(lower, end - chunk + 1)
        logs = client.get_logs(token, start, end, [TRANSFER_TOPIC])
        if logs:
            raw_logs.extend(logs)
            transfers_here = [x for x in (parse_transfer(l) for l in logs) if x]
            if any(x["from"] == ZERO for x in transfers_here):
                saw_mint = True
                quiet_after_mint = 0
            elif saw_mint:
                quiet_after_mint = 0
        elif saw_mint:
            quiet_after_mint += 1
            if quiet_after_mint >= quiet_needed:
                break
        if start == lower:
            reached_lower = True
            break
        end = start - 1

    transfers = [x for x in (parse_transfer(l) for l in raw_logs) if x]
    uniq = {(x["tx_hash"], x["log_index"]): x for x in transfers}
    transfers = sorted(uniq.values(), key=lambda x: (x["block"], x["log_index"]))
    actual_start = transfers[0]["block"] if transfers else lower
    complete = bool(transfers) and (reached_lower or (saw_mint and quiet_after_mint >= quiet_needed))
    note = "已覆盖初始铸币附近历史" if complete else f"仅保证最近约 {scan_hours:g} 小时/指定区间；老币请扩大扫描范围或指定创建区块"
    if not transfers:
        note = "扫描范围内未找到 ERC-20 Transfer 事件"
    return transfers, actual_start, latest, complete, note


def reconstruct_balances(transfers: List[dict]) -> Dict[str, int]:
    balances = defaultdict(int)
    for t in transfers:
        if t["from"] not in (ZERO, DEAD):
            balances[t["from"]] -= t["value"]
        if t["to"] not in (ZERO, DEAD):
            balances[t["to"]] += t["value"]
    return {a: v for a, v in balances.items() if v > 0}


def classify_eoas(client: RpcClient, addresses: List[str]) -> Dict[str, bool]:
    if not addresses:
        return {}
    vals = client.batch([("eth_getCode", [a, "latest"]) for a in addresses], batch_size=min(DEFAULT_BATCH, 50))
    return {a: isinstance(code, str) and code in ("0x", "0x0") for a, code in zip(addresses, vals)}


def get_transactions(client: RpcClient, hashes: List[str]) -> Dict[str, dict]:
    unique = list(dict.fromkeys(h for h in hashes if h))
    if not unique:
        return {}
    vals = client.batch([("eth_getTransactionByHash", [h]) for h in unique])
    return {h: v for h, v in zip(unique, vals) if isinstance(v, dict) and "__error__" not in v}


def get_block_timestamps(client: RpcClient, blocks: Iterable[int]) -> Dict[int, int]:
    unique = sorted(set(int(b) for b in blocks))
    if not unique:
        return {}
    vals = client.batch([("eth_getBlockByNumber", [qhex(b), False]) for b in unique])
    out = {}
    for b, v in zip(unique, vals):
        if isinstance(v, dict) and "__error__" not in v:
            out[b] = h2i(v.get("timestamp"))
    return out


def active_trade_times(client: RpcClient, transfers: List[dict], eoa_addresses: List[str], max_events_per_side: int = 20):
    wanted = set(eoa_addresses)
    incoming = defaultdict(list)
    outgoing = defaultdict(list)
    for t in transfers:
        if t["to"] in wanted:
            incoming[t["to"]].append(t)
        if t["from"] in wanted:
            outgoing[t["from"]].append(t)

    candidate_hashes = []
    for a in wanted:
        candidate_hashes += [x["tx_hash"] for x in incoming[a][:max_events_per_side]]
        candidate_hashes += [x["tx_hash"] for x in outgoing[a][:max_events_per_side]]
    txs = get_transactions(client, candidate_hashes)

    targets = sorted({norm(tx.get("to")) for tx in txs.values() if valid_address(norm(tx.get("to")))})
    target_is_eoa = classify_eoas(client, targets)
    buys, sells = {}, {}
    for a in wanted:
        for ev in incoming[a][:max_events_per_side]:
            tx = txs.get(ev["tx_hash"]) or {}
            to = norm(tx.get("to"))
            if norm(tx.get("from")) == a and to and target_is_eoa.get(to) is False:
                buys[a] = {"block": ev["block"], "tx_hash": ev["tx_hash"], "value": ev["value"]}
                break
        for ev in outgoing[a][:max_events_per_side]:
            tx = txs.get(ev["tx_hash"]) or {}
            to = norm(tx.get("to"))
            if norm(tx.get("from")) == a and to and target_is_eoa.get(to) is False:
                sells[a] = {"block": ev["block"], "tx_hash": ev["tx_hash"], "value": ev["value"]}
                break

    ts = get_block_timestamps(client, [v["block"] for v in buys.values()] + [v["block"] for v in sells.values()])
    for v in buys.values():
        v["timestamp"] = ts.get(v["block"], 0)
    for v in sells.values():
        v["timestamp"] = ts.get(v["block"], 0)
    return buys, sells


def merge_intervals(intervals: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    if not intervals:
        return []
    items = sorted((max(0, a), max(0, b)) for a, b in intervals if b >= a)
    merged = [list(items[0])]
    for a, b in items[1:]:
        if a <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged]


def scan_full_blocks(client: RpcClient, intervals: List[Tuple[int, int]], batch_size: int = 40) -> List[dict]:
    blocks = []
    for a, b in merge_intervals(intervals):
        n = a
        while n <= b:
            nums = list(range(n, min(b, n + batch_size - 1) + 1))
            vals = client.batch([("eth_getBlockByNumber", [qhex(x), True]) for x in nums], batch_size=batch_size)
            for block in vals:
                if isinstance(block, dict) and "__error__" not in block:
                    blocks.append(block)
            n = nums[-1] + 1
    return blocks


def find_prefunders(client: RpcClient, buys: Dict[str, dict], funding_minutes: float):
    if not buys:
        return {}, []
    lookback = max(1, int((funding_minutes * 60) / max(client.chain.avg_block_time, 0.05)))
    blocks = scan_full_blocks(client, [(v["block"] - lookback, v["block"]) for v in buys.values()])
    candidates = set(buys)
    incoming = defaultdict(list)
    all_txs = []
    for block in blocks:
        bn = h2i(block.get("number"))
        ts = h2i(block.get("timestamp"))
        for tx in block.get("transactions") or []:
            if not isinstance(tx, dict):
                continue
            tx = dict(tx)
            tx["_block"] = bn
            tx["_timestamp"] = ts
            all_txs.append(tx)
            to = norm(tx.get("to"))
            fr = norm(tx.get("from"))
            if to in candidates and fr and fr != to and h2i(tx.get("value")) > 0 and bn <= buys[to]["block"]:
                incoming[to].append(tx)

    prefunders = {}
    for wallet, txs in incoming.items():
        txs.sort(key=lambda x: (x["_block"], h2i(x.get("transactionIndex"))))
        tx = txs[-1]
        prefunders[wallet] = {
            "funder": norm(tx.get("from")), "value": h2i(tx.get("value")),
            "block": tx["_block"], "timestamp": tx["_timestamp"], "tx_hash": tx.get("hash") or "",
        }
    return prefunders, all_txs


def find_direct_returns(client: RpcClient, sells: Dict[str, dict], prefunders: Dict[str, dict], return_minutes: float):
    intervals = []
    for wallet, sell in sells.items():
        if wallet in prefunders:
            span = max(1, int((return_minutes * 60) / max(client.chain.avg_block_time, 0.05)))
            intervals.append((sell["block"], sell["block"] + span))
    if not intervals:
        return {}
    blocks = scan_full_blocks(client, intervals)
    hits = {}
    for block in blocks:
        ts = h2i(block.get("timestamp"))
        bn = h2i(block.get("number"))
        for tx in block.get("transactions") or []:
            if not isinstance(tx, dict):
                continue
            fr, to = norm(tx.get("from")), norm(tx.get("to"))
            if fr in prefunders and to == prefunders[fr]["funder"] and h2i(tx.get("value")) > 0:
                hits.setdefault(fr, {"to": to, "value": h2i(tx.get("value")), "block": bn, "timestamp": ts, "tx_hash": tx.get("hash") or ""})
    return hits


def tight_count(times: List[int], window_seconds: int) -> Tuple[int, int]:
    values = sorted(t for t in times if t)
    if not values:
        return 0, 0
    best, best_span, left = 1, 0, 0
    for right, value in enumerate(values):
        while value - values[left] > window_seconds:
            left += 1
        count = right - left + 1
        if count > best:
            best, best_span = count, value - values[left]
    return best, best_span


def amount_similarity(items: List[int], tolerance: float = 0.15) -> int:
    vals = [v for v in items if v > 0]
    if not vals:
        return 0
    med = statistics.median(vals)
    return sum(1 for v in vals if med > 0 and abs(v - med) / med <= tolerance)


def load_labels() -> dict:
    path = os.getenv("WALLET_LABELS_FILE", os.path.join(os.path.dirname(__file__), "labels.json"))
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {norm(k): v for k, v in data.items()}
    except Exception:
        return {}


def funder_identity(client: RpcClient, funder: str, group_wallets: List[str], all_txs: List[dict], prefunders: Dict[str, dict], buys: Dict[str, dict]):
    labels = load_labels()
    if funder in labels:
        item = labels[funder]
        label = item.get("label") if isinstance(item, dict) else str(item)
        kind = item.get("type", "已标记地址") if isinstance(item, dict) else "已标记地址"
        return {"type": kind, "label": label, "service_score": 100 if "交易所" in kind else 0, "coord_score": 0}

    try:
        funder_eoa = classify_eoas(client, [funder]).get(funder, False)
    except Exception:
        funder_eoa = False
    outgoing = [tx for tx in all_txs if norm(tx.get("from")) == funder and h2i(tx.get("value")) > 0]
    recipients = {norm(tx.get("to")) for tx in outgoing if valid_address(norm(tx.get("to")))}
    ratio = len(recipients & set(group_wallets)) / max(1, len(recipients))
    values = [prefunders[w]["value"] for w in group_wallets if w in prefunders]
    similar = amount_similarity(values)
    buy_times = [buys[w].get("timestamp", 0) for w in group_wallets if w in buys]
    buy_tight, _ = tight_count(buy_times, 120)

    service_score = 0
    service_score += 25 if len(recipients) >= 50 else 0
    service_score += 25 if len(recipients) >= 200 else 0
    service_score += 20 if len(outgoing) >= 300 else 0
    service_score += 20 if len(recipients) >= 50 and ratio < 0.05 else 0
    service_score += 10 if not funder_eoa else 0

    coord_score = 10 if funder_eoa else 0
    coord_score += 20 if len(group_wallets) >= 5 else 0
    coord_score += 10 if len(group_wallets) >= 10 else 0
    coord_score += 20 if ratio >= 0.20 else 0
    coord_score += 10 if ratio >= 0.50 else 0
    coord_score += 15 if values and similar / len(values) >= 0.70 else 0
    coord_score += 15 if buy_times and buy_tight / len(buy_times) >= 0.70 else 0

    if service_score >= 70:
        kind = "疑似交易所/服务型资金地址"
    elif coord_score >= 70:
        kind = "高概率私人或团队协调资金地址"
    elif not funder_eoa:
        kind = "合约资金地址（身份未确认）"
    else:
        kind = "普通外部账户（身份未确认）"
    return {
        "type": kind, "label": "", "service_score": min(100, service_score),
        "coord_score": min(100, coord_score), "unique_recipients": len(recipients),
        "outgoing_count": len(outgoing), "related_ratio": ratio,
    }


def write_csv(path: str, rows: List[dict]):
    fields = ["排名", "钱包地址", "当前持仓占比", "是否外部账户", "首次主动买入时间", "首次主动卖出时间", "买入前最近资金来源", "资金到账时间", "资金金额_原始单位", "卖出后是否直接回流"]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def split_telegram(text: str, limit: int = 3900) -> List[str]:
    if len(text) <= limit:
        return [text]
    parts, current = [], ""
    for line in text.splitlines(True):
        if len(current) + len(line) > limit and current:
            parts.append(current)
            current = ""
        current += line
    if current:
        parts.append(current)
    return parts


def send_telegram(text: str):
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        raise RuntimeError("未配置 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for part in split_telegram(text):
        r = requests.post(url, json={"chat_id": chat_id, "text": part, "disable_web_page_preview": True}, timeout=TIMEOUT)
        r.raise_for_status()


def build_report(chain: ChainConfig, token: str, meta: dict, scan_note: str, rows: List[dict], groups: List[dict]) -> str:
    symbol = meta.get("symbol") or "未知"
    lines = [
        "🛰 多链钱包雷达报告",
        f"链：{chain.name_zh}",
        f"代币：{symbol}",
        f"合约地址：{token}",
        f"历史覆盖：{scan_note}",
        f"已分析持币钱包：{len(rows)}",
        "",
    ]
    if not groups:
        lines += [
            "共同资金来源：未发现达到阈值的共同资金组",
            "综合判断：当前扫描范围内，没有形成足够强的共同资金来源证据。",
            "提示：这不等于不存在关联钱包；可扩大历史扫描范围后再次确认。",
        ]
        return "\n".join(lines)

    for i, g in enumerate(groups, 1):
        ident = g["identity"]
        lines += [
            f"【关联资金组 {i}】",
            f"共同资金来源：{g['funder']}",
            f"资金来源类型：{ident['type']}",
        ]
        if ident.get("label"):
            lines.append(f"已知标签：{ident['label']}")
        lines += [
            f"交易所/服务地址倾向：{ident.get('service_score', 0)}%",
            f"协调控制倾向：{ident.get('coord_score', 0)}%",
            f"获得资金的钱包：{g['funded_count']} 个",
            f"检测到主动买入的钱包：{g['buy_count']} 个",
            f"2 分钟内集中买入：{g['buy_tight_count']} 个（跨度 {g['buy_span']} 秒）",
            f"资金金额相似的钱包：{g['similar_funding_count']} 个",
            f"检测到主动卖出的钱包：{g['sell_count']} 个",
            f"5 分钟内集中卖出：{g['sell_tight_count']} 个（跨度 {g['sell_span']} 秒）",
            f"卖出后直接回流共同资金源：{g['return_count']} 个",
            f"当前关联持仓占总供应量：{g['supply_pct']:.4f}%",
            f"综合风险：{g['risk']}",
            "",
        ]
    lines += [
        "说明：共同资金来源只是关系证据，不单独等于同一实际控制人。",
        "系统会结合资金来源类型、买入/卖出时间同步、资金金额相似度和资金回流综合判断。",
    ]
    return "\n".join(lines)


def analyze(args) -> int:
    chain = resolve_chain(args)
    token = norm(args.token)
    if not valid_address(token):
        raise SystemExit("代币合约地址格式错误")
    client = RpcClient(chain, args.rpc_url if args.chain == "custom" else None)
    meta = token_metadata(client, token)
    transfers, actual_start, latest, complete, scan_note = scan_transfer_history(client, token, args.scan_hours, args.start_block)
    if not transfers:
        print(f"未找到 Transfer 事件。{scan_note}")
        return 3

    balances = reconstruct_balances(transfers)
    total_supply = int(meta.get("total_supply") or 0)
    sorted_holders = sorted(balances.items(), key=lambda kv: kv[1], reverse=True)[:args.top]
    holder_addresses = [a for a, _ in sorted_holders]
    eoa_map = classify_eoas(client, holder_addresses)
    eoa_addresses = [a for a in holder_addresses if eoa_map.get(a)]
    buys, sells = active_trade_times(client, transfers, eoa_addresses)
    prefunders, funding_window_txs = find_prefunders(client, buys, args.funding_minutes)
    returns = find_direct_returns(client, sells, prefunders, args.return_minutes)

    groups_by_funder = defaultdict(list)
    for wallet, item in prefunders.items():
        groups_by_funder[item["funder"]].append(wallet)

    groups = []
    for funder, wallets in sorted(groups_by_funder.items(), key=lambda kv: len(kv[1]), reverse=True):
        if len(wallets) < args.min_common_funder:
            continue
        buy_times = [buys[w].get("timestamp", 0) for w in wallets if w in buys]
        sell_times = [sells[w].get("timestamp", 0) for w in wallets if w in sells]
        buy_tight, buy_span = tight_count(buy_times, 120)
        sell_tight, sell_span = tight_count(sell_times, 300)
        similar = amount_similarity([prefunders[w]["value"] for w in wallets if w in prefunders])
        supply = sum(balances.get(w, 0) for w in wallets)
        supply_pct = (Decimal(supply) * Decimal(100) / Decimal(total_supply)) if total_supply else Decimal(0)
        identity = funder_identity(client, funder, wallets, funding_window_txs, prefunders, buys)
        return_count = sum(1 for w in wallets if w in returns)
        strength = 0
        strength += 25 if len(wallets) >= 5 else 10
        strength += 25 if buy_times and buy_tight / len(buy_times) >= 0.70 else 0
        strength += 15 if wallets and similar / len(wallets) >= 0.70 else 0
        strength += 15 if sell_times and sell_tight / len(sell_times) >= 0.60 else 0
        strength += 20 if return_count >= max(2, len(wallets) // 3) else 0
        if identity.get("service_score", 0) >= 70:
            strength = max(0, strength - 35)
        risk = "🔴 高" if strength >= 65 else ("🟠 中" if strength >= 35 else "🟢 低")
        groups.append({
            "funder": funder, "wallets": wallets, "funded_count": len(wallets),
            "buy_count": len(buy_times), "buy_tight_count": buy_tight, "buy_span": buy_span,
            "similar_funding_count": similar, "sell_count": len(sell_times),
            "sell_tight_count": sell_tight, "sell_span": sell_span,
            "return_count": return_count, "supply_pct": supply_pct,
            "identity": identity, "risk": risk, "strength": strength,
        })
    groups.sort(key=lambda g: (g["strength"], g["funded_count"], g["supply_pct"]), reverse=True)

    rows = []
    for rank, (wallet, bal) in enumerate(sorted_holders, 1):
        buy = buys.get(wallet) or {}
        sell = sells.get(wallet) or {}
        fund = prefunders.get(wallet) or {}
        pct = (Decimal(bal) * Decimal(100) / Decimal(total_supply)) if total_supply else Decimal(0)
        rows.append({
            "排名": rank,
            "钱包地址": wallet,
            "当前持仓占比": f"{pct:.6f}%",
            "是否外部账户": "是" if eoa_map.get(wallet) else "否",
            "首次主动买入时间": utc_text(buy.get("timestamp", 0)),
            "首次主动卖出时间": utc_text(sell.get("timestamp", 0)),
            "买入前最近资金来源": fund.get("funder", ""),
            "资金到账时间": utc_text(fund.get("timestamp", 0)),
            "资金金额_原始单位": str(fund.get("value", "")),
            "卖出后是否直接回流": "是" if wallet in returns else "否",
        })

    write_csv(args.output, rows)
    report = build_report(chain, token, meta, scan_note, rows, groups[:args.max_groups])
    print(report)
    print(f"\n明细文件：{args.output}")
    print(f"实际扫描起始区块：{actual_start}；最新区块：{latest}；完整标记：{'是' if complete else '否'}")
    print(f"本次实际使用 RPC：{client.url}")
    if args.telegram:
        send_telegram(report)
        print("中文电报报告已发送。")
    return 0


def selftest(args) -> int:
    keys = list(CHAINS) if args.all else [args.chain]
    failed = False
    for key in keys:
        cfg = CHAINS[key]
        try:
            client = RpcClient(cfg)
            latest = client.latest_block()
            logs = client.get_logs(DEAD, max(0, latest - 2), latest)
            code = client.call("eth_getCode", [DEAD, "latest"])
            print(f"✅ {cfg.name_zh}：链ID={cfg.chain_id}，最新区块={latest}，RPC={client.url}，日志查询=可用，合约判断=可用")
        except Exception as exc:
            failed = True
            print(f"❌ {cfg.name_zh}：{exc}")
    return 1 if failed else 0


def parser():
    p = argparse.ArgumentParser(description="多链钱包雷达：CA → 持仓 → 外部账户 → 共同资金来源 → 同步买卖 → 资金回流 → 中文风险报告")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("analyze", help="分析一个 EVM 代币的钱包关系")
    a.add_argument("--chain", required=True, choices=["robinhood", "hyperevm", "bnb", "custom"])
    a.add_argument("--token", required=True)
    a.add_argument("--top", type=int, default=50)
    a.add_argument("--scan-hours", type=float, default=24.0)
    a.add_argument("--start-block", type=int)
    a.add_argument("--funding-minutes", type=float, default=10.0)
    a.add_argument("--return-minutes", type=float, default=10.0)
    a.add_argument("--min-common-funder", type=int, default=2)
    a.add_argument("--max-groups", type=int, default=5)
    a.add_argument("--output", default="钱包雷达报告.csv")
    a.add_argument("--telegram", action="store_true")
    a.add_argument("--rpc-url")
    a.add_argument("--chain-id", type=int)
    a.add_argument("--chain-name")
    a.add_argument("--native-symbol")
    a.add_argument("--block-time", type=float)
    a.add_argument("--log-chunk", type=int)

    s = sub.add_parser("selftest", help="测试链 RPC 是否可用")
    s.add_argument("--chain", choices=list(CHAINS), default="robinhood")
    s.add_argument("--all", action="store_true")
    return p


def main():
    p = parser()
    args = p.parse_args()
    if args.cmd == "analyze":
        return analyze(args)
    if args.cmd == "selftest":
        return selftest(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
