# Robinhood Chain Early Discovery Radar V1

面向 Robinhood Chain（chainId **4663**）的 24/7 链上早期发现服务。它不是“新币监控器”：Pool 事件只是入口，目标是从可验证的 bytecode、selector、代理关系、部署 trace（可选）和合约集群中尽早发现新协议组件。

## V1 已实现

- 两个互相独立、各自指数退避的入口：(1) V2 `PairCreated`、V3 `PoolCreated`、V4 `Initialize` range logs；(2) 逐 confirmed block 检查 `tx.to == null` 和 receipt `contractAddress` 的全链顶层合约创建。两者使用独立 SQLite cursor，任一短暂失败不会推进其 cursor，也不会永久停止另一个。第一次运行均从当前 confirmed head 开始，不扫描历史全链。
- 正确区分 V4 Pool ID 与合约地址；PoolManager 记录为共享 `Infrastructure`，不会作为项目 Factory 证据；非零 Hook 单独读取 bytecode、分类并加入 cluster。
- 缓存 token metadata、bytecode、storage 和分类结果，避免同一进程重复 RPC；SQLite 使用 WAL、foreign keys 和 busy timeout。
- 保守分类结果统一为 `{label, confidence, evidence[]}`。支持 `ERC20 / Factory / Router / Vault / Lending / Oracle / AMM / Hook / Aggregator / Bridge / Registry / Proxy / Implementation / Settlement / Pool / Unknown`；语义类别要求 selector 组合，链上关系类别要求明确事件/代理关系，证据不足返回 `Unknown`。
- 真正检测 EIP-1967 implementation slot 和标准 EIP-1167 minimal proxy runtime；Proxy 与 Implementation 分别存库、关联并加入同一 cluster。
- 记录 deployer、bytecode hash、selector set、event-topic set、implementation 和核心角色的首次出现，计算 0–100 Novelty Score。
- 使用 `same_transaction`、`created_by`、`proxy_implementation` 和 pool event 等关系保存项目图。`same_transaction` 只是弱上下文，不参与 S 的 strong-relation 计数。可选 `callTracer` 失败时保存/显示 `unavailable`，绝不伪造 CREATE/CREATE2 来源。
- 关系证据按来源隔离：只有 receipt/trace 验证的创建才写 `created_by`；只有 Pool 创建事件中实际编码的 Token/Hook 才写 `same_transaction`；代理槽或 minimal-proxy target 只写 `proxy_implementation`。同一个创建事实不会重复伪造成两类 strong relation。
- PONS/PAIR 地址参考已写入 `data/gmgn_known_launchpads.json`，但只有 `gmgn_confirmed=true` 的 **PONS V1/V2** 具备静默资格。PAIR 明确为 `gmgn_confirmed=false`，只作地址上下文，普通 PAIR 来源仍按 B/A/S 评级。PONS 路径出现可验证的独立核心结构仍会突破过滤。
- SQLite 持久化 30 分钟 structural observation window。Activity tracker 生命周期与其分离：cluster 一旦被选中就独立跟踪满 60 分钟，即使 structural window 已结束；重启从 activity tracker cursor 恢复。结构等级只在 `B → A` 或 `A → S` 时发送一次升级通知。
- Telegram alert 采用 cluster+level 去重，发送失败会释放 reservation 以便 RPC loop 重试。消息显示缺失字段为 `Unknown / Not observed`。
- 为每个检查合约生成 runtime/normalized hash、selector/topic、代理目标、code size、exact/similar/unknown template 证据，并保存到 `contract_fingerprints`。`UNKNOWN_STRUCTURE` 只增加 novelty，不改变结构 A/S。
- contract-only discovery 会积累 deployer event，生成证据化 DEV 状态/风险；全新 deployer 是 `UNKNOWN`，不是风险。Opportunity Score 分项保存且与 A/S 完全分离，缺失资金、使用、Smart Money、领先度数据保持 `NO_DATA`。

