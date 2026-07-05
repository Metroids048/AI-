# 技术架构方案

> 实现状态更新（2026-07-04）：
> 当前仓库已不再是“services 全空实现”状态。真实落地范围已覆盖：
> `shared/models` 统一契约、`/api/v1` 主接口、策略生命周期持久化、Timescale A 级时序仓储、
> carry 回测应用服务、RiskProfile/RiskEvent/Review/Failure/AgentTask/OrderExecution 持久化与首版 gatekeeper。
> 2026-07-03 remediation 后，carry walk-forward/OOS/stress 诊断、系统依赖健康、交易所能力注册表、
> 通知 outbox 和 deterministic Decision Veto/Review executor 已有首版可测试实现；2026-07-04 又补齐了
> `/api/v1/*` 单租户 Bearer 鉴权、Telegram/Webhook 通知派送闭环、`frontend/admin` build 校验恢复，
> 以及 `compose-validate` 脚本化/CI 路径。完整 DSR、Email adapter、Docker runtime smoke、live 下单仍未实现。
> 最新对账请优先查看 [implementation-status-matrix.md](implementation-status-matrix.md)。

## 文档定位

本文件是开发前文档包的第 1 份，回答的是"系统具体怎么部署、代码怎么组织、当前技术缺口在哪里"，
不重复回答"为什么这样分层"（那是 [platform-master-design.md](platform-master-design.md) 的职责）。

真源优先级（与 [v2-integration-reconciliation.md](v2-integration-reconciliation.md) 一致，冲突时上者为准）：

1. 研究报告 `AI_Quant_Research_Platform_完整报告.docx`
2. `AGENTS.md`
3. `docs/architecture/platform-master-design.md`
4. 各子设计文档（domain / data / agent / execution / validation）
5. `v2-integration-reconciliation.md` + v2 PDF（工程实现级细节）
6. **本文件**（技术架构落地细化，且是本文件与后续 7 份开发方案文档中最先产出的一份）

本文件不裁决领域模型、不裁决六层架构边界，只裁决"用什么技术、怎么部署、怎么组织代码、
现在还差什么"。与其他文档的边界见 §13。

---

## 01 架构总览

### 1.1 六层架构 -> 物理落地映射

| 架构层 | 代码目录 | 运行时服务 | 数据存储 |
|---|---|---|---|
| Data Layer | `services/data/` | `celery_worker`（异步抓取/清洗）、`celery_beat`（调度） | TimescaleDB（`ohlcv_bars`/`market_extras`/`macro_events`），Redis（快照/缓存） |
| Strategy Layer | `services/strategy_library/` | `api`（CRUD/查询） | PostgreSQL 关系表（Alembic 管理，`strategies` 起步） |
| AI Agent Layer | `services/agents/` | `celery_worker`（Agent 任务执行） | PostgreSQL（AgentTask 记录），对象引用指向各自产出对象 |
| Validation Layer | `services/validation/` | `celery_worker`（专用 `backtest_queue`）、`freqtrade`（独立容器，REST `:8080`） | PostgreSQL（BacktestRun/PaperRun 等） |
| Execution Layer | `services/execution/` | `celery_worker` 或未来独立 `execution` 服务（P1 决策） | PostgreSQL（OrderExecution/PositionSnapshot），Redis（风险开关状态） |
| Review Layer | `services/review/` | `celery_beat`（每日定时触发） | PostgreSQL（ReviewReport/FailureRecord） |

跨层组件：`apps/api`（FastAPI，六层的统一查询/操作入口）、`frontend/admin`（管理台，P1 起步）、
`research_source/worldquant_adapter`（E 级研究源，不进入主执行链路）。

### 1.2 系统上下文（文字化）

```
外部数据源(A/B/C/D/E) --> services/data 抓取/清洗 --> TimescaleDB / Redis
                                                          |
                                                          v
用户/研究员 --> apps/api --> services/strategy_library --> services/agents（结构化任务）
                                                          |
                                                          v
                              services/validation（回测/优化/OOS/模拟盘准入）
                                                          |
                                                          v
                              services/execution <--> freqtrade(独立容器) <--> 交易所(CCXT/REST)
                                                          |
                                                          v
                                                    services/review --> 知识回写 --> Strategy Layer
```

