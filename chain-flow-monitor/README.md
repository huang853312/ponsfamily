# 多链资金流向监控 · 审核版

目标是观察数据源已覆盖的全部链，不固定限制 Ethereum、Base、HyperEVM 或 Robinhood Chain。**本版本不是“全球所有链、所有钱包、所有交易”的完整索引器。** 默认每小时采集，Python 3.10+，Linux，仅使用标准库。

## 交付范围与当前阻塞

| 内容 | 本版本实现 | 口径 |
|---|---|---|
| 全部已覆盖链 TVL | 免费接口，自动发现链 | 美元估值；变化不是净流入 |
| 全部已覆盖链美元稳定币规模 | 免费接口，自动发现链 | 美元锚定币的 USD 估值；变化还受增发、销毁等影响 |
| DEX 活跃度 | 免费全局24h交易量 | 不按链拆分；不是买入净额 |
| 各链桥流入、流出、净额排行 | 已写适配器，需授权 Key 并完成字段方向核验 | 昨日 UTC 自然日；仅数据源覆盖的桥；有可能后续修订 |
| 电报推送 | 已写发送模块，需 token/chat ID，并显式 --send | 默认仅打印和保存本地，不发送 |
| 链A→链B逐笔资金路径、鲸鱼、交易所地址归因 | 未实现 | 不能从链级净额反推具体来源链、钱包或协议 |
| 服务器部署 | 独立 GitHub Actions 流程 | 成功需首份报告送达，不以进程存在代替 |

实际访问 `bridges.llama.fi/bridges` 与 `bridgevolume/Ethereum` 返回 HTTP 402。官方文档也将 bridges 列为 Pro 接口。没有购买、没有绕过收费接口、没有默认开启付费请求。无 Key 时桥流量明确显示未接入。

## 与以前的电报机器人完全分开

- 为这套监控新建一个 Telegram bot，使用它自己的 Token。代码没有创建新 bot；这一步需要你在 Telegram 的 BotFather 完成。
- 新 bot 的私聊就是独立对话，不需要新注册 Telegram 账号。如果希望推送到群，则创建专用新群，并使用这个群的 Chat ID，不填旧监控群。
- 只读取 `FLOW_TELEGRAM_BOT_TOKEN`、`FLOW_TELEGRAM_CHAT_ID`，不会回退到旧的 `TELEGRAM_BOT_TOKEN`、`TELEGRAM_CHAT_ID`。专用变量缺失时，发送模式立即报错。
- 独立目录 `/opt/chain-flow-monitor`、独立配置 `/etc/chain-flow-monitor.env`、独立服务 `chain-flow-monitor.service`、独立数据目录 `/var/lib/chain-flow-monitor`。
- 不读取旧雷达的环境文件、数据库和配置，不停止或重启以前的机器人服务。不要把旧 Token 复制到新配置；变量名独立不能自动判断你手工填写的 Token 是否来自新 bot。
- 私聊时 Chat ID 可以仍是你自己的用户 ID：新 Token 决定由新 bot 发送，消息会出现在新 bot 的对话中。

## 计算与数据质量

- 每次重新获取链目录，记录所有返回的链，完整数据在 `report.json`，电报只显示排行和重点链。
- 免费 TVL、稳定币规模和 DEX 交易量分别报告，绝不相加为“资金流入”。TVL、稳定币变化与上一次成功快照比较，明确显示真实间隔；首次运行只有规模，无变化基线；超过48小时不计算变化。
- 桥 API 的 `depositUSD`、`withdrawUSD` 被保留。桥合约视角的“存入”和链视角的“流入”可能相反；当前没有授权响应与网页同日核验，**不默认猜测方向**。接入 Key 后，先用至少两条链的网页同日数值和已知桥入/桥出案例核对；确认 depositUSD 对该链是流入则设 `BRIDGE_DEPOSIT_DIRECTION=inflow`，是流出则设为 `outflow`。只在验证口径对所有返回链一致后启用全局映射，否则保持未验证并另做逐链适配。留空状态为 `direction_unverified`，保存原始数据但不输出方向性排行。净额一律按已核验的链流入减流出计算。
- 所有桥排名采用同一昨日 UTC 自然日；没有昨日数据的链标为 `missing_completed_day` 并排除，不把历史旧日替代昨日，不把缺失或请求失败当零。
- 自然日结束不等于桥索引完整；供应商可能延迟或修订。源未给更新时间时，明确仅知道采集时间。HTTP 200 也不能证明数据实时。
- 不计算“全网总净流入”：桥跨两链存在对应流入流出，不能当外部新增资金；聚合数据也无法做独立逐笔去重验证。
- 链名严格按供应商返回，不把 Hyperliquid L1 自动当 HyperEVM，也不把 Arc 模糊匹配成 Arbitrum。重点链若名称不同或未提供数据会显示未覆盖，请先核实命名。
- 任何新链、缺字段、错误、未接入状态都不能解释为没有资金活动。

