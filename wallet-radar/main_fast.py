#!/usr/bin/env python3
"""Wallet Radar 低内存执行入口（保持 main.py 分析口径不变）。

原则：
- 不改变 Top holders、主动买卖、共同资金源、资金源身份评分、回流、风险评分阈值；
- 只把最重的“完整区块一次性留在内存”改成小批次流式读取；
- 资金源身份评分仍覆盖原逻辑使用的完整合并窗口，不因提前找到 funder 而缩水；
- 保留 main.py 原有窗口合并与筛选语义，包括其同区块/合并窗口行为；
- RPC 403/归档权限错误不再无限递归拆分，避免把小服务器拖死；
- 输出明确阶段标记，供 Telegram 实时显示真实进度。
"""
from __future__ import annotations

from collections import Counter
from typing import Dict, Iterable, List, Tuple

import main as core


_original_scan_transfer_history = core.scan_transfer_history
_original_active_trade_times = core.active_trade_times
_original_funder_identity = core.funder_identity


class FundingWindowStats(dict):
    """仅保存 funder 身份评分真正需要的统计量，替代完整交易对象列表。"""


def _tx_index(tx: dict) -> int:
    return core.h2i((tx or {}).get("transactionIndex"))


def _block_batch_size(client: core.RpcClient) -> int:
    # 只控制瞬时内存峰值；不会改变扫描范围或判断口径。
    if client.chain.key == "bnb":
        return 6
    if client.chain.key == "robinhood":
        return 12
    return 8


def _iter_blocks(client: core.RpcClient, start: int, end: int, descending: bool = False) -> Iterable[dict]:
    if end < start:
        return
    batch_size = _block_batch_size(client)
    if descending:
        cursor = end
        while cursor >= start:
            lo = max(start, cursor - batch_size + 1)
            nums = list(range(cursor, lo - 1, -1))
            vals = client.batch(
                [("eth_getBlockByNumber", [core.qhex(n), True]) for n in nums],
                batch_size=batch_size,
            )
            for block in vals:
                if isinstance(block, dict) and "__error__" not in block:
                    yield block
            cursor = lo - 1
    else:
        cursor = start
        while cursor <= end:
            hi = min(end, cursor + batch_size - 1)
            nums = list(range(cursor, hi + 1))
            vals = client.batch(
                [("eth_getBlockByNumber", [core.qhex(n), True]) for n in nums],
                batch_size=batch_size,
            )
            for block in vals:
                if isinstance(block, dict) and "__error__" not in block:
                    yield block
            cursor = hi + 1


def _iter_merged_blocks(client: core.RpcClient, intervals: List[Tuple[int, int]], descending: bool = False) -> Iterable[dict]:
    merged = core.merge_intervals(intervals)
    if descending:
        for a, b in reversed(merged):
            yield from _iter_blocks(client, a, b, descending=True)
    else:
        for a, b in merged:
            yield from _iter_blocks(client, a, b, descending=False)


def _positive_value(tx: dict) -> int:
    return core.h2i((tx or {}).get("value"))


def scan_transfer_history_with_progress(client, token, scan_hours, start_block=None):
    result = _original_scan_transfer_history(client, token, scan_hours, start_block)
    print("进度标记：25 历史转账读取完成，正在重建持币结构。", flush=True)
    return result


def active_trade_times_with_progress(client, transfers, eoa_addresses, max_events_per_side=20):
    result = _original_active_trade_times(client, transfers, eoa_addresses, max_events_per_side)
    print("进度标记：50 持币结构、外部账户和主动买卖初筛完成，正在分析共同资金来源。", flush=True)
    return result