Risk Engine 横切 Validation/Execution/Review 三层，任何一层拒绝，执行不得继续（见
[execution-risk-review-design.md](execution-risk-review-design.md) §01/§02）。

---

## 02 部署拓扑与服务清单

### 2.1 当前 docker-compose 服务清单（现状盘点）

| 服务 | 镜像/构建方式 | 端口 | 依赖 | 现状 |
|---|---|---|---|---|
| `timescaledb` | `timescale/timescaledb:2.17.2-pg16`（钉版本） | 仅 dev overlay 暴露 `5432` | — | 已建，`init.sql` 建表 |
| `redis` | `redis:7` | 仅 dev overlay 暴露 `6379` | — | 已建 |
| `api` | `python:3.12-slim` + `pip install -e .` | `8000` | timescaledb、redis（健康检查） | 已建，多 router 均挂载在 `/api/v1` |
| `celery_worker` | 同上 | — | redis、timescaledb | 已建，已有 data/backtest/paper 等任务入口；队列路由和 beat 编排仍待强化 |
| `celery_beat` | 同上 | — | redis | 已建，调度表仍待按 ingestion/review/risk heartbeat 补齐 |
| `flower` | 同上 | `5555` | redis | 已建 |
| `freqtrade` | `freqtradeorg/freqtrade:stable` | `8080` | — | 已建骨架，策略目录为空，`stable` 标签未来上线前需钉定日期版本 |
| `grafana` | `grafana/grafana` | `3000` | timescaledb | 已建，仅数据源，无 dashboard |

已知缺口（不在本轮实现，登记为技术债，见 §12）：
- `prometheus` 服务与 Grafana dashboard 骨架已入仓，但本机尚未通过 Docker compose runtime 验证
- `infra/jesse/strategies/.gitkeep` 是纯占位，`pyproject.toml` 未声明 Jesse 依赖，用途未定
- `celery_worker`/`celery_beat`/`flower`/`freqtrade`/`grafana` 均无 `docker-compose.dev.yml` 覆盖项（例如 debug 日志级别只对 `celery_worker` 生效）

### 2.2 环境分层与 compose overlay 规划

当前只有一份 `docker-compose.dev.yml`，与 `environment-and-config.md` 声明的四环境
（`dev`/`test`/`paper`/`live`）不匹配。技术方案：

| 环境 | Compose 组合 | 关键差异 |
|---|---|---|
| `dev` | `docker-compose.yml` + `docker-compose.dev.yml` | 暴露全部端口，`--reload`，debug 日志，`freqtrade dry_run=true` |
| `test` | `docker-compose.yml` + `docker-compose.test.yml` | 独立 Postgres schema/db 名，CI 内一次性起停，不暴露端口 |
| `paper` | `docker-compose.yml` + `docker-compose.paper.yml` | `freqtrade dry_run=true` 但连真实行情，独立 `.env.paper`，独立 Redis DB 编号段 |
| `live` | `docker-compose.yml` + `docker-compose.live.yml` | `freqtrade dry_run=false`，交易所 key 仅 trade 权限、禁提现（见风控方案文档），独立子账户 |

这些 overlay 文件已有首版，但 Docker runtime 尚未在当前 Windows 工作区验证；上线前仍需执行
`docker compose -f docker-compose.yml -f docker-compose.{test,paper,live}.yml config` 与实际起停测试。

---

## 03 技术选型与分层理由（引用式，不重复裁决）

选型已经在 `pyproject.toml`/`docker-compose.yml` 中落地，理由已记录在决策日志，本文件只做索引：

