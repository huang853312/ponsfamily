# Robinhood Chain Early Discovery Radar (MVP)

一个不依赖 DEX Screener/GMGN API 的 Robinhood Chain（chainId **4663**）链上发现服务。它轮询 V2 `PairCreated`、V3 `PoolCreated` 与 V4 `Initialize` 日志，持久化游标，保守分析字节码，并发送可解释的 Telegram 通知。

## 当前真正可运行的能力

- 使用标准 HTTP JSON-RPC 按确认区块增量拉取日志；失败以 1–60 秒指数退避，systemd 也会自动重启进程。
- 首次启动从当前确认高度开始（不会意外回扫全链）；此后每个完整批次事务性保存游标，重启续跑。
- 解码 V2/V3 池地址；V4 没有独立池合约，数据库以 Pool ID 作为池键。
- 保存题目要求的 11 类 SQLite 表，WAL 模式，并用池主键和 alert dedupe key 防止重复通知。
- 读取 token name/symbol（失败显示 `?`），提取 PUSH4 function selector、计算 runtime bytecode hash，以严格证据规则分类；证据不足始终为 `Unknown`。
- 按交易发送者合并 Factory、Pool、Token 为集群。已知系统仅在“无独立核心结构”时静默；核心证据可突破过滤。
- Telegram 使用 HTTPS Bot API；未配置 Telegram 时仍可采集，日志会明确说明未投递。

## 重要边界（预留但尚未实现）

标准 RPC 无法可靠列举内部 `CREATE/CREATE2`，因此 MVP 的 `Creator` 是可验证的池创建交易发送者，而不是臆测的内部 creator。`DeployerTracer` 已留出 trace/explorer adapter。全链合约监听、代理 EIP-1967 slot、事件 topic 指纹、真实 30 分钟后台追踪与升级提醒、调用图、GMGN/DEX Screener/Explorer/GitHub/X、资金来源分析和 AI 二次分类是后续能力。当前集群会随同一发送者后续触发的新池持续更新，但不会主动扫描其全部部署历史。不要把当前启发式标签当作审计结论。

`data/gmgn_known_launchpads.json` 故意不预填未经验证的地址。维护格式：

```json
{"schema_version":1,"updated_at":"2026-09-09","systems":[{"name":"PONS","addresses":{"factory":["0x..."],"launcher":["0x..."],"hook":["0x..."]}}]}
```

## Ubuntu VPS 部署

```bash
sudo useradd --system --home /opt/robinhood-radar --shell /usr/sbin/nologin robinhood-radar
sudo mkdir -p /opt/robinhood-radar
sudo cp -a robinhood-radar/. /opt/robinhood-radar/
sudo chown -R robinhood-radar:robinhood-radar /opt/robinhood-radar
sudo -u robinhood-radar python3 -m venv /opt/robinhood-radar/.venv
sudo -u robinhood-radar /opt/robinhood-radar/.venv/bin/pip install -r /opt/robinhood-radar/requirements.txt
sudo cp /opt/robinhood-radar/.env.example /etc/robinhood-radar.env
sudo chmod 600 /etc/robinhood-radar.env
sudo editor /etc/robinhood-radar.env
sudo cp /opt/robinhood-radar/systemd/robinhood-radar.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now robinhood-radar
sudo systemctl status robinhood-radar
journalctl -u robinhood-radar -f
```

停止/启动/重启分别使用 `sudo systemctl stop|start|restart robinhood-radar`。

## 必填配置

`RPC_URL` 必须是 chainId 4663 的 RPC。还必须至少配置一个经过核验的 `UNISWAP_V2_FACTORIES`、`UNISWAP_V3_FACTORIES` 或 `UNISWAP_V4_POOL_MANAGERS` 地址（逗号分隔）。要报警则填 `TELEGRAM_BOT_TOKEN` 与 `TELEGRAM_CHAT_ID`。本程序不需要、也绝不读取钱包私钥。

## 本地检查

```bash
cd robinhood-radar
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q .
```

真实链验证需要有效 RPC 和已核验 factory 地址；项目不声称仅凭离线测试已经在 Robinhood Chain 实网验证。
