# Multi-Chain Auto Trader

Independent EVM auto-trading framework for strategies A-E. It is fail-closed by default.

Confirmed policy: single order max USD 100; UTC daily aggregate max USD 500; slippage max 300 bps (3%); minimum pool liquidity USD 1,000; strategy mode independent. A/B/D/E are configured as enabled rule definitions. C remains disabled until wallet addresses and a verified buy-detection adapter are supplied.

Safety boundary: this repository does not ship a chain-specific signing/broadcast adapter. No generic transaction is constructed or sent. TRADING_ENABLED defaults to false. Unknown chain, factory, DEX router, ABI, token extraction rule, or wallet is rejected. A reviewed chain adapter and verified chain configuration are prerequisites for live orders.

Install: copy .env.example to /etc/multi-chain-auto-trader.env; generate a unique SIGNAL_API_TOKEN; keep TRADING_ENABLED=false until adapters are reviewed; copy config/chains.example.yaml to config/chains.yaml and fill chain IDs/RPC environment variable names from official sources; then run pytest -q.

Signal API: POST /signals with Authorization header and JSON containing signal_id, chain_id, tx_hash, log_index, rule_id, token_address plus quote/risk fields. Duplicate signals are ignored. Signals cannot execute without verified risk fields and an installed adapter.

Kill switch: POST /kill creates /var/lib/multi-chain-auto-trader/KILL_SWITCH. Keep API bound to localhost or a private authenticated network.