| 技术 | 用途 | 决策依据 |
|---|---|---|
| FastAPI | 平台统一接口层 | AGENTS.md 必需技术栈 |
| PostgreSQL + TimescaleDB | 事实存储 + 时序存储 | ADR-007 |
| Redis | 缓存/风险开关状态/任务协调 | AGENTS.md 必需技术栈 |
| Celery + Redis | 异步任务 | AGENTS.md 必需技术栈 |
| CCXT | 交易所统一适配 | AGENTS.md 必需技术栈 |
| Freqtrade | 加密策略研究/回测/模拟盘（独立容器） | v2-integration-reconciliation.md 框架隔离原则 |
| Backtrader / VectorBT | 多市场/更灵活研究备用引擎 | AGENTS.md 必需技术栈，P1 预留 |
| pandas-ta | 技术指标库（TA-Lib 可选） | ADR-010 |
| Claude API（`anthropic` SDK） | Agent 能力 | AGENTS.md 必需技术栈；接入方式见后续 LLM 接入方案文档 |
| LangChain / LlamaIndex | RAG / 知识检索层 | AGENTS.md 必需技术栈；P0 仅声明依赖，未接入代码 |
| React + Tailwind | 管理后台 | AGENTS.md 必需技术栈，P1 起步 |
| uv + setuptools | 依赖锁定 / 构建后端 | ADR-010 |

---

## 04 代码组织与模块边界

### 4.1 目录结构现状对照

现状与 [appendix-a-repository-structure.md](appendix-a-repository-structure.md) 一致，无漂移：

```
apps/api/            FastAPI 入口、路由、DI、配置
frontend/admin/       Paper-first 管理台（Kline/carry/orders/positions/risk/manual controls）
services/data/        Binance public data ingestion、timeseries repository、capability registry
services/strategy_library/  策略定义/版本/状态、ensemble/meta-label deterministic service
services/agents/       AgentTask 持久化与 deterministic executor registry 首版
services/validation/   carry 回测、persisted application flow、walk-forward/OOS/stress diagnostics
services/execution/    paper preparation、order gatekeeper、execution facts
services/review/       FailureRecord/ReviewReport 与策略失败原因回写
research_source/worldquant_adapter/  WQ 方法论移植（有 README + 3 个模块骨架）
docs/                  设计与方案文档
tests/                 测试（仅契约测试 + API 骨架测试）
```

### 4.2 框架隔离原则的技术落地检查点

原则一（`v2-integration-reconciliation.md`）："Freqtrade / Jesse / VectorBT / CCXT 只出现在
`services/` 与 `infra/`；`apps/api/` 只认 `shared.models`"。技术落地方式：

- **代码层面**：`apps/api/**` 禁止 `import freqtrade`/`import ccxt`/`import backtrader`/
  `import vectorbt`。P1 引入 `services/*` 代码后，建议在 CI 的 `lint-and-test` job 中新增一条
  import 边界检查（可用 `ruff` 的 `TID251`/`banned-api` 规则，或引入 `import-linter` 包，二选一，
  在 P1 实现时决定），而不是仅靠人工审查。
- **通信层面**：Freqtrade 永远经 REST `:8080` 通信，不在 Python 进程内 `import freqtrade`
  （已在 `docker-compose.yml` 落地为独立服务，`apps/api` 目前也确实没有引入该依赖）。
- **契约层面**：跨层对象一律先在 `shared/models/` 定义 Pydantic 契约（ADR-008），
  `apps/api` 与 `services/*` 之间只传递 `shared.models` 对象，不传框架原生对象
  （如 Freqtrade 的 `Trade`、CCXT 的原始 dict）。

---

## 05 API 与接口层设计规范

### 5.1 六大接口簇边界

沿用 `platform-master-design.md` §8.2 定义的 6 个接口簇，本文件补充设计规范（非字段级 schema）：

- `Strategy Lifecycle APIs`
- `Backtest & Optimization APIs`
- `Paper/Live Run APIs`
- `Risk Event APIs`
- `Review & Reporting APIs`
- `Reference Data & Source Ingestion APIs`

字段级 OpenAPI schema 属于 Phase 1 开发任务（`delivery-checklist.md` "API schema" 项），
由 Coding Agent 产出草稿 + 人工审核确认，不在本文档产出，避免文档与代码字段漂移。

### 5.2 通用设计规范

