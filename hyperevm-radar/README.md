# HyperEVM Radar

现有链上 scanner、Creator 聚类、Platform Family 和 infra role 识别保持为身份发现的事实入口。身份补全在 `asyncio.to_thread` 后台调度中运行，不在 block scanner 的网络热路径等待搜索、官网或 X。

## Family intelligence 流程

```text
HyperEVM blocks
  -> contract discovery / Creator / roles（原有逻辑）
  -> platform_candidates（原有逻辑）
  -> creator + block window Platform Family（原有逻辑）
  -> background identity refresh
       -> creator/member address searches（始终执行）
       -> optional name/symbol/brand and DefiLlama hints
       -> website and X candidates
       -> hostname / root domain / subdomain / title / metadata
       -> registrable label -> core_word -> bounded derived_words
       -> domain/title-driven X, docs, GitHub and ecosystem searches
       -> public website / X metadata and links
       -> website -> docs -> Family address verification
       -> website <-> X identity/context cross-verification
       -> multi-label infrastructure classification
       -> official-source Token / CA association check
       -> platform_family_intelligence
       -> verified Family Telegram notification
```

Family 现在是增强证据而不是 identity hard gate：已有 Family 会提供 Creator、member address 和 role；没有 Family 的链上 candidate 或 ecosystem website seed 也会进入独立 investigation。官网/Docs 新发现的项目地址会保存为反向关联证据，并在地址已属于现有 Family 时回连该 `family_id`。无论是否有关联 Family，只有达到既有 `VERIFIED` 标准的结果才生成 Telegram notification。

`brand_hint`、`token_name`、`token_symbol` 均只是可选线索，不是 gate。DefiLlama 和 ecosystem directory 也只提供辅助 URL，不负责确认身份。

### URL 中间词

- `core_word` 来自 registrable/root domain 的完整主标签：去掉点号层级和标签内分隔符后转小写，不由网页标题替换，也不加入任何外部营销词；完整核心词始终保留为最重要的搜索和匹配线索。
- `derived_words` 按顺序取 `core_word` 的前 3 位、前 4 位和前 5 位；长度不足时，只生成实际可用的前缀。例如：`trade → [tra, trad, trade]`，`novagrid → [nov, nova, novag]`。
- 不做去元音缩写，不做字符排列组合，不改变字符顺序，也不添加 `core_word` 中不存在的字符。
- core/derived 命中现有 Token name/symbol 只保存为 discovery candidate；不能改变 `token_status`，官方 CA 仍必须经过下述来源确认。

## Verification 与平台代币

- 搜索结果中的 X 或网站只算 candidate。
- 官网或由官网链接/同 root domain 的 Docs 中出现 Creator/Family member 地址，会标为 `VERIFIED_BY_ADDRESS`。
- 官网与 X 明确双向互链、品牌身份一致，且 HyperEVM/Hyperliquid 或产品上下文一致时，可标为 `VERIFIED_BY_CROSS_LINK`。
- 单独网站、单独 X、搜索命中、域名关键词命中或单向链接都只能标为 `PARTIAL`。
- X 无法公开读取时保存 `NO_DATA` evidence，绝不生成 bio、置顶内容或最近推文。
- 同 Creator、同 Family、同交易或“检测为 ERC20”都不能确认平台代币。
- 寻找 Token / CA 不要求官网、官方 X 或可信 docs 出现任何指定关键词；既有项目与 Token 对应关系经已建立的官方来源确认后，才保存 `CONFIRMED_OFFICIAL`。代币部署者不必等于 Family Creator；反之，同 Creator 也不是确认依据。否则保存 `NONE`，Telegram 不显示 CA。

## External providers

搜索采用 provider abstraction。可以通过 `HYPEREVM_SEARCH_API_URL` 接入 JSON Search API；未配置时使用公共 HTML fallback。X reader 也有独立 provider interface，默认只能尝试公开页面。所有请求都有单请求 timeout 和 Family 总 deadline，异常隔离后降级为 `NO_DATA`。

Root domain 使用无额外依赖的保守解析（包含常见国家二级后缀处理）。若生产环境需要覆盖完整公共后缀列表，应在 provider 中接入持续更新的 Public Suffix List 实现。

若需要稳定读取 X Bio、置顶内容和最近推文，应配置合规的 X API 或其他获授权服务，并实现 `XContentProvider`；公开网页可能返回 403/429 或只返回登录页。

## Tests

```bash
cd hyperevm-radar
PYTHONPATH=. python3 -m unittest discover -s tests -v
python3 -m compileall -q .
```
