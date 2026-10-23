"""Robinhood Chain copy trader controlled from Telegram.

Watches a list of wallets. When one of them buys or sells through an allowed
router (default: the Pons swap router), the same route is replayed from our own
hot wallet with our own size. Trading starts OFF; turn it on with /on.
"""
import json
import os
import sys
import time
import traceback
from decimal import Decimal
from urllib import error, parse, request

RPC_URL = os.getenv("RPC_URL", "https://rpc.mainnet.chain.robinhood.com")
CHAIN_ID = int(os.getenv("CHAIN_ID", "4663"))
BOT = os.getenv("COPY_TG_BOT_TOKEN", "")
CHAT = os.getenv("TELEGRAM_CHAT_ID", "")
PRIVATE_KEY = os.getenv("COPY_PRIVATE_KEY", "")
STATE_FILE = os.getenv("COPY_STATE_FILE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json"))
ROUTERS = {a.strip().lower() for a in os.getenv("COPY_ROUTERS", "0x65050a9b7e5075a2ba5ced7b1b64ee66262c40dc").split(",") if a.strip()}
SWAP_SELECTOR = "0x4d819a2a"
POLL_SEC = float(os.getenv("COPY_POLL_SEC", "1.5"))
MAX_AGE_SEC = int(os.getenv("COPY_MAX_AGE_SEC", "30"))
EXPLORER = "https://robinhoodchain.blockscout.com"
TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
WEI = 10 ** 18
MAX_SPAN = 25000

DEFAULT_STATE = {
    "enabled": False,
    "dry_run": False,
    "copy_sells": True,
    "amount_eth": "0.01",
    "slippage_pct": "15",
    "max_trade_eth": "0.05",
    "daily_cap_eth": "0.2",
    "gas_reserve_eth": "0.002",
    "targets": {"0xfc9fdf943a988c8fc09830df38052ac9e320d4c8": {"enabled": True, "label": "first", "amount_eth": None}},
    "spent": {"day": "", "wei": 0},
    "last_block": 0,
    "seen_tx": [],
    "tg_offset": 0,
    "positions": {},
}


# ---------- helpers (pure, unit tested) ----------

def norm(addr):
    a = (addr or "").strip().lower()
    if len(a) != 42 or not a.startswith("0x"):
        raise ValueError("地址格式不对")
    int(a[2:], 16)
    return a


def to_wei(eth):
    return int(Decimal(str(eth)) * WEI)


def fmt_eth(wei):
    return f"{Decimal(wei) / WEI:.6f}".rstrip("0").rstrip(".") or "0"


def words(calldata):
    body = calldata[10:]
    return [body[i:i + 64] for i in range(0, len(body), 64)]


def word_int(calldata, idx):
    return int(words(calldata)[idx], 16)


def rewrite_swap(calldata, target, me, amount_in, min_out, deadline):
    """Reuse the target's route; swap in our size, min-out, deadline and address."""
    if not calldata.lower().startswith(SWAP_SELECTOR):
        raise ValueError("not a router swap")
    w = words(calldata)
    tword = "0" * 24 + target.lower()[2:]
    mword = "0" * 24 + me.lower()[2:]
    w = [mword if x.lower() == tword else x for x in w]
    w[2] = f"{amount_in:064x}"
    w[3] = f"{max(min_out, 0):064x}"
    w[4] = f"{deadline:064x}"
    return SWAP_SELECTOR + "".join(w)


def scaled_min_out(target_min, target_in, my_in, slippage_pct):
    if target_min <= 0 or target_in <= 0:
        return 0
    keep = Decimal(100) - Decimal(str(slippage_pct))
    return int(Decimal(target_min) * Decimal(my_in) / Decimal(target_in) * keep / 100)


def sell_fraction(sold, remaining):
    total = sold + remaining
    if total <= 0:
        return Decimal(1)
    frac = Decimal(sold) / Decimal(total)
    return Decimal(1) if frac >= Decimal("0.95") else frac