- **路径版本化**：`/api/v1/{resource}`，为未来 breaking change 预留版本号位。
- **资源命名**：与 `shared.models` 类名对应的复数小写蛇形路径（如 `Strategy` -> `/strategies`）。
- **统一错误结构**：`{"error_code": str, "message": str, "detail": dict | None}`，
  与 Pydantic `ValidationError` 统一转换，不直接暴露框架原生异常堆栈。
- **分页**：列表接口统一 `limit`/`offset` + 响应体 `{"items": [...], "total": int}`。
- **鉴权占位**：P0/P1 仅内部研究台场景，暂不做复杂 RBAC（AGENTS.md 非目标），
  但必须有一个最小访问控制（如固定 API Key header 或本地网络限制），具体强度由风控方案文档裁决，
  本文件只登记这是一个必须在 P1 结束前补齐的空缺（见 §12）。
- **写操作幂等性**：所有创建类接口建议支持 `Idempotency-Key` 或客户端指定业务主键
  （如 `strategy_key`），避免重复提交产生重复策略/重复回测任务。

---

## 06 异步任务与调度架构

### 6.1 队列规划

当前 `celery_worker` 已有首版任务模块（data/backtest/paper），但队列隔离与 beat 调度仍不完整。
Phase 1 队列划分继续按以下方向收敛：

| 队列 | 用途 | 理由 |
|---|---|---|
| `default` | 轻量任务（状态更新、通知发送） | 避免被重任务阻塞 |
| `backtest_queue` | 回测/参数优化/样本外验证 | 计算密集，需与数据同步隔离（已在 docker-compose 声明） |
| `ingestion_queue`（P1 新增） | A/B/C/D/E 级数据抓取/清洗 | 与回测任务分离，防止数据抓取延迟拖慢回测排队 |
| `agent_queue`（P1 新增） | 11 个 Agent 任务（含 LLM 调用） | LLM 调用延迟不可控，需与其他任务隔离，避免拖慢回测/数据队列 |

### 6.2 Beat 调度规划（当前为空，Phase 1 填充）

按 `docs/architecture/agent-and-orchestration-design.md` 编排主链路，Beat 至少需要以下周期任务：

- A 级市场数据同步（`services/data`，高频，如每 1-5 分钟或由 WebSocket 常驻进程替代 Beat 轮询，
  具体频率见后续外部数据源接入方案文档）
- 数据缺口检查 `gap_checker`（对应 Makefile 中已声明但未实现的 `data-check`）
- 每日复盘触发（`services/review`，固定时点，如 UTC 每日 00:00）
- 风险规则巡检（Risk Engine 心跳/账户状态检查）
- 模拟盘/实盘持仓快照定时落库（`PositionSnapshot`）

具体 cron 表达式和任务函数在 Phase 1 编码阶段确定，本文件只定义任务类别和队列归属。

### 6.3 任务设计原则

- 所有 Celery 任务必须幂等（同一 `task_id`/业务键重复执行不产生重复副作用）。
- 任务失败必须区分错误类别（抓取失败/校验失败/写入失败/下游依赖不可用），对应
  `data-and-ingestion-design.md` §07 的"失败必须区分阶段"要求，写入 `AgentTask.error_summary`
  或对应的 `IngestionJob` 失败字段，不能只记录笼统的 "task failed"。
- 涉及外部 API（交易所/新闻源/社媒）的任务必须有重试上限与退避策略，避免触发对端限流。

---

## 07 存储架构

### 7.1 表归属现状（已通过 ADR-007 + v2-integration-reconciliation.md 固化）

| 存储 | 归属方 | 现有对象 | Phase 1 待补 |
|---|---|---|---|
| TimescaleDB 超表 | `infra/timescale/init.sql` | `ohlcv_bars`、`market_extras`、`risk_events`、`macro_events` | 无需 Alembic 管理，新增字段直接改 init.sql（仅限新环境重建；已跑生产库需要手写迁移 SQL） |
| PostgreSQL 关系表 | Alembic（`migrations/`） | `strategies` | `strategy_versions`、`backtest_runs`、`paper_runs`、`live_runs`、`review_reports`、`failure_records`、`signal_ensembles`、`meta_labels`、`agent_tasks` |
| Redis | 无持久化契约（缓存/协调用） | Celery broker(db1)/backend(db2)、应用缓存(db0) | 风险开关状态 key 命名空间（如 `risk:switch:{scope}`）、Agent 任务短期协调 key，均需在 Phase 1 定义命名规范 |

