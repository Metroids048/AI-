# 外部数据源/信息源接入方案

## 01 文档定位与边界

`data-and-ingestion-design.md` 已经定义了 A/B/C/D/E 五级数据源的职责边界、接入接口族
（`MarketDataFeed`/`MacroCalendarFeed`/`NewsFeed`/`SocialSignalFeed`/`ResearchSourceFeed`）
与存储分层原则，但该文档有意保持抽象——不点名具体供应商、SDK 或拉取频率数值
（详见该文档"文档定位"一节的声明）。

本文件的职责是**把这些抽象接口填上具体的接入参数**：每一级数据源用什么 API/SDK、
多久拉一次、原始数据存到哪、如何去重。本文件不重新定义分级边界或接口族命名，
全部沿用 `data-and-ingestion-design.md` 与 `AGENTS.md` 的既有定义。

本文件同样不重复定义：连接层重连/心跳策略（见 `24x7-operations-plan.md` §02）、
新闻/社媒数据触发的风控动作（见 `execution-risk-review-design.md` §2.2a）、
LLM 对 C/D 级数据的分类调用方式（见 `llm-integration-plan.md` §1.1/§4）。

---

## 02 分级数据源接入清单

### 2.1 A 级：加密交易所市场数据

| 项目 | 方案 |
|---|---|
| 采集方式 | CCXT（`services/data/` 内，遵循框架隔离原则，不进入 `apps/api`） |
| 已配置交易所 | Binance / OKX / Bybit（`.env.example` 已有对应 Key，`Exchange` 枚举已定义三者） |
| K 线（OHLCV） | 覆盖 `Timeframe` 枚举全部粒度：`1m`/`5m`/`15m`/`1h`/`4h`/`1d`；短周期（1m/5m）用于实时信号与风控巡检，长周期（1h 以上）用于策略回测与研究 |
| 拉取方式 | 优先使用交易所 WebSocket 实时推送 K 线/成交，REST 轮询作为补齐缺口的兜底手段（WS 断连期间的缺口补齐属于 `24x7-operations-plan.md` §2.2"重连后一致性核对"的范畴） |
| Funding Rate | 高频轮询（如每 1 分钟一次），因为资金费率是策略与风控共同关注的字段；具体轮询间隔在 P1 实现时按交易所限流规则校准 |
| Open Interest / Long-Short Ratio | 中频轮询（如每 5 分钟一次），交易所此类接口本身更新频率通常低于行情 |
| Liquidation（强平） | 优先使用交易所 WS 强平推送流（如有），无对应流则退化为不采集，不用高频 REST 轮询模拟（避免无谓触发限流） |
| Order Book（订单簿） | 仅写入 Redis，短 TTL（如数秒级），不落库、不长期保留——沿用 `technical-architecture-plan.md` §07 已确认的"订单簿只进 Redis 不进 TimescaleDB"存储边界 |
| 标准化存储 | `ohlcv_bars`/`market_extras`（TimescaleDB，`infra/timescale/init.sql` 已定义并拥有这两张表） |

### 2.2 B 级：宏观数据

| 项目 | 方案 |
|---|---|
| 日历类来源 | ForexFactory RSS（`.env.example` 已有 `FOREXFACTORY_RSS_URL`）——用于同步"未来有哪些宏观事件、什么时间发生" |
| 数据类来源 | Trading Economics API、Alpha Vantage API（`.env.example` 已有对应 Key）——用于获取实际值/预测值/前值 |
| 日历同步频率 | 低频（如每日一次），日历本身变化不快 |
| 事件发生窗口内拉取 | 已知事件发生时间点前后设一个短窗口（如 ±15 分钟），窗口内提高轮询频率（如每 1 分钟一次）去抓取"实际值是否已公布"，而不是全天高频轮询——这是把"日历同步"和"结果抓取"拆成两个不同频率的任务，避免对 API 做无意义的高频调用 |
| 落地形式 | 落为 `RiskEvent`（`event_type=macro_event`）与标准化宏观事件参考数据；`macro_events`（TimescaleDB，已确认归属）存放标准化事件记录 |

### 2.3 C 级：新闻数据