def classify(tx, logs, target):
    """Return ('buy'|'sell'|None, token) from the target's own transfers in the tx."""
    t = target[2:]
    ins = [l for l in logs if len(l["topics"]) >= 3 and l["topics"][2][-40:] == t]
    outs = [l for l in logs if len(l["topics"]) >= 3 and l["topics"][1][-40:] == t]
    value = int(tx.get("value", "0x0"), 16)
    if value > 0 and ins:
        return "buy", ins[-1]["address"].lower()
    if outs and value == 0:
        return "sell", outs[0]["address"].lower()
    return None, None


# ---------- state ----------

def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            s = json.load(f)
    except FileNotFoundError:
        s = {}
    merged = json.loads(json.dumps(DEFAULT_STATE))
    merged.update(s)
    return merged


def save_state(s):
    s["seen_tx"] = s["seen_tx"][-500:]
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(s, f, indent=1)
    os.replace(tmp, STATE_FILE)


# ---------- io ----------

class RPC:
    def __init__(self, url):
        self.url = url

    def call(self, method, params):
        delay = 0.5
        for _ in range(8):
            body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
            req = request.Request(self.url, body, {"content-type": "application/json", "user-agent": "copy-trader/1"})
            try:
                with request.urlopen(req, timeout=20) as r:
                    out = json.loads(r.read())
            except error.HTTPError as exc:
                if exc.code == 429:
                    time.sleep(delay); delay = min(delay * 2, 5); continue
                raise
            except (error.URLError, TimeoutError):
                time.sleep(delay); delay = min(delay * 2, 5); continue
            if "error" in out:
                if out["error"].get("code") == 429:
                    time.sleep(delay); delay = min(delay * 2, 5); continue
                raise RuntimeError(f"{method}: {out['error']}")
            return out["result"]
        raise RuntimeError(f"{method}: RPC 限流/超时")


def tg(method, data):
    req = request.Request(f"https://api.telegram.org/bot{BOT}/{method}", parse.urlencode(data).encode(), method="POST")
    with request.urlopen(req, timeout=40) as r:
        return json.loads(r.read())


def send(text):
    print("TG", text.replace("\n", " | "), flush=True)
    if not (BOT and CHAT):
        return
    for _ in range(3):
        try:
            tg("sendMessage", {"chat_id": CHAT, "text": text, "disable_web_page_preview": "true"})
            return
        except error.HTTPError as exc:
            if exc.code == 429:
                time.sleep(5); continue
            print("TG_ERR", exc, flush=True); return
        except Exception as exc:
            print("TG_ERR", exc, flush=True); time.sleep(2)


# ---------- trader ----------

