# Wallet Radar (isolated)

Standalone Robinhood Chain wallet-cluster analysis. This directory is intentionally independent from `hyperevm-radar/` and `robinhood-radar/`; it does not import, modify, or deploy either radar.

## MVP

`CA -> Top Holders -> EOA/Contract -> earliest native ETH funder -> common-funder ranking -> linked supply %`

Data sources:
- Robinhood Chain Blockscout API for token holders and address transaction history.
- Robinhood Chain RPC only for `eth_getCode` EOA/contract classification.

## Setup

```bash
cd wallet-radar
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Fill only ROBINHOOD_RPC_URL in wallet-radar/.env
```

## Run

```bash
python main.py --token 0xTOKEN --top 50 --output wallet_report.csv
```

The output contains each top holder, supply percentage, EOA classification, earliest native-ETH funder, timestamp, transaction hash, and transfer type. The terminal also ranks common funders and sums the linked holder supply.

## Safety boundary

- No HyperEVM files are read or imported at runtime.
- No existing radar service is restarted or edited.
- No Telegram or production deployment is performed by this MVP.
- A common funder is an on-chain relationship signal, not proof that wallets have the same owner.