**硬约束（延续 v2-integration-reconciliation.md）**：任何表不得被 TimescaleDB init.sql 与
Alembic 同时创建；Order Book 数据只进 Redis，永不落库。

### 7.2 原始数据/大文件存储（待决策，登记为 P1 决策项）

新闻全文、社媒原始 payload、研究论文/代码快照等非结构化内容尚未确定存储位置。候选方案（P1 决策，
非本轮裁决）：本地文件系统路径 + PostgreSQL 存元数据引用，或对象存储（MinIO/S3 兼容）。
决策需等待外部数据源接入方案文档确定各数据源的实际数据量级后再定。

---

## 08 配置与环境管理

### 8.1 当前 Settings 覆盖缺口

`apps/api/config.py` 的 `Settings` 只声明了 8 个字段（`app_env`/`app_name`/`api_host`/
`api_port`/`postgres_url`/`redis_url`/`celery_broker_url`/`celery_result_backend`），
而 `.env.example` 中的交易所 Key、宏观/新闻/社媒/研究源 Key、Freqtrade 凭据均未纳入
`Settings` 模型（`extra="ignore"` 使其被静默接受但未被类型校验和统一管理）。

Phase 1 技术要求：`services/data`、`services/execution` 等模块开始读取这些变量时，必须
同步把它们补入 `Settings`（或按数据分级拆成 `MarketDataSettings`/`RiskSettings` 等子配置类），
禁止在业务代码里直接 `os.environ.get(...)` 绕开 `Settings` 统一管理。

### 8.2 四环境落地方式

呼应 §2.2 的 compose overlay 规划，`.env` 也需要按环境拆分：`.env.dev.example`（当前
`.env.example` 改名或新增副本）、`.env.test.example`、`.env.paper.example`、`.env.live.example`。
四份文件字段结构一致，仅取值不同（尤其是数据库名、Redis DB 编号段、交易所 Key 的权限级别）。

### 8.3 凭据管理原则的技术保证

风控方案文档会定义"交易所 Key 必须仅有交易权限、禁止提现"这一业务规则；本文件定义**如何在技术上
保证**：

- 凭据一律通过环境变量/`.env` 注入，不写入代码或配置文件（现状已符合，`config.example.json`
  已注明真实配置文件被 gitignore）。
- Phase 1 建议在服务启动时增加一次性 API Key 权限自检（调用交易所"账户权限查询"接口，
  确认返回的权限位不包含提现权限，若包含则拒绝启动并告警）——具体实现留给执行层方案，
  本文件只登记这是一个必须存在的启动期检查点。
- `paper`/`live` 环境的凭据与 `dev`/`test` 环境物理隔离（不同 `.env` 文件、不建议共用同一交易所
  子账户）。

---

## 09 可观测性与告警

### 9.1 现状

Grafana 已配置 TimescaleDB 数据源，但 `dashboards/` 目录为空，且没有 Prometheus，
所以 Grafana 目前只能查数据库里已有的数据，无法看到 API/Celery/Redis 自身的运行时指标。
全仓库范围内已有持久化 `notification_outbox` 与 `NotificationOutboxItem` 契约，用于记录高严重度风险事件、
数据中断和日报生成等通知意图，并支持读取、手工创建与配送结果回写。当前首批 Telegram/Webhook 推送 adapter
已接入真实 dispatch 闭环，但 Email 仍未实现，且本机尚未做真实运维凭据演练。`.env.example`
里的 `TELEGRAM_BOT_TOKEN` 是 D 级数据源采集用途；通知出站应使用独立配置，不与采集链路复用同一语义。

### 9.2 Phase 1/2 规划