## 本地验证

在解压后的目录运行：

```bash
python3 -m unittest -v
python3 monitor.py
```

默认没有电报发送。输出 `data/report.txt`、`data/report.json`、30天 SQLite 快照；第二次采样开始出现变化。

可限制诊断范围（正式运行省略 `--only`）：

```bash
python3 monitor.py --only 'Ethereum,Base,Robinhood Chain,Arc' --data-dir ./diagnostic-data
```

使用不同 `--only` 范围时应使用不同数据目录。程序不自动加载 `.env`，服务由 systemd 注入环境变量；手动运行则由 shell 设置。

## Git 自动部署（独立服务）

推送 `chain-flow-monitor/**` 或 `.github/workflows/deploy-chain-flow-monitor.yml` 到 main 时，运行独立的 GitHub Actions 流程。沿用仓库现有 SERVER_HOST / SERVER_PORT / SERVER_USER / SERVER_SSH_KEY 连接服务器；这些是 SSH 配置，不是旧机器人的 Telegram 配置。

部署前要求服务器已有 `/etc/chain-flow-monitor.env`，包含独立的 FLOW_TELEGRAM_BOT_TOKEN 和 FLOW_TELEGRAM_CHAT_ID。不会创建、覆盖或导入旧机器人的环境文件。部署会确认 bot 为已经人工验证的 `zijin_alert_bot`，目标为频道，且有发布消息权限；不符合则停止。

部署文件仅安装到 `/opt/chain-flow-monitor/releases/<commit-sha>`，通过 `/opt/chain-flow-monitor/current` 选择版本。配置仍在 `/etc/chain-flow-monitor.env`，数据在 `/var/lib/chain-flow-monitor`。只重启 `chain-flow-monitor.service`。失败只回滚本服务；不会操作以前的雷达、机器人或其数据库。

服务启动后，流程最多等待6分钟，只有新采样含有效TVL且首份报告得到 Telegram 成功响应、`health.json` 已更新，才确认部署成功并开启开机启动。`active` 本身不算成功。

检查：

```bash
sudo systemctl status chain-flow-monitor.service --no-pager
sudo cat /var/lib/chain-flow-monitor/health.json
sudo cat /opt/chain-flow-monitor/current/REVISION
```

回退到已存在的某个版本时，只切换 current 到本项目 releases 下的版本，并重启这一个服务。不要对旧程序执行 git pull、rsync、覆盖安装或服务重启。

## 运行约束

- HTTP 请求超时25秒；429和常见5xx有限退避；401/402/403等直接报告，不无限重试。默认桥并发2。
- Telegram 按长度分段，成功确认后记录发送状态；失败不记成功。网络超时发生在 Telegram 已接收后时仍可能重复发送，不能保证 exactly-once。
- API Key 在 URL 中使用是供应商要求，但错误日志不输出完整 URL。真实凭据只放服务器环境文件。
- Linux 文件锁阻止同一数据目录重复实例。数据文件原子替换。默认保存30天快照。
- 此版本没有自动交易、签名或私钥读取。

## 官方参考

- API 分层及授权路径：https://api-docs.defillama.com/
- 免费端点文档：https://api-docs.defillama.com/llms-free.txt
- 实际可用稳定币主机：https://stablecoins.llama.fi/stablecoinchains （文档统一主机路径实测404）
- 桥服务源码：https://github.com/DefiLlama/bridges-server
- Telegram sendMessage：https://core.telegram.org/bots/api#sendmessage

本地11项测试通过；实际部署状态以 GitHub Actions 结果及服务器 health.json 为准。