## 分级规则

- **S 🔥**：至少三种独立核心角色，并且至少两类强链上关系证据；这仍表示“疑似完整体系”，不是协议身份断言。
- **A 🚨**：保守规则确认 Lending/Oracle/AMM/Aggregator/Settlement，或确认 Factory+Router、Vault+Registry、Proxy/Implementation+核心逻辑、Hook+核心逻辑等组合。单独 Proxy、Implementation、Factory、Router 或 Vault 不会仅凭角色进入 A。
- **B 🟡**：未命中 mature launchpad 的普通独立 Pool/Token，尚无核心证据。
- **SILENT ❌**：命中 `gmgn_confirmed=true` 的 PONS mature 路径且没有任何独立核心结构；PAIR 不具备静默资格。

## RPC 与 V1 边界

标准 RPC 不提供内部 CREATE/CREATE2 ancestry。默认 `TRACE_ENABLED=false`，此时 alert 明确显示 `trace_status=disabled`；设为 `true` 后尝试 `debug_traceTransaction` 的 `callTracer`。节点不支持时显示 `unavailable`、禁用本进程本次运行的后续 trace 并继续顶层创建监听，避免每笔交易重复请求不支持的方法。V1 不做昂贵的历史全链 trace。

V1 已覆盖每个 confirmed block 的顶层创建；启用 trace 后还会检查区块内每笔交易的内部 CREATE/CREATE2，这可能显著增加 RPC 压力，只应在自有或明确允许 callTracer 的节点开启。它不是 mempool 监听。Explorer、GMGN/DEX Screener API、GitHub/X、资金钱包分析和 AI 二次分类仍未实现，AI 不参与当前评级。

`external_reference/debot.py` 仅定义未来 DeBot 参考接口，没有伪造 endpoint 或 API。该参考层不得替代链上发现，也不得直接升降等级或触发静默。

## 配置

复制 `.env.example`。必须确认或填写：

```dotenv
RPC_URL=https://rpc.mainnet.chain.robinhood.com
TELEGRAM_BOT_TOKEN=       # 要发送 Telegram 时必填
TELEGRAM_CHAT_ID=         # 要发送 Telegram 时必填
UNISWAP_V2_FACTORIES=     # 三类监听地址至少填一类，逗号分隔
UNISWAP_V3_FACTORIES=
UNISWAP_V4_POOL_MANAGERS=
DATABASE_PATH=./data/radar.db
TRACE_ENABLED=false       # 仅 trace-capable RPC 建议启用
CONTRACT_MONITOR_ENABLED=true
MAX_RPC_CONCURRENCY=4
MAX_TRACE_CONCURRENCY=1
MAX_ACTIVITY_CLUSTERS=10
ACTIVITY_SAMPLE_INTERVAL=60
EXTERNAL_PROVIDER_TIMEOUT=10
```

应用启动时会拒绝错误 chainId。合约 monitor 默认启用，所以 Pool factory 可以留空；如果关闭合约 monitor，则至少必须配置一类 Pool factory/manager。本项目不读取、不需要钱包私钥。

## Ubuntu VPS 最终部署

在仓库根目录执行：

```bash
sudo useradd --system --home /opt/robinhood-radar --shell /usr/sbin/nologin robinhood-radar
sudo install -d -o robinhood-radar -g robinhood-radar /opt/robinhood-radar
sudo cp -a robinhood-radar/. /opt/robinhood-radar/
sudo chown -R robinhood-radar:robinhood-radar /opt/robinhood-radar
sudo -u robinhood-radar python3 -m venv /opt/robinhood-radar/.venv
sudo -u robinhood-radar /opt/robinhood-radar/.venv/bin/pip install -r /opt/robinhood-radar/requirements.txt
sudo cp /opt/robinhood-radar/.env.example /etc/robinhood-radar.env
sudo chmod 600 /etc/robinhood-radar.env
sudo editor /etc/robinhood-radar.env
sudo cp /opt/robinhood-radar/systemd/robinhood-radar.service /etc/systemd/system/robinhood-radar.service
sudo systemctl daemon-reload
sudo systemctl enable --now robinhood-radar
sudo systemctl status robinhood-radar
sudo journalctl -u robinhood-radar -f
```