- 新增 `prometheus` 服务 + 对应 exporter（`postgres_exporter`、`redis_exporter`，
  Celery 可用 `flower` 已有指标或额外接 `celery-prometheus-exporter`），补齐系统健康监控。
- Grafana dashboard 按用途分三类：系统健康（服务存活/延迟/队列积压）、策略绩效（回测/模拟盘/
  实盘关键指标趋势）、风险事件（`RiskEvent` 频次与分级分布）。
- 新增通知层，用于把 Review 日报、风险熔断、执行异常推送给人工，承接 freqtrade/daily_stock_analysis
  生态里常见的"Telegram Bot 推送"模式，但用途是**出站告警**，与 D 级数据源的 Telegram 采集是两条
  独立链路（不可复用同一个 Bot Token 语义）。具体触发规则和消息格式，留给后续
  "24 小时自动实时交易运行方案" 文档裁决，本文件只登记这是一个当前完全缺失、必须补齐的模块。

---

## 10 CI/CD 与质量门禁

### 10.1 现状

`.github/workflows/ci.yml` 含两个 job：`lint-and-test`（ruff + mypy + `pytest -q -m "not
integration"`）、`compose-validate`（仅 `docker compose config` 语法校验，不真实起服务）。
`.pre-commit-config.yaml` 覆盖 ruff/格式检查/基础文件卫生检查/mypy。

### 10.2 缺口与规划

- 目前没有真正跑起服务的集成测试 job（标记为 `integration` 的测试尚未运行在任何 CI 环节）。
  Phase 1 待 `services/*` 有实现后，需要新增一个用 `docker compose up` 起 TimescaleDB/Redis
  的集成测试 job，独立于 `lint-and-test`，避免拖慢主 CI。
- P0 明确非目标是"绕过人工审核的高风险自动化"，因此不建立自动部署流水线属于合理现状，
  不作为缺口登记；`paper`/`live` 环境的启动仍应是人工触发的运维动作。

---

## 11 多市场/多资产扩展点

当前主市场固定为 `BTC/USDT` 永续（AGENTS.md 初始市场范围）。为满足"数据模型从第一天起
必须支持扩展到 ETH/SOL/A股/美股/黄金/纳指"的要求，技术落地方式：

- `Strategy.market`/`symbol_scope` 字段已在领域模型和 `migrations/0001_create_strategies.py`
  中支持任意市场枚举和符号数组，无需改表结构即可扩展加密货币内的新交易对（ETH/SOL）。
- 跨资产类别扩展（A股/美股/黄金/纳指）需要独立的交易所/数据源适配层，CCXT 只覆盖加密货币，
  这部分适配器的技术选型是 P2 决策项，本文件不预先选型，只登记：新增资产类别时必须新增独立的
  `MarketDataFeed` 实现（复用 `data-and-ingestion-design.md` §04 的接口框架），不得改动
  现有 A 级加密数据接入代码。
- Freqtrade 目前只承载加密策略；跨资产策略研究预期落在 Backtrader/VectorBT（已在依赖中预留）。

---

## 12 已知技术缺口跟踪表

本表汇总当前代码/配置层面的缺口，供 Phase 1/2 排期参考。"目标阶段"是建议值，非本文档裁决——
实际排期以路线图文档和用户确认为准。