def find_prefunders_streaming(client: core.RpcClient, buys: Dict[str, dict], funding_minutes: float):
    """与 main.py 的 find_prefunders 保持同一选择语义，但不保存完整区块。

    原逻辑：
    1) 对每个 buy 建立 lookback 区间；
    2) 合并区间后扫描完整区块；
    3) 对候选钱包，选择合并窗口中 block<=buy_block 的最后一笔原生币入账；
    4) funder 身份评分使用这些合并窗口里的完整资金行为。

    这里第一遍倒序仅用于快速定位“最后一笔入账”；第二遍仍完整覆盖合并窗口，
    但只累计最终 funder 的 outgoing_count / recipients，数学结果与原评分所需输入一致。
    """
    if not buys:
        print("进度标记：75 未发现主动买入钱包，共同资金来源分析完成。", flush=True)
        return {}, FundingWindowStats()

    lookback = max(1, int((funding_minutes * 60) / max(client.chain.avg_block_time, 0.05)))
    intervals = [(int(v["block"]) - lookback, int(v["block"])) for v in buys.values()]
    merged = core.merge_intervals(intervals)
    unresolved = set(buys)
    prefunders: Dict[str, dict] = {}

    print(f"进度：正在查找 {len(unresolved)} 个买入钱包的买入前资金来源……", flush=True)

    # 倒序找最后一笔符合原逻辑条件的入账。一个钱包找到后，继续往前扫描不会改变结果。
    for block in _iter_merged_blocks(client, merged, descending=True):
        bn = core.h2i(block.get("number"))
        ts = core.h2i(block.get("timestamp"))
        txs = [tx for tx in (block.get("transactions") or []) if isinstance(tx, dict)]
        txs.sort(key=_tx_index, reverse=True)
        for tx in txs:
            to = core.norm(tx.get("to"))
            if to not in unresolved:
                continue
            if bn > int(buys[to]["block"]):
                continue
            fr = core.norm(tx.get("from"))
            value = _positive_value(tx)
            if not fr or fr == to or value <= 0:
                continue
            prefunders[to] = {
                "funder": fr,
                "value": value,
                "block": bn,
                "timestamp": ts,
                "tx_hash": tx.get("hash") or "",
            }
            unresolved.remove(to)
        if not unresolved:
            break

    # 身份评分必须按原逻辑覆盖完整合并窗口，不能因为已找到 funder 就提前结束。
    funders = {item["funder"] for item in prefunders.values() if item.get("funder")}
    stats = FundingWindowStats({f: {"outgoing_count": 0, "recipients": set()} for f in funders})
    if funders:
        for block in _iter_merged_blocks(client, merged, descending=False):
            for tx in block.get("transactions") or []:
                if not isinstance(tx, dict):
                    continue
                fr = core.norm(tx.get("from"))
                if fr not in funders or _positive_value(tx) <= 0:
                    continue
                item = stats[fr]
                item["outgoing_count"] += 1
                to = core.norm(tx.get("to"))
                if core.valid_address(to):
                    item["recipients"].add(to)

    print(f"进度：已找到 {len(prefunders)} 个钱包的买入前资金来源。", flush=True)
    print("进度标记：75 共同资金来源分析完成，正在检查卖出后资金回流。", flush=True)
    return prefunders, stats


def funder_identity_from_stats(
    client: core.RpcClient,
    funder: str,
    group_wallets: List[str],
    all_txs,
    prefunders: Dict[str, dict],
    buys: Dict[str, dict],
):
    # 非低内存入口仍完全交回原函数。
    if not isinstance(all_txs, FundingWindowStats):
        return _original_funder_identity(client, funder, group_wallets, all_txs, prefunders, buys)

    labels = core.load_labels()
    if funder in labels:
        item = labels[funder]
        label = item.get("label") if isinstance(item, dict) else str(item)
        kind = item.get("type", "已标记地址") if isinstance(item, dict) else "已标记地址"
        return {
            "type": kind,
            "label": label,
            "service_score": 100 if "交易所" in kind else 0,
            "coord_score": 0,
        }

    try:
        funder_eoa = core.classify_eoas(client, [funder]).get(funder, False)
    except Exception:
        funder_eoa = False

    stat = all_txs.get(funder) or {"outgoing_count": 0, "recipients": set()}
    outgoing_count = int(stat.get("outgoing_count") or 0)
    recipients = set(stat.get("recipients") or set())
    ratio = len(recipients & set(group_wallets)) / max(1, len(recipients))
    values = [prefunders[w]["value"] for w in group_wallets if w in prefunders]
    similar = core.amount_similarity(values)
    buy_times = [buys[w].get("timestamp", 0) for w in group_wallets if w in buys]
    buy_tight, _ = core.tight_count(buy_times, 120)

    # 以下评分公式逐项保持 main.py 原值。
    service_score = 0
    service_score += 25 if len(recipients) >= 50 else 0
    service_score += 25 if len(recipients) >= 200 else 0
    service_score += 20 if outgoing_count >= 300 else 0
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
        "type": kind,
        "label": "",
        "service_score": min(100, service_score),
        "coord_score": min(100, coord_score),
        "unique_recipients": len(recipients),
        "outgoing_count": outgoing_count,
        "related_ratio": ratio,
    }


