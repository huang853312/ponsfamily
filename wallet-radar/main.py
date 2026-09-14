#!/usr/bin/env python3
import argparse
import csv
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Dict, Iterable, List, Optional, Tuple

import requests
from dotenv import load_dotenv

load_dotenv()

API_BASE = os.getenv("BLOCKSCOUT_API_BASE", "https://robinhoodchain.blockscout.com/api/v2").rstrip("/")
RPC_URL = os.getenv("ROBINHOOD_RPC_URL", "").strip()
TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "30"))

ZERO_ADDRESSES = {
    "0x0000000000000000000000000000000000000000",
    "0x000000000000000000000000000000000000dead",
}


def norm(addr: Optional[str]) -> str:
    return (addr or "").lower()


def addr_hash(obj) -> str:
    if isinstance(obj, dict):
        return obj.get("hash") or obj.get("address") or ""
    return obj or ""


def as_decimal(value) -> Decimal:
    try:
        return Decimal(str(value or "0"))
    except (InvalidOperation, ValueError):
        return Decimal(0)


def parse_ts(value: Optional[str]) -> datetime:
    if not value:
        return datetime.max.replace(tzinfo=timezone.utc)
    value = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return datetime.max.replace(tzinfo=timezone.utc)


def get_json(url: str, params: Optional[dict] = None, attempts: int = 4) -> dict:
    last = None
    for i in range(attempts):
        try:
            r = requests.get(url, params=params, timeout=TIMEOUT)
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            last = exc
            if i + 1 < attempts:
                time.sleep(1.0 * (i + 1))
    raise RuntimeError(f"GET failed: {url}: {last}")


def iter_items(path: str, params: Optional[dict] = None, max_pages: int = 200) -> Iterable[dict]:
    current = dict(params or {})
    seen = set()
    for _ in range(max_pages):
        payload = get_json(f"{API_BASE}{path}", current)
        items = payload.get("items") or []
        for item in items:
            yield item
        nxt = payload.get("next_page_params")
        if not nxt:
            return
        marker = tuple(sorted((str(k), str(v)) for k, v in nxt.items()))
        if marker in seen:
            return
        seen.add(marker)
        current = dict(nxt)
    raise RuntimeError(f"Pagination exceeded max_pages={max_pages} for {path}")


def rpc(method: str, params: list):
    if not RPC_URL:
        raise RuntimeError("ROBINHOOD_RPC_URL is empty. Put it in wallet-radar/.env before running.")
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    last = None
    for i in range(4):
        try:
            r = requests.post(RPC_URL, json=payload, timeout=TIMEOUT)
            r.raise_for_status()
            data = r.json()
            if "error" in data:
                raise RuntimeError(data["error"])
            return data.get("result")
        except Exception as exc:
            last = exc
            if i + 1 < 4:
                time.sleep(1.0 * (i + 1))
    raise RuntimeError(f"RPC {method} failed: {last}")


def is_eoa(address: str) -> bool:
    code = rpc("eth_getCode", [address, "latest"])
    return code in ("0x", "0x0", None)


def token_meta(token: str) -> dict:
    return get_json(f"{API_BASE}/tokens/{token}")


def top_holders(token: str, top_n: int) -> Tuple[List[dict], dict]:
    meta = token_meta(token)
    supply = as_decimal(meta.get("total_supply"))
    decimals = int(meta.get("decimals") or 0)
    rows = []
    for item in iter_items(f"/tokens/{token}/holders", max_pages=50):
        address = addr_hash(item.get("address"))
        if not address or norm(address) in ZERO_ADDRESSES:
            continue
        raw = as_decimal(item.get("value"))
        pct = (raw / supply * Decimal(100)) if supply > 0 else Decimal(0)
        rows.append({
            "address": address,
            "raw_balance": raw,
            "balance": raw / (Decimal(10) ** decimals) if decimals >= 0 else raw,
            "supply_pct": pct,
            "explorer_is_contract": bool((item.get("address") or {}).get("is_contract")) if isinstance(item.get("address"), dict) else None,
        })
        if len(rows) >= top_n:
            break
    return rows, meta