| 缺口 | 所属层/模块 | 影响 | 建议目标阶段 |
|---|---|---|---|
| Celery 已有首版任务入口，但缺少完整路由策略/beat 编排 | AI Agent / Validation / Data | data/backtest/paper 等入口可执行，生产级队列隔离和周期调度仍不足 | P1 |
| `services/{data,agents,validation,execution,review}` 均为空实现 | 对应六层 | 该描述已过期：Data/Validation/Risk/Review/Agent/Execution 已有首版真实实现，剩余缺口见 `implementation-status-matrix.md` | 已从“全空”收敛到“partial/implemented` 混合状态 |
| 六大接口簇已从 skeleton 扩展到多条持久化/服务闭环，但仍有部分能力是 deterministic seam | API | 策略、验证、风险、复盘、paper console 已有真实路径；LLM、live、真实通知仍待补 | P1/P2，随各服务实现同步补 |
| `Settings` 已覆盖 `.env.example`，但多数变量尚未被业务模块真正消费 | 配置管理 | 类型入口已统一，仍需避免后续模块重新绕过 Settings 直读环境变量 | P1，随首次使用该变量的模块同步补 |
| 已有 Telegram/Webhook 第一批出站 adapter，但缺 Email 与真实运维凭据演练 | Review / Risk / Execution | 高严重度通知已能派送并回写状态，但值班链路仍未完成全渠道验收 | P1（配合 24 小时运行方案文档） |
| Prometheus/Grafana 骨架存在，但未完成 runtime 验证与指标覆盖 | 可观测性 | 有配置资产，仍无法证明系统运行时健康监控可用 | P1/P2 |
| `migrations/` 仅有 `strategies` 表，`SignalEnsemble`/`MetaLabel`/`BacktestRun`/`PaperRun`/
  `ReviewReport`/`FailureRecord`/`AgentTask` 等对象未建表 | Strategy/Validation/Review | 该描述已过期：`0001` 已覆盖主生命周期表，`0002` 已补 `OptimizationRun`、`RiskProfile`、`ReviewReport`、`FailureRecord`、`AgentTask`、`LiveRun`、`OrderExecution`、`PositionSnapshot` 等关系表；SignalEnsemble/MetaLabel 也已纳入迁移 | 后续重点转向服务层能力而非“是否有表” |
| `anthropic`/`langchain`/`llama-index` 已声明依赖但零代码引用 | AI Agent Layer | LLM 能力尚未接入 | P1（见后续 LLM 接入方案文档） |
| 仅有单租户 Bearer 管理令牌，无多用户账号体系/RBAC | API | 已补最小管理面保护，但仍不适合扩展到多操作者或细粒度权限场景 | P2 |
| `infra/jesse/` 为占位目录，未在依赖中声明，用途未定 | Validation | 目录存在但无实际路径引用它 | P1 决策：启用或移除 |
| `test`/`paper`/`live` overlay 已存在，且 `compose-validate` 已脚本化，但未在本机 Docker 做 runtime smoke | 部署 | 环境隔离与 CI 语法校验路径已具备，仍缺可运行主机上的启动/停止证据 | P1（paper 上线前必须） |
| Freqtrade 镜像标签为 `stable`（浮动标签） | Execution | 生产前版本不可控 | live 上线前必须钉定日期版本 |

---

## 13 与其他文档的边界

避免与后续 7 份文档重复裁决同一问题：

- **产品需求文档（PRD）**：负责用户故事/验收标准，不重复本文件的部署/技术选型内容。
- **各模块功能清单**：负责逐模块列出功能点，本文件只在 §01/§04 给出模块到代码目录的映射。
- **量化策略库方案**：负责策略收集/评分/淘汰机制，不涉及本文件的存储/队列架构（策略对象的表结构由本文件 §07 承接）。
- **LLM API 接入方案**：负责 Prompt 结构/调用时机/成本控制，本文件只在 §06 定义 Agent 任务的队列归属，不涉及具体 LLM 调用设计。
- **24 小时自动实时交易运行方案**：负责调度细节、通知触发规则、心跳/降级策略，本文件只在 §09 登记通知层是缺口，不裁决触发规则。
- **外部数据源接入方案**：负责各数据源的具体频率/API 选型/存储路径，本文件只在 §07.2 登记原始数据存储是待决策项。
- **风控措施与保障方案**：负责风险规则取值/熔断阈值/凭据权限业务规则，本文件只在 §08.3 定义"技术上如何保证"，不裁决具体阈值。

---

## 14 下一步

建议排期：先处理 §12 中标记为"P1（优先）"的缺口（Celery 任务图、`services/data` +
`services/validation` 骨架、Strategy 相关表补全），再进入其余模块编码，与
`appendix-b-feature-phasing.md` 的既有排序保持一致。

本文件完成后，下一份交付是产品需求文档（PRD）。