class Trader:
    def __init__(self, rpc, state):
        self.rpc = rpc
        self.s = state
        self.acct = None
        if PRIVATE_KEY:
            from eth_account import Account
            self.acct = Account.from_key(PRIVATE_KEY)
        self.me = self.acct.address.lower() if self.acct else None

    # chain reads
    def balance(self, addr):
        return int(self.rpc.call("eth_getBalance", [addr, "latest"]), 16)

    def erc20(self, token, data):
        return int(self.rpc.call("eth_call", [{"to": token, "data": data}, "latest"]) or "0x0", 16)

    def token_balance(self, token, owner):
        return self.erc20(token, "0x70a08231" + "0" * 24 + owner[2:])

    def allowance(self, token, owner, spender):
        return self.erc20(token, "0xdd62ed3e" + "0" * 24 + owner[2:] + "0" * 24 + spender[2:])

    def symbol(self, token):
        try:
            raw = self.rpc.call("eth_call", [{"to": token, "data": "0x95d89b41"}, "latest"])
            b = bytes.fromhex(raw[2:])
            if len(b) >= 96:
                n = int.from_bytes(b[32:64], "big")
                return b[64:64 + n].decode("utf-8", "ignore") or token[:8]
            return b.rstrip(b"\0").decode("utf-8", "ignore") or token[:8]
        except Exception:
            return token[:8]

    # sending
    def send_tx(self, to, data, value):
        gas_price = int(self.rpc.call("eth_gasPrice", []), 16)
        tx = {"from": self.me, "to": to, "data": data, "value": hex(value)}
        gas = int(self.rpc.call("eth_estimateGas", [tx, "latest"]), 16)
        nonce = int(self.rpc.call("eth_getTransactionCount", [self.me, "pending"]), 16)
        signed = self.acct.sign_transaction({
            "chainId": CHAIN_ID, "nonce": nonce, "to": to, "data": data, "value": value,
            "gas": int(gas * 1.3) + 20000, "maxFeePerGas": gas_price * 2, "maxPriorityFeePerGas": 0, "type": 2,
        })
        raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
        raw_hex = "0x" + raw.hex().removeprefix("0x")
        h = "0x" + signed.hash.hex().removeprefix("0x")
        try:
            h = self.rpc.call("eth_sendRawTransaction", [raw_hex])
        except RuntimeError as exc:
            if "already known" not in str(exc).lower():
                raise
        for _ in range(60):
            rc = self.rpc.call("eth_getTransactionReceipt", [h])
            if rc:
                return h, rc.get("status") == "0x1"
            time.sleep(0.5)
        return h, None

    def position(self, token):
        """Tokens this bot bought. A buy still unconfirmed is stored as 'pending:<balance before>'."""
        pos = self.s.setdefault("positions", {})
        v = str(pos.get(token, "0"))
        if v.startswith("pending:"):
            got = max(self.token_balance(token, self.me) - int(v.split(":", 1)[1]), 0)
            if got:
                pos[token] = str(got)
            return got
        return int(v)

    def best_out(self, to, target, inp, amt, value, hi):
        """Largest min-out the router still accepts right now (binary search with eth_call)."""
        def ok(mo):
            data = rewrite_swap(inp, target, self.me, amt, mo, int(time.time()) + 120)
            try:
                self.rpc.call("eth_call", [{"from": self.me, "to": to, "data": data, "value": hex(value)}, "latest"])
                return True
            except RuntimeError:
                return False
        if not ok(1):
            return 0
        lo, hi = 1, max(hi, 2)
        while ok(hi) and hi < 1 << 200:
            lo, hi = hi, hi * 2
        for _ in range(14):
            mid = (lo + hi) // 2
            if mid <= lo:
                break
            if ok(mid): lo = mid
            else: hi = mid
        return lo

    def spent_today(self):
        day = time.strftime("%Y-%m-%d", time.gmtime(time.time() + 8 * 3600))  # Beijing day
        if self.s["spent"].get("day") != day:
            self.s["spent"] = {"day": day, "wei": 0}
        return self.s["spent"]["wei"]

    def copy_buy(self, target, tx, token, sym):
        cfg = self.s["targets"][target]
        amt = to_wei(cfg.get("amount_eth") or self.s["amount_eth"])
        amt = min(amt, to_wei(self.s["max_trade_eth"]))
        if self.spent_today() + amt > to_wei(self.s["daily_cap_eth"]):
            return f"⛔ 今日额度已用完（上限 {self.s['daily_cap_eth']} ETH），没跟。"
        bal = self.balance(self.me)
        if bal - amt < to_wei(self.s["gas_reserve_eth"]):
            return f"⛔ 余额不足：{fmt_eth(bal)} ETH，没跟。"
        inp = tx["input"]
        t_in = word_int(inp, 2)
        if word_int(inp, 3) <= 0 or t_in <= 0:
            return "⛔ 对方这笔没设最小成交量（可能是诱饵交易），为安全不跟。"
        min_out = scaled_min_out(word_int(inp, 3), t_in, amt, self.s["slippage_pct"])
        data = rewrite_swap(inp, target, self.me, amt, min_out, int(time.time()) + 120)
        if self.s["dry_run"]:
            return f"🧪 模拟：会用 {fmt_eth(amt)} ETH 买 {sym}"
        before = self.token_balance(token, self.me)
        self.s["spent"]["wei"] += amt          # count first, so a crash/pending tx can't bypass the cap
        save_state(self.s)
        try:
            h, ok = self.send_tx(tx["to"], data, amt)
        except Exception:
            self.s["spent"]["wei"] -= amt
            raise
        if ok is False:
            self.s["spent"]["wei"] -= amt
        pos = self.s.setdefault("positions", {})
        if ok:
            got = max(self.token_balance(token, self.me) - before, 0)
            pos[token] = str(self.position(token) + got)
        elif ok is None and not pos.get(token):
            pos[token] = f"pending:{before}"
        save_state(self.s)
        return f"{'✅ 已跟买' if ok else '❌ 跟买失败' if ok is False else '⏳ 已发出未确认'} {fmt_eth(amt)} ETH → {sym}\n{EXPLORER}/tx/{h}"

    def copy_sell(self, target, tx, token, sym):
        pos = self.s.setdefault("positions", {})
        mine = min(self.token_balance(token, self.me), self.position(token))
        if mine <= 0:
            return None
        inp = tx["input"]
        sold = word_int(inp, 2)
        left = self.token_balance(token, target)
        frac = sell_fraction(sold, left)
        amt = mine if frac == 1 else int(Decimal(mine) * frac)
        if amt <= 0:
            return None
        pct = f"{frac * 100:.0f}%"
        if self.s["dry_run"]:
            return f"🧪 模拟：会卖出 {pct} 的 {sym}"
        router = tx["to"].lower()
        if self.allowance(token, self.me, router) < amt:
            approve = "0x095ea7b3" + "0" * 24 + router[2:] + f"{amt:064x}"
            h, ok = self.send_tx(token, approve, 0)
            if not ok:
                return f"❌ 授权失败，没卖 {sym}\n{EXPLORER}/tx/{h}"
        # The target's own sell already moved the price, so quote what we can get now.
        guess = scaled_min_out(word_int(inp, 3), sold, amt, 0)
        quote = self.best_out(router, target, inp, amt, 0, guess)
        if quote <= 0:
            return f"❌ 现在卖不出去（可能是貔貅币或没流动性），请手动处理 {sym}"
        keep = (Decimal(100) - Decimal(self.s["slippage_pct"])) / 100
        min_out = int(Decimal(quote) * keep)
        data = rewrite_swap(inp, target, self.me, amt, min_out, int(time.time()) + 120)
        h, ok = self.send_tx(router, data, 0)
        if ok:
            left_pos = max(int(pos.get(token, "0")) - amt, 0)
            if left_pos: pos[token] = str(left_pos)
            else: pos.pop(token, None)
            save_state(self.s)
        return f"{'✅ 已跟卖' if ok else '❌ 跟卖失败' if ok is False else '⏳ 已发出未确认'} {pct} {sym}\n{EXPLORER}/tx/{h}"

    def handle_tx(self, h, logs):
        tx = self.rpc.call("eth_getTransactionByHash", [h])
        if not tx:
            return
        target = (tx.get("from") or "").lower()
        if target not in self.s["targets"] or target == self.me:
            return
        cfg = self.s["targets"][target]
        label = cfg.get("label") or target[:8]
        side, token = classify(tx, logs, target)
        if not side:
            return
        sym = self.symbol(token)
        head = f"👀 {label} {'买入' if side == 'buy' else '卖出'} {sym}"
        if side == "buy":
            head += f" {fmt_eth(int(tx['value'], 16))} ETH"
        head += f"\n代币 {token}\n{EXPLORER}/tx/{h}"
        routed = (tx.get("to") or "").lower() in ROUTERS and tx["input"].lower().startswith(SWAP_SELECTOR)
        blk = self.rpc.call("eth_getBlockByNumber", [tx["blockNumber"], False])
        age = time.time() - int(blk["timestamp"], 16)
        if not routed:
            send(head + "\n（不是走 Pons 路由的交易，只提醒不跟）"); return
        if not self.s["enabled"] or not cfg.get("enabled", True):
            send(head + "\n（跟单关着，只提醒）"); return
        if age > MAX_AGE_SEC:
            send(head + f"\n（已过去 {int(age)} 秒，太旧不跟）"); return
        if not self.acct:
            send(head + "\n（服务器还没配置跟单钱包私钥）"); return
        if side == "sell" and not self.s["copy_sells"]:
            send(head + "\n（跟卖已关）"); return
        try:
            result = self.copy_buy(target, tx, token, sym) if side == "buy" else self.copy_sell(target, tx, token, sym)
        except Exception as exc:
            result = f"❌ 跟单出错：{str(exc)[:200]}"
        if result:
            send(head + "\n" + result)
        elif side == "sell":
            send(head + "\n（我们没持仓，不用卖）")

    def poll_chain(self):
        targets = list(self.s["targets"].keys())
        latest = int(self.rpc.call("eth_blockNumber", []), 16)
        start = self.s["last_block"] + 1 if self.s["last_block"] else latest
        if latest - start > MAX_SPAN:
            start = latest - 200
        if start > latest or not targets:
            self.s["last_block"] = latest; return
        pads = ["0x" + "0" * 24 + a[2:] for a in targets]
        logs = []
        for topics in ([TRANSFER, None, pads], [TRANSFER, pads]):
            logs += self.rpc.call("eth_getLogs", [{"fromBlock": hex(start), "toBlock": hex(latest), "topics": topics}])
        by_tx = {}
        for l in logs:
            by_tx.setdefault(l["transactionHash"], []).append(l)
        for h, ls in by_tx.items():
            if h in self.s["seen_tx"]:
                continue
            self.s["seen_tx"].append(h)
            save_state(self.s)   # persist before trading so a restart never copies the same tx twice
            try:
                self.handle_tx(h, ls)
            except Exception as exc:
                print("HANDLE_ERR", h, exc, flush=True)
        self.s["last_block"] = latest

    # ---------- telegram commands ----------
    def status_text(self):
        lines = [
            f"跟单：{'🟢 开' if self.s['enabled'] else '🔴 关'}{'（模拟）' if self.s['dry_run'] else ''}",
            f"每笔 {self.s['amount_eth']} ETH · 单笔上限 {self.s['max_trade_eth']} · 每日上限 {self.s['daily_cap_eth']}",
            f"滑点 {self.s['slippage_pct']}% · 跟卖 {'开' if self.s['copy_sells'] else '关'} · 今日已用 {fmt_eth(self.spent_today())} ETH",
        ]
        if self.me:
            try:
                lines.append(f"钱包 {self.me}\n余额 {fmt_eth(self.balance(self.me))} ETH")
            except Exception:
                lines.append(f"钱包 {self.me}")
        else:
            lines.append("钱包：未配置私钥")
        lines.append(f"跟踪 {len(self.s['targets'])} 个地址（/list 查看）")
        return "\n".join(lines)

    def command(self, text):
        parts = text.strip().split()
        if not parts:
            return None
        cmd = parts[0].split("@")[0].lower()
        args = parts[1:]
        s = self.s
        try:
            if cmd in ("/help", "/start"):
                return HELP
            if cmd == "/status":
                return self.status_text()
            if cmd == "/on":
                s["enabled"] = True
                return "🟢 跟单已开启。" + ("" if self.acct else "\n⚠️ 但服务器还没配置私钥，暂时只能提醒。")
            if cmd == "/off":
                s["enabled"] = False; return "🔴 跟单已关闭，只提醒。"
            if cmd in ("/dryrun", "/sells"):
                key = "dry_run" if cmd == "/dryrun" else "copy_sells"
                arg = (args[:1] or [""])[0].lower()
                if arg in ("on", "1", "开"): s[key] = True
                elif arg in ("off", "0", "关"): s[key] = False
                else: return f"用法：{cmd} on 或 {cmd} off"
                return f"{'模拟模式' if key == 'dry_run' else '跟卖'}：{'开' if s[key] else '关'}"
            if cmd == "/add":
                a = norm(args[0]); label = " ".join(args[1:]) or a[:8]
                if a == self.me or a in ROUTERS:
                    return "不能添加自己的跟单钱包或路由合约"
                s["targets"][a] = {"enabled": True, "label": label, "amount_eth": None}
                return f"➕ 已添加 {label}\n{a}"
            if cmd in ("/del", "/remove"):
                a = norm(args[0])
                return "➖ 已删除" if s["targets"].pop(a, None) else "列表里没有这个地址"
            if cmd in ("/pause", "/resume"):
                a = norm(args[0])
                if a not in s["targets"]:
                    return "列表里没有这个地址"
                s["targets"][a]["enabled"] = cmd == "/resume"
                return f"{s['targets'][a]['label']}：{'恢复跟单' if cmd == '/resume' else '暂停跟单（仍提醒）'}"
            if cmd == "/list":
                if not s["targets"]:
                    return "还没有跟踪地址，用 /add 地址 备注 添加"
                return "\n".join(
                    f"{'🟢' if c.get('enabled', True) else '⏸'} {c.get('label')} · {c.get('amount_eth') or s['amount_eth']} ETH\n{a}"
                    for a, c in s["targets"].items())
            if cmd == "/amount":
                if len(args) == 2:
                    a = norm(args[0]); v = Decimal(args[1]); assert v.is_finite() and v > 0
                    if a not in s["targets"]:
                        return "列表里没有这个地址"
                    s["targets"][a]["amount_eth"] = str(v); return f"{s['targets'][a]['label']} 每笔 {v} ETH"
                v = Decimal(args[0]); assert v.is_finite() and v > 0
                s["amount_eth"] = str(v); return f"默认每笔 {v} ETH"
            if cmd == "/max":
                v = Decimal(args[0]); assert v.is_finite() and v > 0; s["max_trade_eth"] = str(v); return f"单笔上限 {v} ETH"
            if cmd == "/daily":
                v = Decimal(args[0]); assert v.is_finite() and v > 0; s["daily_cap_eth"] = str(v); return f"每日上限 {v} ETH"
            if cmd == "/slip":
                v = Decimal(args[0]); assert 0 < v < 90; s["slippage_pct"] = str(v); return f"滑点 {v}%"
            if cmd == "/reserve":
                v = Decimal(args[0]); assert v.is_finite() and v >= 0; s["gas_reserve_eth"] = str(v); return f"保留 {v} ETH 作 Gas"
        except (IndexError, ValueError, AssertionError, ArithmeticError):
            return "参数不对，发 /help 看用法"
        return None

    def poll_telegram(self):
        if not BOT:
            return
        try:
            r = tg("getUpdates", {"offset": self.s["tg_offset"] + 1, "timeout": 0,
                                  "allowed_updates": json.dumps(["message", "channel_post"])})
        except Exception as exc:
            print("TG_POLL_ERR", exc, flush=True); return
        for u in r.get("result", []):
            self.s["tg_offset"] = max(self.s["tg_offset"], u["update_id"])
            m = u.get("message") or u.get("channel_post") or {}
            if str(m.get("chat", {}).get("id")) != str(CHAT):
                continue
            reply = self.command(m.get("text") or "")
            if reply:
                save_state(self.s)
                send(reply)


HELP = """跟单机器人命令
/status 状态和余额
/on 开启跟单  /off 关闭（只提醒）
/add 地址 备注  添加跟踪地址
/del 地址  删除
/pause 地址  /resume 地址  单个暂停/恢复
/list 地址列表
/amount 0.01  默认每笔 ETH
/amount 地址 0.02  某个地址单独金额
/max 0.05  单笔上限
/daily 0.2  每日上限
/slip 15  滑点%
/sells on|off  跟卖开关
/dryrun on|off  模拟模式（不真下单）
/reserve 0.002  保留 Gas"""


def main():
    if not BOT or not CHAT:
        raise SystemExit("Missing COPY_TG_BOT_TOKEN or TELEGRAM_CHAT_ID")
    state = load_state()
    trader = Trader(RPC(RPC_URL), state)
    send("🤖 跟单机器人已启动\n" + trader.status_text() + "\n发 /help 查看命令")
    last_tg = 0.0
    while True:
        try:
            trader.poll_chain()
            if time.time() - last_tg > 3:
                trader.poll_telegram(); last_tg = time.time()
            save_state(state)
        except Exception as exc:
            print("LOOP_ERR", exc, flush=True)
            traceback.print_exc()
            time.sleep(3)
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    sys.exit(main())
