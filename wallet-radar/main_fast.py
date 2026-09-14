#!/usr/bin/env python3
"""Wallet Radar 轻量执行入口。

不改变 main.py 的分析口径，只替换最容易把小服务器拖死的两段：
1. 买入前原生币资金来源扫描；
2. 卖出后资金回流扫描。

旧实现会一次批量拉取很多“完整区块 + 全部交易”。BNB 活跃区块体积很大，
长窗口时容易造成内存/网络峰值。这里改为小批次流式扫描，找到目标钱包后立即停止追踪，
并且只保留有原生币 value 的紧凑交易记录。
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Tuple

import main as core


def _tx_index(tx: dict) -> int:
    return core.h2i((tx or {}).get("transactionIndex"))


def _block_batch_size(client: core.RpcClient) -> int:
    if client.chain.key == "bnb":
        return 4
    if client.chain.key == "robinhood":
        return 12
    return 8


def _iter_blocks(client: core.RpcClient, start: int, end: int, descending: bool) -> Iterable[dict]:
    if end < start:
        return
    batch_size = _block_batch_size(client)
    if descending:
        cursor = end
        while cursor >= start:
            lo = max(start, cursor - batch_size + 1)
            nums = list(range(cursor, lo - 1, -1))
            vals = client.batch([("eth_getBlockByNumber", [core.qhex(n), True]) for n in nums], batch_size=batch_size)
            for block in vals:
                if isinstance(block, dict) and "__error__" not in block:
                    yield block
            cursor = lo - 1
    else:
        cursor = start
        while cursor <= end:
            hi = min(end, cursor + batch_size - 1)
            nums = list(range(cursor, hi + 1))
            vals = client.batch([("eth_getBlockByNumber", [core.qhex(n), True]) for n in nums], batch_size=batch_size)
            for block in vals:
                if isinstance(block, dict) and "__error__" not in block:
                    yield block
            cursor = hi + 1


def _compact_positive_value_txs(block: dict) -> Iterable[dict]:
    bn = core.h2i(block.get("number"))
    ts = core.h2i(block.get("timestamp"))
    for tx in block.get("transactions") or []:
        if not isinstance(tx, dict):
            continue
        value = core.h2i(tx.get("value"))
        if value <= 0:
            continue
        fr = core.norm(tx.get("from"))
        to = core.norm(tx.get("to"))
        if not fr or not to:
            continue
        yield {
            "from": fr,
            "to": to,
            "value": value,
            "hash": tx.get("hash") or "",
            "transactionIndex": tx.get("transactionIndex"),
            "_block": bn,
            "_timestamp": ts,
        }


def find_prefunders_streaming(client: core.RpcClient, buys: Dict[str, dict], funding_minutes: float):
    if not buys:
        return {}, []

    lookback = max(1, int((funding_minutes * 60) / max(client.chain.avg_block_time, 0.05)))
    buy_txs = core.get_transactions(client, [v.get("tx_hash", "") for v in buys.values()])
    buy_index = {w: _tx_index(buy_txs.get(v.get("tx_hash", ""), {})) for w, v in buys.items()}
    start_for = {w: max(0, int(v["block"]) - lookback) for w, v in buys.items()}
    min_start = min(start_for.values())
    max_end = max(int(v["block"]) for v in buys.values())

    unresolved = set(buys)
    prefunders: Dict[str, dict] = {}
    positive_value_txs: List[dict] = []

    print(f"进度：正在查找 {len(unresolved)} 个买入钱包的买入前资金来源……", flush=True)

    for block in _iter_blocks(client, min_start, max_end, descending=True):
        bn = core.h2i(block.get("number"))
        for tx in _compact_positive_value_txs(block):
            positive_value_txs.append(tx)
            wallet = tx["to"]
            if wallet not in unresolved:
                continue
            buy = buys[wallet]
            buy_block = int(buy["block"])
            if bn < start_for[wallet] or bn > buy_block:
                continue
            if bn == buy_block and _tx_index(tx) >= buy_index.get(wallet, 0):
                continue
            if tx["from"] == wallet:
                continue
            prefunders[wallet] = {
                "funder": tx["from"],
                "value": tx["value"],
                "block": bn,
                "timestamp": tx["_timestamp"],
                "tx_hash": tx["hash"],
            }
            unresolved.remove(wallet)

        if not unresolved:
            break
        # 当前区块已经早于所有剩余钱包各自允许的窗口，就可以结束。
        if unresolved and bn < min(start_for[w] for w in unresolved):
            break

    print(f"进度：已找到 {len(prefunders)} 个钱包的买入前资金来源。", flush=True)
    return prefunders, positive_value_txs


def find_returns_streaming(client: core.RpcClient, sells: Dict[str, dict], prefunders: Dict[str, dict], return_minutes: float):
    wallets = [w for w in sells if w in prefunders]
    if not wallets:
        return {}

    span = max(1, int((return_minutes * 60) / max(client.chain.avg_block_time, 0.05)))
    sell_txs = core.get_transactions(client, [sells[w].get("tx_hash", "") for w in wallets])
    sell_index = {w: _tx_index(sell_txs.get(sells[w].get("tx_hash", ""), {})) for w in wallets}
    start_for = {w: int(sells[w]["block"]) for w in wallets}
    end_for = {w: int(sells[w]["block"]) + span for w in wallets}
    lower = min(start_for.values())
    upper = max(end_for.values())
    unresolved = set(wallets)
    hits: Dict[str, dict] = {}

    print(f"进度：正在检查 {len(wallets)} 个钱包卖出后的资金回流……", flush=True)

    for block in _iter_blocks(client, lower, upper, descending=False):
        bn = core.h2i(block.get("number"))
        for tx in _compact_positive_value_txs(block):
            wallet = tx["from"]
            if wallet not in unresolved:
                continue
            if bn < start_for[wallet] or bn > end_for[wallet]:
                continue
            if bn == start_for[wallet] and _tx_index(tx) <= sell_index.get(wallet, -1):
                continue
            target = prefunders[wallet]["funder"]
            if tx["to"] != target:
                continue
            hits[wallet] = {
                "to": target,
                "value": tx["value"],
                "block": bn,
                "timestamp": tx["_timestamp"],
                "tx_hash": tx["hash"],
            }
            unresolved.remove(wallet)

        if not unresolved:
            break
        if unresolved and bn > max(end_for[w] for w in unresolved):
            break

    print(f"进度：检测到 {len(hits)} 个钱包存在直接资金回流。", flush=True)
    return hits


def safer_get_logs(self: core.RpcClient, address: str, start: int, end: int, topics=None, _depth: int = 0):
    params = {"fromBlock": core.qhex(start), "toBlock": core.qhex(end), "address": address}
    if topics is not None:
        params["topics"] = topics

    errors = []
    # 先尝试当前节点，再尝试同链备用节点；成功后直接切换当前节点。
    urls = [self.url] + [u for u in self.urls if u != self.url]
    for url in urls:
        try:
            result = self._single_on(url, "eth_getLogs", [params]) or []
            self.url = url
            return result
        except Exception as exc:
            errors.append(f"{url}: {exc}")

    text = " | ".join(errors).lower()
    permanent = any(x in text for x in (
        "archive requests require", "forbidden", "http 403", "status code: 403",
        "unauthorized", "invalid api key",
    ))
    if permanent or start >= end or _depth >= 14:
        raise core.RpcError("历史日志节点请求失败：" + " | ".join(errors))

    mid = (start + end) // 2
    return (
        safer_get_logs(self, address, start, mid, topics, _depth + 1)
        + safer_get_logs(self, address, mid + 1, end, topics, _depth + 1)
    )


# 替换重资源路径，但保持主分析、风险评分、中文报告字段不变。
core.find_prefunders = find_prefunders_streaming
core.find_direct_returns = find_returns_streaming
core.RpcClient.get_logs = safer_get_logs


if __name__ == "__main__":
    raise SystemExit(core.main())