运维命令：

```bash
sudo systemctl start robinhood-radar
sudo systemctl stop robinhood-radar
sudo systemctl restart robinhood-radar
sudo systemctl status robinhood-radar
```

## 检查

```bash
cd robinhood-radar
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
PYTHONPATH=. .venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q .
```

**NOT LIVE VERIFIED**：自动测试覆盖 Pool 解码、顶层/内部创建发现、trace unavailable、过滤突破、代理检测、等级升级、独立 cursor 和 observation 重启恢复，但没有把离线测试冒充 Robinhood Chain 端到端实网验证。

## Capability status

### WORKING

- confirmed-chain contract/Pool discovery、独立 cursor/backoff、SQLite WAL/recovery、30 分钟 cluster、PONS/PAIR filter、EIP-1967/EIP-1167、保守 A/S、Telegram dedupe。
- code fingerprint 与 exact/similar/unknown template 判定、deployer history/risk accumulation、透明 Opportunity 分项持久化。
- ActivityWorker 已接入主程序，只选择 active A/S、near-A 或尚未完成 60 分钟生命周期的 tracker，并受 `MAX_ACTIVITY_CLUSTERS` 限制；逐 confirmed block 统计成功交易的 native value、token-flow 次数、unique users、verified actions 与 `UNKNOWN_CALL`，持久化各自独立的 5/15/30/60 分钟累计 snapshot。token transfer 不会被称为 TVL。
- Capital Growth 只根据相邻 snapshot 的真实 native delta 评分；单次入金后持平不会随时间自动加分。至少两个 snapshot 才有 growth 分数，evidence 保存每个累计值和 delta。
- Activity 更新 Opportunity Score 后，首次跨过 WATCH/HIGH/HOT 档位可发送独立 Telegram upgrade；dedupe key 为 `cluster:{id}:opportunity:{band}`，同档不重复，发送失败释放 reservation 后重试，且不修改 structural A/S。
- wallet profile/event/performance、external first-seen、activity snapshot 的 migration-safe 存储。

### PARTIAL

- Activity：真实 ingestion worker 已运行，但只覆盖直接发送到已知 cluster 合约的 confirmed 交易，以及这些交易 receipt 中流入 cluster 地址的 ERC20 Transfer；复杂内部调用资金流仍可能不完整，TVL 保持 `UNKNOWN`。
- DEV intelligence：已处理本雷达观察到的创建历史；funding source 和无链上证据的 related address 不推测。
- Smart Money：框架和历史表可积累，但没有足够 performance 历史时始终 `NO_DATA/UNKNOWN`。

### PLACEHOLDER / NO_DATA PROVIDERS

- `external_reference/dexscreener.py`、`gmgn.py`、`debot.py`、`social.py` 没有伪造 endpoint，当前统一返回 `NO_DATA`。
- 未连接真实 GMGN、DeBot、DEX Screener 或 Social API；provider 缺失不会影响链上 monitor，也不会改变 A/S 或触发 SILENT。
- USD TVL/价格、Smart Money confirmation 和外部 lead time 当前均为 `NO_DATA`，不是零分或负面结论；Capital/Usage 只有 ActivityWorker 取得真实 snapshot 后才从 `NO_DATA` 变为有数据。

默认并发配置面向 2 CPU/2 GB：两个 discovery monitor 与 ActivityWorker 共用同一个 RPC semaphore（默认 4），trace 另有 semaphore（默认 1），activity 同时最多跟踪 10 个 cluster。开启逐交易 trace 是主要额外 RPC 成本；公共节点建议保持关闭。