def find_returns_streaming(
    client: core.RpcClient,
    sells: Dict[str, dict],
    prefunders: Dict[str, dict],
    return_minutes: float,
):
    # 区间构造与 main.py 完全一致。
    intervals = []
    for wallet, sell in sells.items():
        if wallet in prefunders:
            span = max(1, int((return_minutes * 60) / max(client.chain.avg_block_time, 0.05)))
            intervals.append((int(sell["block"]), int(sell["block"]) + span))
    if not intervals:
        print("进度标记：90 无需检查资金回流，正在汇总综合风险。", flush=True)
        return {}

    hits = {}
    # 原逻辑在合并区间中判断 fr in prefunders；这里保留同一语义，不额外收窄到单钱包窗口。
    for block in _iter_merged_blocks(client, intervals, descending=False):
        ts = core.h2i(block.get("timestamp"))
        bn = core.h2i(block.get("number"))
        for tx in block.get("transactions") or []:
            if not isinstance(tx, dict):
                continue
            fr = core.norm(tx.get("from"))
            to = core.norm(tx.get("to"))
            if fr in prefunders and to == prefunders[fr]["funder"] and _positive_value(tx) > 0:
                hits.setdefault(
                    fr,
                    {
                        "to": to,
                        "value": _positive_value(tx),
                        "block": bn,
                        "timestamp": ts,
                        "tx_hash": tx.get("hash") or "",
                    },
                )
        if len(hits) == len(prefunders):
            break

    print(f"进度：检测到 {len(hits)} 个钱包存在直接资金回流。", flush=True)
    print("进度标记：90 资金回流检查完成，正在汇总关联持仓和综合风险。", flush=True)
    return hits


def safer_get_logs(self: core.RpcClient, address: str, start: int, end: int, topics=None, _depth: int = 0):
    params = {"fromBlock": core.qhex(start), "toBlock": core.qhex(end), "address": address}
    if topics is not None:
        params["topics"] = topics

    errors = []
    urls = [self.url] + [u for u in self.urls if u != self.url]
    for url in urls:
        try:
            result = self._single_on(url, "eth_getLogs", [params]) or []
            self.url = url
            return result
        except Exception as exc:
            errors.append(f"{url}: {exc}")

    low = " | ".join(errors).lower()
    permanent = any(
        x in low
        for x in (
            "archive requests require",
            "forbidden",
            "http 403",
            "status code: 403",
            "unauthorized",
            "invalid api key",
        )
    )
    if permanent or start >= end or _depth >= 14:
        raise core.RpcError("历史日志节点请求失败：" + " | ".join(errors))

    mid = (start + end) // 2
    return (
        safer_get_logs(self, address, start, mid, topics, _depth + 1)
        + safer_get_logs(self, address, mid + 1, end, topics, _depth + 1)
    )


# 只替换资源实现/进度输出；主分析、分组、风险阈值、报告字段仍由 main.py 执行。
core.scan_transfer_history = scan_transfer_history_with_progress
core.active_trade_times = active_trade_times_with_progress
core.find_prefunders = find_prefunders_streaming
core.funder_identity = funder_identity_from_stats
core.find_direct_returns = find_returns_streaming
core.RpcClient.get_logs = safer_get_logs


if __name__ == "__main__":
    raise SystemExit(core.main())