| 项目 | 方案 |
|---|---|
| RSS 来源 | 金十（`JINSHI_RSS_URL`）、CoinDesk（`COINDESK_RSS_URL`）、The Block（`THEBLOCK_RSS_URL`）、Reuters（`REUTERS_CRYPTO_RSS_URL`，当前为空，需在 P1 落地时补一个可用的 Reuters/Bloomberg 加密频道源或替代源） |
| 监管类来源 | SEC EDGAR 全文检索 API（`SEC_EDGAR_RSS_URL`） |
| SDK/库选择 | RSS 类用 `feedparser`；SEC EDGAR 用其官方全文检索 REST 接口（`requests`/`httpx` 直接调用，不需要额外 SDK） |
| 拉取频率 | RSS 源近实时轮询（如每 1-3 分钟一次），SEC EDGAR filing 更新频率远低于新闻，可用更低频率轮询（如每 15 分钟一次） |
| 落地形式 | 原始条目先落"原始捕获"（见 §04），经分类判断后落 `RiskEvent`（`event_type=news_risk`），分类逻辑（是否触发风险、严重度分级）由 LLM 完成，属于 `llm-integration-plan.md` §1.1 News Agent 的职责，本文件只负责把内容"采集到位" |

### 2.4 D 级：社媒数据

| 项目 | 方案 |
|---|---|
| Twitter/X | Twitter API v2（`.env.example` 已有 `TWITTER_BEARER_TOKEN`/`TWITTER_WATCH_USER_IDS`）。免费/基础层级的 API 有较严格的调用频率与月度配额限制，需按实际选用的 API 套餐校准轮询频率——这是一个真实的成本/额度约束，不是纯技术选择，具体套餐选型留给 Phase 1 结合预算确认。轮询模式优先用"按已关注账号列表定期拉取最新推文"，而非试图订阅全量实时流（全量流通常需要更高级付费套餐） |
| Telegram | Telegram Bot API（`.env.example` 已有 `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHANNEL_IDS`）。Bot 加入目标频道后可近实时接收消息推送（事件驱动，非轮询），延迟通常在秒级 |
| 落地形式 | 原始消息先落"原始捕获"，经 LLM 分类（严重度/是否风险相关）后落 `RiskEvent`（`event_type=social_event`），分类职责同样在 `llm-integration-plan.md` 范围内，本文件只负责采集环节 |

### 2.5 E 级：研究数据

| 项目 | 方案 | 现状 |
|---|---|---|
| GitHub | GitHub REST/GraphQL API（`.env.example` 已有 `GITHUB_TOKEN`），用于搜索策略相关仓库、跟踪特定仓库更新 | P1 可落地，Key 已具备 |
| 学术论文 | arXiv API（`.env.example` 已有 `ARXIV_CATEGORIES=q-fin.TR,q-fin.PM`），按分类定期拉取新论文列表 | P1 可落地，Key/参数已具备 |
| WorldQuant | 不通过网络 API，走本地路径（`WORLDQUANT_ALPHA_LOCAL_PATH`），由 `research_source/worldquant_adapter/` 消费，仅移植方法论，不上传 Brain 会话密钥（沿用 ADR-009 既有边界） | P0 已有骨架 |
| Reddit | **当前无对应 API Key/库选型**，`AGENTS.md` E 级定义中列出 Reddit 但 `.env.example` 未配置——这是一个尚未落地的真实缺口，建议 P2 补充 `PRAW`（Reddit 官方推荐的 Python 客户端）与对应 API 凭据 | 缺口，P2 |
| YouTube | **当前无对应 API Key/库选型**，同上情况——建议 P2 补充 YouTube Data API 与对应凭据，用于监控特定频道更新 | 缺口，P2 |
| A 股本地系统 | 不是网络数据源，是本地已有系统的数据移植，不在本文件"外部接入"范围内，属于 `research_source/` 下的独立移植任务 | 不适用 |
| GitHub/arXiv 拉取频率 | 低频（如每日一次），研究类数据不要求近实时 | — |
| 落地形式 | 原始捕获 + 转化为 `StrategyIdea`（经 Research Agent），流程见 `strategy-library-collection-and-scoring.md` §01 | — |

