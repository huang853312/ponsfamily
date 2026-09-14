# 多链钱包雷达

这是一个完全独立的 EVM 钱包关系分析工具，安装路径固定为 `/opt/wallet-radar`。它不读取、不修改、不重启 `/opt/hyperevm-radar` 或 `hyperevm-radar.service`。

内置支持：

- Robinhood Chain（链 ID 4663）
- HyperEVM（链 ID 999）
- BNB Chain（链 ID 56）
- 其他 EVM 链可用 `custom` 模式直接传 RPC 与 Chain ID，无需改主分析逻辑

核心逻辑全部直接读链上 RPC，不依赖 Bubblemaps、Arkham、Cielo 或 Blockscout 才能运行。Blockscout/交易所标签未来只作为可选补充，不是主数据源。

## 当前分析能力

输入 `链 + 代币 CA` 后，系统会自动：

1. 直接扫描 ERC-20 Transfer 日志并重建持仓。
2. 计算 Top 持币地址并区分外部账户/合约。
3. 识别由钱包本人发起、通过合约完成的首次主动买入/卖出。
4. 在首次主动买入前的指定时间窗口，直接扫描区块交易，寻找最近一笔原生币 Gas 资金来源。
5. 聚合同一资金来源的钱包。
6. 统计 2 分钟集中买入、5 分钟集中卖出、资金金额相似度、当前关联持仓占比。
7. 检查卖出后是否直接把原生币回流给原资金来源。
8. 根据资金地址的扇出范围、关联钱包占比、金额相似、买入同步性，区分“疑似交易所/服务型地址”与“高概率私人或团队协调资金地址”。
9. 输出中文 CSV；如启用 Telegram，则电报报告全部中文。

注意：共同资金来源只是链上关联证据，不等于同一人。系统不会因为“同一 Funder”单一证据就下结论。

## 使用

检查三条内置链：

```bash
cd /opt/wallet-radar
.venv/bin/python main.py selftest --all
```

分析 Robinhood Chain：

```bash
.venv/bin/python main.py analyze --chain robinhood --token 0xTOKEN --top 50 --telegram
```

分析 HyperEVM：

```bash
.venv/bin/python main.py analyze --chain hyperevm --token 0xTOKEN --top 50 --telegram
```

分析 BNB Chain：

```bash
.venv/bin/python main.py analyze --chain bnb --token 0xTOKEN --top 50 --telegram
```

其他 EVM 链：

```bash
.venv/bin/python main.py analyze \
  --chain custom \
  --token 0xTOKEN \
  --rpc-url https://YOUR_RPC \
  --chain-id 12345 \
  --chain-name "自定义链" \
  --native-symbol ETH
```

## 扫描范围

新币默认向前扫描约 24 小时。若代币更老，建议增加 `--scan-hours` 或直接提供 `--start-block`。系统会在报告里明确标记“链上历史覆盖：较完整/有限”，避免把不完整数据包装成完整结论。

默认只在首次主动买入前 10 分钟寻找直接原生币资金来源，这是为了快速抓“发币前集中打 Gas → 集中买入”的模式。可用 `--funding-minutes` 调大。卖出后的直接回流窗口默认 10 分钟，可用 `--return-minutes` 调整。

## 电报

电报字段、判断、风险、统计全部使用中文。链名、Token Symbol、CA、钱包地址、交易哈希等原始链上标识保持原样。

## 安全边界

- 不导入现有 HyperEVM Radar 的任何 Python 文件。
- 不读取 `/opt/hyperevm-radar`。
- 不修改或重启 `hyperevm-radar.service`。
- HyperEVM 在这里只是一条被查询的 EVM 链，与现有 HyperEVM Radar 是两套独立程序。
