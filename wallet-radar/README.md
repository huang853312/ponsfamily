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

## Telegram 输出规范

Telegram 面向用户的描述必须全部使用中文。不得出现诸如 `Common Funder`、`Type`、`Exchange probability`、`Coordinated-wallet probability`、`Funded wallets`、`Bought same token`、`Buy window`、`Coordinated sells`、`Returned funds` 等英文标签。

允许保留链上原始内容，例如代币符号、合约地址、钱包地址、交易哈希和必要的协议/产品专有名词；解释性字段和风险结论统一使用中文。

示例：

```text
🚨 钱包集群预警

代币：GRACE
合约地址：0x...

共同资金来源：0xc10c...a461
资金来源类型：疑似个人或团队控制钱包
交易所地址可能性：8%
协同控制可能性：92%

获得资金的钱包：30 个
买入同一代币的钱包：28 个
集中买入时间：2 分 03 秒
资金金额相似的钱包：26 / 30
同步卖出的钱包：24 个
资金回流的钱包：18 个
历史无关交互地址数量：较少

综合风险：高
```

## Safety boundary

- No HyperEVM files are read or imported at runtime.
- No existing radar service is restarted or edited.
- No Telegram or production deployment is performed by this MVP.
- A common funder is an on-chain relationship signal, not proof that wallets have the same owner.