---

## 03 IngestionJob 字段取值补全

`domain-and-interfaces-design.md` §3.17 已定义 `IngestionJob` 的字段列表
（`ingestion_job_id`/`source_family`/`source_name`/`job_type`/`schedule_mode`/
`job_status`/`input_window`/`output_ref`），但尚未在 `shared/models/enums.py` 中
落地为具体枚举（当前代码库中不存在 `IngestionJob` 模型本身）。本文件在 P1 实现时
建议的枚举取值：

- `source_family`：`a_market` / `b_macro` / `c_news` / `d_social` / `e_research`
  （与 AGENTS.md 五级分类一一对应，不新增第六类）
- `job_type`：按 §02 各小节，取值如 `ccxt_ohlcv` / `ccxt_funding_rate` /
  `macro_calendar_sync` / `rss_poll` / `sec_edgar_search` / `twitter_poll` /
  `telegram_listen` / `github_search` / `arxiv_sync`
- `schedule_mode`：`realtime`（WS 推送/事件驱动）/ `near_realtime`（分钟级轮询）/
  `scheduled_batch`（日级及以上）/ `manual_backfill`（人工触发补数），与
  `data-and-ingestion-design.md` §7.2 已定义的四种调度模式名称保持一致，不重新命名
- `job_status`：`pending` / `running` / `succeeded` / `failed_fetch` /
  `failed_normalize` / `failed_validate` / `failed_publish`（对应 §7.3 已定义的
  失败分类，细化到具体阶段，方便复盘定位故障环节）

这组取值是本文件相对 `domain-and-interfaces-design.md` 的具体化，不改变该文档已定义
的字段名本身。

---

## 04 原始数据存储路径

`data-and-ingestion-design.md` §5.3 明确把"原始数据与工件"的具体落地方式留白
（"可先以文件/对象存储引用设计，具体落地留到实现阶段"）。本文件给出 P0/P1 阶段的
具体方案：

- **P0/P1（当前数据量级下）**：原始捕获内容（RSS 原文、推文/消息原文、GitHub/arXiv
  返回的原始 JSON）以文件形式写入按 `source_family` 分目录的本地存储卷
  （如 `data/raw/{source_family}/{ingestion_job_id}.json`），`IngestionJob.output_ref`
  记录该文件路径。选择文件存储而非直接落 PostgreSQL 大字段，是因为原始捕获内容
  体量不定且不需要结构化查询，用文件更轻量。
- **P2（数据量增长后）**：迁移到 S3 兼容对象存储（如自建 MinIO），`output_ref`
  改为对象存储 URI，迁移只影响 `output_ref` 的取值格式，不影响上层调用方对
  `IngestionJob` 的使用方式。
- 无论哪个阶段，原始捕获都不直接进入标准化表（`ohlcv_bars`/`market_extras`/
  `risk_events`/`macro_events`），标准化表只存经过 `normalize`/`validate` 阶段
  产出的结构化结果——这一点沿用 `data-and-ingestion-design.md` §4.2 接入流水线的
  阶段划分，本文件不改变该划分，只是把"原始"这一步的物理落地方式定下来。

---

## 05 去重与数据质量规则

- **C 级新闻去重**：同一事件被多个 RSS 源报道是常见情况（如金十和 CoinDesk 同时报
  同一条新闻），去重键建议用"标题归一化文本 + 发布时间窗口（如 10 分钟内）"的组合，
  而不是单纯比较 URL 或 GUID（不同源的 GUID 格式不统一）。去重发生在 `normalize`
  阶段，去重后仍保留全部原始捕获（不删除任何原始记录，只是不重复触发 LLM 分类调用），
  呼应 `llm-integration-plan.md` §4.4 提到的"重复报道应先去重"要求以及成本控制原则。
- **D 级社媒去重**：同一账号短时间内的重复/近似内容（如转发链）同样需要去重，规则
  与新闻类似（文本近似度 + 时间窗口）。
- **时间戳与时区**：所有来源统一转换为 UTC 时间戳落库，沿用
  `data-and-ingestion-design.md` §09 已定义的"统一时间戳标准"原则，本文件不重新定义，
  只强调这一原则同样适用于本文件新引入的具体来源。
