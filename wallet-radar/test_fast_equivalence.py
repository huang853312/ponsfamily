#!/usr/bin/env python3
"""证明 main_fast 的低内存实现不改变 main.py 关键关系判断语义。"""
from types import SimpleNamespace

import main as core

# 必须在导入 main_fast 前保存原实现。
original_prefunders = core.find_prefunders
original_returns = core.find_direct_returns
original_identity = core.funder_identity

import main_fast as fast  # noqa: E402  导入后会 monkeypatch core


def h(n):
    return hex(n)


W1 = "0x1111111111111111111111111111111111111111"
W2 = "0x2222222222222222222222222222222222222222"
F1 = "0xf111111111111111111111111111111111111111"
X1 = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
X2 = "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
TOKEN = "0xcccccccccccccccccccccccccccccccccccccccc"


def tx(fr, to, value, idx, txid):
    return {
        "from": fr,
        "to": to,
        "value": h(value),
        "transactionIndex": h(idx),
        "hash": "0x" + txid.rjust(64, "0"),
    }


def block(n, txs):
    return {"number": h(n), "timestamp": h(1_700_000_000 + n * 60), "transactions": txs}


BLOCKS = {
    8: block(8, [tx(F1, W2, 5, 1, "81"), tx(F1, X1, 1, 2, "82")]),
    9: block(9, [tx(F1, X2, 2, 1, "91")]),
    # W1 的资金入账放在 buy 所在区块高 transactionIndex；原逻辑会计入，低内存实现必须保持一致。
    10: block(10, [tx(F1, W1, 7, 9, "109")]),
    11: block(11, []),
    20: block(20, [tx(W1, F1, 3, 0, "200")]),
    21: block(21, []),
    22: block(22, []),
    23: block(23, [tx(W2, F1, 4, 3, "233")]),
}


class FakeClient:
    def __init__(self):
        self.chain = SimpleNamespace(avg_block_time=60.0, key="bnb", name_zh="测试链", chain_id=56)
        self.url = "fake://rpc"
        self.urls = (self.url,)

    def batch(self, calls, batch_size=50):
        out = []
        for method, params in calls:
            if method == "eth_getBlockByNumber":
                n = int(params[0], 16)
                out.append(BLOCKS.get(n, block(n, [])))
            elif method == "eth_getCode":
                out.append("0x")
            else:
                out.append(None)
        return out


client = FakeClient()
buys = {
    W1: {"block": 10, "timestamp": 1_700_000_600},
    W2: {"block": 11, "timestamp": 1_700_000_660},
}

# funding_minutes=2 => W1 区间[8,10]，W2区间[9,11]，合并后[8,11]。
# 原逻辑对 W2 会看到合并窗口中的 block 8；这是现有主逻辑语义，低内存实现不得私自修正。
orig_pref, orig_txs = original_prefunders(client, buys, 2)
fast_pref, fast_stats = fast.find_prefunders_streaming(client, buys, 2)
assert fast_pref == orig_pref, (orig_pref, fast_pref)
assert fast_pref[W1]["funder"] == F1
assert fast_pref[W1]["block"] == 10
assert fast_pref[W2]["funder"] == F1
assert fast_pref[W2]["block"] == 8

orig_ident = original_identity(client, F1, [W1, W2], orig_txs, orig_pref, buys)
fast_ident = fast.funder_identity_from_stats(client, F1, [W1, W2], fast_stats, fast_pref, buys)
assert fast_ident == orig_ident, (orig_ident, fast_ident)

sells = {
    W1: {"block": 20, "timestamp": 1_700_001_200},
    W2: {"block": 21, "timestamp": 1_700_001_260},
}
orig_ret = original_returns(client, sells, orig_pref, 2)
fast_ret = fast.find_returns_streaming(client, sells, fast_pref, 2)
assert fast_ret == orig_ret, (orig_ret, fast_ret)

print("EQUIVALENCE_OK")
print("prefunders=一致")
print("funder_identity=一致")
print("direct_returns=一致")
