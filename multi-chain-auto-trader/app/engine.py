import json, os, uuid
from pathlib import Path
from .risk import evaluate
from .execution import execute_live

class TradingEngine:
    def __init__(self, settings, database, chain_configs, rules, adapter=None):
        self.settings, self.db = settings, database
        self.chain_configs, self.rules, self.adapter = chain_configs, rules, adapter
        self.kill_switch = Path("/var/lib/multi-chain-auto-trader/KILL_SWITCH")

    def handle(self, payload: dict) -> dict:
        required = ("signal_id", "chain_id", "tx_hash", "log_index", "rule_id", "token_address")
        missing = [k for k in required if k not in payload]
        if missing:
            return {"accepted": False, "status": "rejected", "reason": "Missing fields: " + ",".join(missing)}
        rule_id = str(payload["rule_id"]).upper()
        rule = self.rules.get(rule_id, {})
        if not rule or not rule.get("enabled", False):
            return {"accepted": False, "status": "blocked", "reason": "Strategy is not enabled or configured"}
        if rule_id == "C" and not rule.get("wallets"):
            return {"accepted": False, "status": "blocked", "reason": "C strategy has no wallet allowlist"}
        if self.kill_switch.exists():
            return {"accepted": False, "status": "blocked", "reason": "Emergency kill switch is engaged"}
        if self.adapter is None:
            return {"accepted": False, "status": "blocked", "reason": "Reviewed chain-specific execution adapter is not installed"}
        signal = dict(payload)
        signal["payload_json"] = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        if not self.db.insert_signal_once(signal):
            return {"accepted": True, "status": "duplicate_ignored"}
        chain = self.chain_configs.get(str(signal["chain_id"]), {})
        decision = evaluate(signal, self.settings, chain, self.settings.trading_enabled)
        if not decision.allowed:
            return {"accepted": True, "status": "blocked", "reason": decision.reason}
        if rule_id == "D" and not signal.get("composite_conditions_passed"):
            return {"accepted": True, "status": "blocked", "reason": "D strategy requires all AND conditions"}
        order_id = str(uuid.uuid4())
        if not self.db.reserve_daily_budget(order_id, signal["signal_id"], int(signal["chain_id"]),
                                            float(signal["amount_usd"]), self.settings.daily_limit_usd):
            return {"accepted": True, "status": "blocked", "reason": "Daily budget exhausted or duplicate order"}
        key = os.getenv("SIGNER_PRIVATE_KEY", "")
        try:
            result = execute_live(signal, key, self.adapter)
            return {"accepted": True, "status": "submitted", "order_id": order_id, "result": result}
        except Exception as exc:
            return {"accepted": True, "status": "execution_blocked", "order_id": order_id, "reason": str(exc)}