- **来源可追溯性**：每条标准化记录必须能追溯到对应的 `IngestionJob` 与原始捕获文件
  路径，这是 §09 已有要求，`output_ref` 字段承担这一职责。

---

## 06 速率限制与成本考量

| 来源 | 限制类型 | 应对方式 |
|---|---|---|
| Twitter API v2 | 调用频率+月度配额（付费分级） | 只轮询已关注账号列表，不订阅全量流；套餐选型留给 Phase 1 结合预算确定 |
| 交易所 REST（Binance/OKX/Bybit） | 按权重的限流（各交易所规则不同） | 优先用 WS 推送减少 REST 调用次数；REST 仅用于补齐缺口和低频字段（OI/Long-Short Ratio） |
| GitHub API | 按认证方式的调用次数限制 | 已用 Token 认证（比匿名限额高），且拉取频率本身是日级，不构成压力 |
| RSS 源 | 通常无官方限流，但过于频繁轮询可能被识别为异常流量 | 近实时轮询间隔不低于 1 分钟 |
| SEC EDGAR | 官方有速率限制规范（需带识别性 User-Agent） | 低频轮询（15 分钟级），并按官方要求设置 User-Agent 标识 |

本节只列出约束类型和应对原则，不锁定具体套餐/额度数值——那些数值会随供应商政策变化，
锁定在设计文档里容易过期，交给 P1 实现时按当时实际情况确认。

---

## 07 与风控链路的对接点

B/C/D 级数据经分类判断后触发 `RiskEvent`，具体的严重度分级与执行层动作（暂停开仓/
收紧止损/仅记录）由 `execution-risk-review-design.md` §2.2a 与即将产出的
风控措施与保障方案文档裁决，本文件不重复定义触发后的动作，只保证"数据能可靠、
及时地采集到位，形成可供分类判断的输入"。

---

## 08 Telegram 三重角色澄清

repo 中 Telegram 相关的用途容易混淆，本节明确区分三种角色，避免 Bot Token 复用错误：

1. **D 级社媒采集**：监听指定频道/群组的公开消息，识别市场相关事件（`TELEGRAM_BOT_TOKEN`
   + `TELEGRAM_CHANNEL_IDS`，当前 `.env.example` 已有）。
2. **E 级研究采集**：监听研究类 Telegram 频道（如策略讨论群），产出 `StrategyIdea`
   线索。这一角色与角色 1 同属"入站采集"，可以复用同一个 Bot Token（只是监听的
   频道列表不同），不违反已有的 Token 隔离原则——因为该原则针对的是"入站采集"与
   "出站告警"的隔离，不是"D 级"与"E 级"之间的隔离。
3. **运维出站告警**（`24x7-operations-plan.md` §06 定义）：系统主动推送告警消息。
   **必须使用与角色 1/2 完全不同的 Bot Token**，这一点已在 `24x7-operations-plan.md`
   与 `technical-architecture-plan.md` 中确认，本文件在此复述以避免实现时误用同一个
   Token 导致"采集"和"告警"权限/流量混在一起。

---

## 09 P0/P1/P2 边界

- **P0**：A 级 CCXT 真实接入（K 线为主）；WorldQuant 本地路径接入（已有骨架）。
- **P1**：A 级补齐 Funding Rate/OI/Long-Short Ratio/Liquidation；B 级宏观日历+数据
  源上线；C 级 RSS+SEC EDGAR 上线；D 级 Twitter/Telegram 上线；E 级 GitHub/arXiv
  上线；`IngestionJob` 模型代码化（§03 枚举落地）；原始数据文件存储路径实现（§04 P0/P1 方案）。
- **P2**：Reddit（PRAW）、YouTube Data API 补充；原始数据存储迁移到对象存储；
  多源融合与复杂事件驱动分析（沿用 `data-and-ingestion-design.md` §06 已有的 P2 定义）。

---

## 10 下一步

本文件完成后，下一份交付是风控措施与保障方案，将裁决本文件与其他已完成文档中
显式留白的风险容忍度问题（LLM 否决超时降级选项、交易所 Key 权限自检的具体实现、
熔断阈值取值）。