def collect_incoming_native(address: str, max_pages: int) -> List[dict]:
    target = norm(address)
    out = []
    seen = set()

    # Normal transactions.
    for item in iter_items(f"/addresses/{address}/transactions", max_pages=max_pages):
        to_addr = addr_hash(item.get("to"))
        from_addr = addr_hash(item.get("from"))
        value = as_decimal(item.get("value"))
        if norm(to_addr) != target or value <= 0 or not from_addr:
            continue
        status = str(item.get("status") or "").lower()
        if status and status not in {"ok", "success", "1"}:
            continue
        key = (item.get("hash"), norm(from_addr), str(value), item.get("timestamp"), "normal")
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "source": from_addr,
            "value_wei": value,
            "timestamp": item.get("timestamp"),
            "tx_hash": item.get("hash") or "",
            "kind": "normal",
        })

    # Internal native transfers are included too so proxy/router paths do not get silently missed.
    try:
        for item in iter_items(f"/addresses/{address}/internal-transactions", max_pages=max_pages):
            to_addr = addr_hash(item.get("to"))
            from_addr = addr_hash(item.get("from"))
            value = as_decimal(item.get("value"))
            if norm(to_addr) != target or value <= 0 or not from_addr:
                continue
            key = (item.get("transaction_hash") or item.get("hash"), norm(from_addr), str(value), item.get("timestamp"), "internal")
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "source": from_addr,
                "value_wei": value,
                "timestamp": item.get("timestamp"),
                "tx_hash": item.get("transaction_hash") or item.get("hash") or "",
                "kind": "internal",
            })
    except RuntimeError as exc:
        print(f"WARN internal tx scan skipped for {address}: {exc}", file=sys.stderr)

    out.sort(key=lambda x: parse_ts(x.get("timestamp")))
    return out


def earliest_funder(address: str, max_pages: int) -> Optional[dict]:
    incoming = collect_incoming_native(address, max_pages=max_pages)
    return incoming[0] if incoming else None


def fmt_pct(v: Decimal) -> str:
    return f"{v:.6f}"


def main() -> int:
    p = argparse.ArgumentParser(description="Robinhood Chain wallet cluster MVP: token -> top holders -> EOA -> earliest native-ETH funder")
    p.add_argument("--token", required=True, help="ERC-20 token contract address")
    p.add_argument("--top", type=int, default=50, help="number of top holders to inspect")
    p.add_argument("--tx-page-limit", type=int, default=200, help="maximum Blockscout pages per wallet")
    p.add_argument("--output", default="wallet_report.csv", help="CSV output path")
    args = p.parse_args()

    if not RPC_URL:
        print("ERROR: ROBINHOOD_RPC_URL is not set. Create wallet-radar/.env from .env.example.", file=sys.stderr)
        return 2

    holders, meta = top_holders(args.token, args.top)
    symbol = meta.get("symbol") or "?"
    name = meta.get("name") or "?"
    print(f"Token: {name} ({symbol}) {args.token}")
    print(f"Top holders loaded: {len(holders)}")

    report = []
    common = Counter()

    for idx, h in enumerate(holders, 1):
        address = h["address"]
        print(f"[{idx}/{len(holders)}] {address} {fmt_pct(h['supply_pct'])}%", flush=True)
        try:
            eoa = is_eoa(address)
        except Exception as exc:
            print(f"  WARN eth_getCode failed: {exc}", file=sys.stderr)
            eoa = False

        funder = None
        if eoa:
            try:
                funder = earliest_funder(address, args.tx_page_limit)
            except Exception as exc:
                print(f"  WARN funder scan failed: {exc}", file=sys.stderr)

        funder_addr = funder["source"] if funder else ""
        if funder_addr:
            common[norm(funder_addr)] += 1

        report.append({
            "rank": idx,
            "wallet": address,
            "supply_pct": fmt_pct(h["supply_pct"]),
            "balance": str(h["balance"]),
            "is_eoa": str(eoa).lower(),
            "earliest_native_funder": funder_addr,
            "earliest_native_value_wei": str(funder["value_wei"]) if funder else "",
            "earliest_native_timestamp": funder["timestamp"] if funder else "",
            "earliest_native_tx_hash": funder["tx_hash"] if funder else "",
            "earliest_native_kind": funder["kind"] if funder else "",
        })

    with open(args.output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(report[0].keys()) if report else ["rank"])
        writer.writeheader()
        writer.writerows(report)

    print("\nCommon earliest native-ETH funders:")
    if not common:
        print("  none found")
    else:
        for address, count in common.most_common(20):
            linked_supply = sum(
                as_decimal(r["supply_pct"])
                for r in report
                if norm(r["earliest_native_funder"]) == address
            )
            print(f"  {address}  wallets={count}  linked_supply={linked_supply:.6f}%")

    print(f"\nCSV: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
