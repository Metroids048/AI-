# AI 量化研究平台 — 多维度查缺补漏审查报告

> 审查时间：2026-07-08
> 审查范围：`C:\Users\win\Desktop\AI--main` 全仓库
> 审查基准：`AGENTS.md` 六层架构定义 + `docs/architecture/implementation-status-matrix.md` 对账表
> 审查方法：4 路并行代码级审查 + 对账表交叉验证（trust but verify）

---

## 0. 总体结论

平台骨架完整度约 **65%**，六层物理分包清晰、领域模型（17 个）与 API 路由（14 个）齐备，核心回测指标与风控 Gatekeeper 是**真算真校验**而非占位，密钥管理基本规范。但存在 **3 类系统性问题**直接阻断"研究→验证→执行→复盘→迭代"闭环：

1. **声明与实现严重背离**：Freqtrade / Backtrader / VectorBT / LangChain / LlamaIndex / anthropic SDK 在 `pyproject.toml` 与 `AGENTS.md` 中声明，代码层**零 import**；Claude 实际走裸 httpx 调用。
2. **状态机名存实亡**：Strategy 表的 `backtest_status` / `live_status` 全链路**无任何回写代码**，`paper_status` 只更新 PaperRun 不回写 Strategy 表。
3. **关键 Agent 零 executor**：10 个 Agent 中 Coding / Backtest / Optimization / Risk 四个**无任何执行器**，提交即落 "no executor registered"；参数优化引擎完全缺失，`OptimizationRun` 永远停在 `queued`。

此外发现 **1 项严重资金安全风险**：真实 Binance API Key/Secret 明文存于本地 `.env`。

> ⚠️ 建议在补齐 P0 级断点前，**不得宣称 Phase 1 完成**，也不得推进小资金实盘。

---

## 维度 1：功能完整性

### 整体评价
对账表大部分声称属实，但存在 3 处高估——Strategy 状态机字段定义齐全却全链路无回写；10 个 Agent 中 4 个零 executor；参数优化只有 ORM+API 落库无执行引擎。LLM 接入（Anthropic/OpenAI 兼容 + Fallback 链）真实可用，但仅覆盖 2 个 task，未达 AGENTS.md 要求的 10 Agent 全覆盖。B/C/D/E 级数据源大量缺失或仅 schema。

### 缺失功能点清单

| 模块 | 缺失点 | 优先级 | 证据路径 |
|---|---|---|---|
| Data Layer A 级 | 仅 Binance 单交易所，无 OKX/Bybit 扩展 | P2 | `services/data/binance.py` 全文；`capabilities.py` 仅 binance |
| Data Layer B 级 | 宏观默认 disabled，依赖 `forexfactory_rss_url` 配置，FOMC/CPI 无结构化字段 | P1 | `services/data/macro_calendar.py:27` |
| Data Layer D 级 | 仅 Twitter watchlist，无 Telegram/Reddit/YouTube 真实采集 | P1 | `services/data/social.py:20` |
| Data Layer E 级 | 向量库/LlamaIndex 检索层未实现，RAG 止于 manifest 摘要 | P1 | `implementation-status-matrix.md:8`；`research_source/open_source_strategy_library/` 无 vector store |
| **Strategy Layer** | **Draft→Contract 规则化是硬编码模板（按 title 关键词），非 AI 规则化** | **P0** | `services/agents/service.py:372-420` `_draft_from_open_source_idea` |
| **Agent Layer** | **Coding/Backtest/Optimization/Risk Agent 零 executor** | **P0** | `services/agents/service.py:226-231` 默认返回 `executor_registered: False` |
| Agent Layer | LLM 仅覆盖 2 个 task（classify_event/pre_execution_veto），无 LangChain/LlamaIndex 深度推理 | P1 | `services/agents/llm_runtime.py:290` `_build_prompt` |
| Validation Layer | 通用回测 `POST /backtests` 仍是同步落库 seam，非真异步执行 | P1 | `apps/api/routers/backtests.py:92` |
| **Validation Layer** | **参数优化引擎完全缺失，无 hyperopt/optuna 调用** | **P0** | `services/validation/` grep `hyperopt/optuna` 零匹配；`backtests.py:198` 仅 `create_run` |
| Validation Layer | Deflated Sharpe 通用校正未接入，仅 carry lane 调用 | P2 | `services/validation/metrics.py:52` 已实现但仅 `carry.py:90` 调用 |
| Execution Layer | 默认 `NullExchangeGateway` 全 `NotImplementedError`，需配 binance 凭据 | P1 | `services/execution/gateway.py:27-54` |
| Review Layer | 失效模式识别仅 `failure_type` 去重，无算法聚类 | P1 | `services/review/service.py:50` |

---

## 维度 2：功能质量

### 整体评价
核心回测指标（Sharpe/PF/MaxDD/Expectancy/DSR）和风控 Gatekeeper 是**真算真校验**，非占位；但策略草稿生成、压力测试、复盘聚合明显偏薄，存在大量硬编码阈值与裸 `except` 吞异常。测试以单元+轻量集成为主（46 个测试文件），关键路径覆盖较好，但 mock 较多导致真实交易/LLM 路径缺集成验证。

### 问题清单

| 问题 | 严重度 | 证据路径 | 改进建议 |
|---|---|---|---|
| 准入阈值硬编码且跨文件重复（min_sharpe=1.0/PF=1.3/MaxDD=0.25） | 高 | `services/validation/carry.py:115-122`、`services/validation/admission.py:26-33` | 抽到 `RiskProfile` 或 `ValidationPolicy` 配置对象，注入而非内联 |
| 开源策略→草稿生成靠标题关键词匹配，规则全硬编码 | 高 | `services/agents/service.py:372-420` | 改为基于 source manifest 的元数据驱动映射，或交由 LLM 结构化生成 |
| `paper_runtime.run_cycle` 单函数 ~350 行、6 层嵌套 if/else | 高 | `services/execution/paper_runtime.py:91-447` | 拆分为 `_scan`/`_manage_open`/`_open_new`/`_close` 等私有方法 |
| Agent `_execute` 长 if/elif 链按 (agent_type,task_type) 分派 | 中 | `services/agents/service.py:81-231` | 改为 handler 注册表 `dict[(agent,task)->callable]` |
| 压力测试只是 expectancy 的线性减损，非真实场景重放 | 中 | `services/validation/stress_scenarios.py:11-18` | 至少做 funding_flip 逐笔重放或 Monte Carlo reshuffle |
| `review/service.py` 仅为 CRUD+轻聚合，无归因分析 | 中 | `services/review/service.py:43-62` | 加入失败聚类、PnL 归因、与假设回扫 |
| 大量 `except Exception: pass` 静默吞异常 | 中 | `services/data/market.py:148-149,162-163,170-171`、`services/execution/gateway.py:224`、`services/agents/llm_runtime.py:342` | 至少 log warning + 返回降级状态对象 |
| Freshness 阈值 `timedelta(hours=2)` 硬编码 | 低 | `services/execution/gatekeeper.py:29`、`services/data/market.py:236` | 走 `settings.market_data_stale_seconds` |
| 测试用 Stub 覆盖真实 CCXT/网关/LLM，无端到端集成 | 中 | `tests/services/test_binance_gateway.py:7`、`test_execution_runtime_api.py:33` | 增加 marked-skip 的真实 testnet 烟雾测试 |
| `runs.py:558 create_order` 无 try/except，与其他端点错误处理不一致 | 低 | `apps/api/routers/runs.py:558-560` | 包裹 `api_error` 并与 `step_paper_run` 对齐 |

---

## 维度 3：前后端架构设计

### 整体评价
六层物理分包清晰（apps/services/shared/research_source 边界明确），但存在**一处致命跨层**——`services/` 反向 import `apps.api.config`（10 处），破坏了分层独立性。API 层有 v1 版本与统一错误包络，但**缺分页/排序/过滤规范**；前端用 `.jsx` 无类型安全，且 `useConsoleData` 单 hook 承担 8 路并发+WS（290 行），可维护性差；前后端字段已发现多处不一致。

### 问题清单

| 问题 | 类型 | 证据路径 | 改进建议 |
|---|---|---|---|
| **services 反向依赖 apps/api**（10 处） | 跨层依赖 | `services/agents/service.py:7`、`services/database.py:13`、`services/execution/gateway.py:8`、`services/data/news.py:13`、`services/notifications.py:13` 等 | 将 `Settings` 下沉到 `shared/config.py`，apps/api 与 services 都从 shared 导入 |
| 列表接口无分页/游标，全量返回 | 接口规范 | `strategies.py:78`、`runs.py:130`、`review.py:51`、`console.py:37`（`[-5:]` 切片在应用层硬切） | 统一 `limit/offset` 或 cursor + `CollectionResponse` 带 `next_cursor` |
| `/market/news`、`/market/macro-events` 返回裸 `dict` 而非 `CollectionResponse` | 契约一致性 | `apps/api/routers/market.py:457,480` | 改用 `CollectionResponse[NewsItem]` |
| GET 请求触发副作用（`refresh=true` 拉取 RSS） | REST 规范 | `market.py:466-475`；前端 `OpsConsole.jsx:27`、`ReviewCenter.jsx:24`、`ResearchDesk.jsx:19` 都带 `refresh=true` | 拆为 `POST /market/news/refresh`，GET 只读 |
| 前端无 TypeScript，字段不一致编译期不可见 | 类型安全 | `frontend/admin/package.json`（.jsx 全量）、`ValidationCenter.jsx:48-49` 读 `annualized_carry`/`signal_status` 但后端 `FundingArbitrageSignal` 无此字段 | 迁移到 .tsx，或至少用 JSDoc + zod 校验响应 |
| `useConsoleData` 单 hook 290 行，8 路并发+WS+upsert | 组件组织 | `frontend/admin/src/hooks/useConsoleData.js:12-289` | 拆为 `useMarketSnapshot`/`useConsoleOverview`/`useExchangeStream` 等独立 hook |
| `client.js` 错误读取 `payload?.detail`（后端 detail 是 dict） | 契约对齐 | `frontend/admin/src/api/client.js:27` vs `shared/models` ApiError.detail | 前端应读 `payload.message`，detail 仅作展示对象 |
| 前端 `FeedPanel` 在不同页面 prop 名不同（`rows` vs `items`） | 组件复用 | `ReviewCenter.jsx:90` vs `OpsConsole.jsx:119` | 抽到 `components/Common.jsx` 统一 props 签名 |
| `TradingConsolePanels.jsx:150` 硬编码 `account_equity:10000` 提交订单 | 契约对齐 | 前端 vs 后端 `ExecutionRiskState` 需要 11 字段 | 前端应从 `manualContext` 或 `/execution/trading-status` 拉取真实 risk_state |
| `routers/__init__.py` 仅有两行 docstring，无路由聚合 | 装配 | `apps/api/routers/__init__.py:1-2` | 显式 `__all__` 导出各 router |

---

## 维度 4：业务流程与数据闭环

### 整体评价
核心闭环在"策略来源→Idea→Draft→回测→模拟盘→复盘"前半段基本打通（含 Celery beat 7×24 调度），但存在 **3 处致命状态机断层**与 **1 处知识复用断点**：Strategy 表的三个 `*_status` 字段中 `backtest_status`/`live_status` 全链路无回写代码，`paper_status` 只更新 PaperRun 表不回写 Strategy 表；Review 沉淀的 FailureRecord 从不被 Research/Strategy Agent 读取复用，"失败→迭代"闭环在复用环节断裂。优化环节无执行引擎导致"回测→优化→OOS"链条物理断开。

### 流程断点清单

| 断点位置 | 问题描述 | 影响 | 证据 |
|---|---|---|---|
| **Strategy.backtest_status 回写** | grep `row.backtest_status =` 全仓库零匹配；回测完成后只更新 `BacktestRun.run_status`，Strategy 表字段永远 `not_started` | 状态机失效，无法据 `backtest_status` 判断策略是否已通过回测准入模拟盘 | `services/strategy_library/models.py:112`；`services/validation/carry.py:198` 只设 `run_status="completed"` |
| **Strategy.live_status 回写** | `LiveExecutionService` 不更新 `LiveRun.live_status`，更不回写 Strategy 表 | 实盘运行状态不可观测，无法驱动"实盘运行→复盘"迁移 | `services/execution/live.py` 全文无 `live_status=` 赋值；grep `row.live_status =` 零匹配 |
| **Strategy.paper_status 回写** | `paper_runtime.py:430` 只更新 `PaperRun.paper_status="running"`，不回写 `Strategy.paper_status` | 策略级 paper 状态与 run 级状态脱节 | `services/execution/paper_runtime.py:430` |
| **失败知识复用断点** | `AgentTaskService` 仅 `review_repo.create_failure`（写），从不 `list_failures`（读）；Research/Strategy Agent 生成新 idea 时不参考历史失败 | AGENTS.md §6"失败知识沉淀供 Research/Strategy Agent 复用"未落地，同类失败会重复 | `services/agents/service.py:354` 唯一调用是写；grep `list_failures` 在 `services/agents/` 零匹配 |
| **优化环节物理断开** | `OptimizationRun` 落库后无 worker 执行，`run_status` 永远 `queued`，无 `best_candidate_summary` 回填 | "回测→优化→OOS"链条在优化处断，OOS 只能基于原始参数 | `apps/api/routers/backtests.py:199` `create_run` 后无 enqueue；`services/validation/` 无 optimize 执行函数 |
| Draft→Contract 规则化 | `_draft_from_open_source_idea` 按 title 含 "funding"/"grid" 等关键词硬编码 3 套规则模板 | Coding Agent 缺位导致策略规则化非 AI 驱动，违背 AGENTS.md §1"先规则化再回测" | `services/agents/service.py:372-420` |
| failure_reasons 回写面窄 | 仅 gatekeeper reject / paper stoploss / alpha reject 三处触发 `append_failure_record`；回测不达标/优化失败不回写 | 策略 iteration_history 缺失大量失败证据 | `repository.py:1185`；`gatekeeper.py:225`；`paper_runtime.py:624` |

---

## 维度 5：第三方集成

### 整体评价
平台骨架的 adapter 隔离意识较强（Binance/LLM/通知均有 Protocol 边界），密钥统一走 `Settings`/`.env` 且 `.env` 已 gitignore，源码无硬编码密钥。但存在严重的"**声明 vs 实现**"背离：Freqtrade/Backtrader/VectorBT/LangChain/LlamaIndex/anthropic SDK 全部仅在 `pyproject.toml` 的 optional extras 和 `AGENTS.md` 中声明，代码层**零 import**；Claude 实际走裸 `httpx` 调用；Email 通知、Reuters/Bloomberg、A股系统、arxiv/Reddit/YouTube 等 B/C/E 级数据源基本是空壳或缺失。

### 第三方集成清单

| 服务 | 接入方式 | 鉴权 | 异常处理 | 成熟度 | 证据路径 |
|---|---|---|---|---|---|
| Binance 市场数据 | REST(urlopen)+WS(websockets)，CCXT 可选 lazy import | 公共端点无需 key；CCXT `enableRateLimit` | WS 有 reconnect；REST fallback 无重试 | 完整 | `services/data/binance.py:139,362` |
| Binance 实盘执行 | CCXT lazy import | apiKey/secret 走 settings | 无重试，仅 try/except 降级 | 完整(脆弱) | `services/execution/gateway.py:198-210` |
| Claude/Anthropic | **裸 httpx POST `/v1/messages`**（未用官方 SDK） | `x-api-key` header | 单次请求无重试；FallbackChain 仅切 provider | 完整 | `services/agents/llm_runtime.py:25-79` |
| OpenRouter/GitHub Models | 裸 httpx OpenAI 兼容 | Bearer token | 401/403/429/5xx 触发 fallback | 完整 | `llm_runtime.py:82-150` |
| 金十/CoinDesk/TheBlock/ForexFactory RSS | httpx GET + ET.fromstring | 无 | `raise_for_status` 后无重试 | 完整(脆弱) | `services/data/news.py:101-110`，`macro_calendar.py:52-61` |
| Reuters/Bloomberg RSS | — | — | — | **缺失** | `config.py:62` 默认空串 |
| SEC Filing | 声明 RSS | — | — | **骨架(必崩)** | `config.py:63` URL 指向 JSON 端点，`news.py:145` 按 RSS 解析必抛 ParseError |
| Twitter/X | httpx GET v2 API | Bearer token | 无重试 | 完整 | `services/data/social.py:31-44` |
| Telegram 通知 | httpx POST bot API | bot_token in URL | dispatcher 指数退避 max=3 | 完整 | `services/notifications.py:36-63,228-231` |
| Webhook 通知 | httpx POST | URL 内嵌 | 同上退避 | 完整 | `notifications.py:66-91` |
| Email 通知 | — | — | — | **缺失** | `notifications.py` 无 EmailAdapter |
| **Freqtrade** | 声明 REST :8080 | 声明 user/pass | — | **骨架(从未调用)** | `config.py:80-82` 三配置项全代码无引用；`docker-compose.yml:100` 跑容器无人调 |
| **Backtrader/VectorBT** | — | — | — | **缺失** | `pyproject.toml:47-50` 声明，全仓零 import |
| **LangChain/LlamaIndex/anthropic SDK** | — | — | — | **缺失** | `pyproject.toml:42-45` 声明，全仓零 import |
| GitHub 研究源 | urllib GET api.github.com/raw | 无 token(公开仓) | OSError 捕获 | 完整 | `research_source/open_source_strategy_library/ingestion.py:25-64` |
| WorldQuant | 本地 CSV/JSONL 扫描 | 无 | FileNotFoundError | 完整(本地 only) | `worldquant_adapter/local_alpha_scanner.py:27-60` |
| arxiv/Reddit/YouTube/A股 | — | — | — | **缺失** | `config.py:77` arxiv_categories 无 fetcher |
| Trading Economics/Alpha Vantage | — | key 声明 | — | **缺失** | `config.py:55-56` key 无任何引用 |
| Postgres/Redis/Celery/Prometheus/Grafana | docker-compose 容器 | 弱默认口令 | healthcheck 有 | 完整 | `docker-compose.yml:13,117` |

### 主要风险点
1. **声明与实现严重背离**：`pyproject.toml:42-52` 的 `llm`/`quant` optional extras 全仓零 import，AGENTS.md "Required Tech Stack" 大量虚标。Claude 经裸 httpx 调用，绕过官方 SDK 的类型安全与重试机制。
2. **SEC EDGAR URL 错配必崩**：`config.py:63` 指向 JSON 端点，`news.py:145` 按 RSS 解析，运行时必然 `ParseError`。
3. **Freqtrade REST 客户端从未实现**：`docker-compose.yml:100-111` 跑了 freqtrade 容器却无服务调用 :8080，"Freqtrade 经 REST 隔离"仅是注释承诺。
4. **缺统一重试/退避/熔断**：除 `NotificationDispatcherService` 有指数退避外，Binance REST fallback、Anthropic/OpenRouter httpx、RSS 抓取、Twitter 抓取均无重试。
5. **adapter 边界破例**：`apps/api/routers/market.py:254` 在路由层直接 `import ccxt`，破坏了 `services/data/binance.py` 设立的 adapter 隔离边界。

---

## 维度 6：安全性与性能

### 安全性

#### 整体评价
项目在鉴权（Bearer token + `secrets.compare_digest`）、SQL 参数化（SQLAlchemy Core）、gatekeeper 止损强制检查等基础面做得扎实，`.env` 也正确排除出 git。但存在**真实密钥落盘本地**、AGENTS.md 三条硬规则未在代码中强制、**无 kill switch/二次确认/限流**、基础设施弱凭证等高危问题。

| 风险 | 等级 | 证据路径 | 修复建议 |
|---|---|---|---|
| **真实 Binance API Key/Secret 明文存于本地 `.env`** | **严重** | `.env:33-34`（真实 64 位密钥） | **立即轮换该密钥**；生产用 vault/KMS 注入，禁止明文落盘 |
| **AGENTS.md 禁 Martingale，代码零检查** | **严重** | `AGENTS.md:98` vs `gatekeeper.py:95-208`（无任何加仓倍率/历史亏损后翻倍检测） | 在 gatekeeper 增加加仓前后仓位对比 + 亏损后加仓倍率检测 |
| **无 kill switch / 全局熔断** | **严重** | 全仓库无 `kill_switch`（仅 `AGENTS.md` 提及） | 增加 Redis 全局开关，gatekeeper 下行检查 |
| 无二次确认 / 无金额频率上限 | 高 | `runs.py:184-198`（manual-order 直接下单，无 confirm/2FA/rate-limit）；全局无 `slowapi`/`throttle` | 实盘下单要求二次确认 token；接入 slowapi 限流 |
| PostgreSQL/Redis/Grafana 弱凭证 | 高 | `docker-compose.yml:13`（postgres/postgres）、`:117`（admin/admin）、Redis 无密码 | 生产 compose 覆盖强密码；Redis 设 requirepass |
| 基础设施端口全暴露（Flower/Freqtrade/Grafana/Prometheus） | 高 | `docker-compose.yml:94,111,123,136`；`live.yml:25` 仅关 Flower | live 环境关闭所有非必要端口或绑定 127.0.0.1 |
| 单租户静态 token，无过期/轮换/RBAC | 中 | `auth.py:57`（compare_digest 比对静态 token）；无 JWT/refresh/RBAC | 升级为 JWT + 角色分级（operator/viewer） |
| WS token 经 URL query 传输（可入日志） | 中 | `market.py:112,161` `token: str = Query(default="")` | 改用首帧握手鉴权或 Sec-WebSocket-Protocol |
| `APP_ENV=development` + 默认 token `dev-admin-token` | 中 | `.env:2,6` + `config.py:94`（仅非 development 拦截） | 开发环境也强制非默认 token |
| `entry_context: dict[str, Any]` 无 schema 校验 | 中 | `workflow.py:225,322`；gatekeeper 从中读 `close_only_mode`/`quantity` | 用嵌套 Pydantic model 替换裸 dict |
| 无 CORS 配置（默认拒绝，但未显式声明） | 低 | `main.py` 全文无 CORSMiddleware | 显式配置白名单 origins |

### 性能

#### 整体评价
数据层用 SQLAlchemy Core 批量 upsert + Timescale 索引设计合理，但 **DB 连接池完全未调优**、WS 回写路径每 bar 新建 session、Celery 缺并发/超时配置、同步 LLM/urllib 阻塞 IO 隐患突出。

| 瓶颈 | 影响 | 证据路径 | 优化建议 |
|---|---|---|---|
| `create_engine` 未配置连接池参数 | 高并发下连接耗尽/失效（PG 默认 pool_size=5） | `services/database.py:30` | 加 `pool_size=20, pool_recycle=1800, pool_pre_ping=True` |
| WS 每 closed kline 新建 DB session 写入 | 高频行情下 session 频繁创建/销毁，GC 压力大 | `market.py:335-336` `with get_session_factory()() as db:` | 改为批量缓冲 + 单 session 批量 commit |
| Celery 无 concurrency/prefetch/time_limit | 回测任务可能阻塞 worker、无超时保护 | `apps/api/celery_app.py`（全无配置） | 配 `worker_concurrency=4, task_time_limit=600, prefetch_count=1` |
| 同步 `httpx.Client` 调 LLM | 若在 async 路由内调用会阻塞事件循环 | `llm_runtime.py:51-65` | 改 `httpx.AsyncClient` 或 `run_in_executor` |
| `urllib.urlopen` 阻塞 IO（binance REST fallback） | 非 CCXT 路径下同步阻塞 | `binance.py:362` `urlopen(..., timeout=5)` | 统一走 httpx async 或 CCXT |
| 前端 8s 轮询 + WS 双通道 | 同时轮询与 WS 推送，重复请求浪费带宽 | `useConsoleData.js:156`（setInterval 8000）+ `:174`（WS） | WS 连上后暂停轮询，断开才回退 |
| `_open_position_count` 遍历全部持仓 | 每次手动下单全量拉持仓列表 | `manual.py:262-266` | DB 层 `COUNT(*) WHERE quantity != 0` |
| `list_ohlcv_bars` limit 子查询套子查询 | 大表上 LIMIT 取最新 N 行走全表扫 | `repository.py:220-221` | 用 `ORDER BY time DESC LIMIT N` 单次查询后反转 |

---

## 整改路线图

### P0 — 阻断闭环，必须立即处理
1. **轮换并清除本地明文 Binance 密钥**（`.env:33-34`），改用 vault/KMS
2. **补齐 Strategy 状态机回写**：`backtest_status`/`paper_status`/`live_status` 在对应 service 完成时回写 Strategy 表
3. **实现参数优化引擎**（hyperopt 或 optuna），打通"回测→优化→OOS"链条
4. **实现 Coding Agent executor**：用 LLM 结构化生成 StrategyDraft，替换 `_draft_from_open_source_idea` 的关键词硬编码
5. **实现 Risk Agent 的 Martingale 检测**：gatekeeper 增加加仓倍率/亏损后翻倍检测，落实 AGENTS.md 硬规则
6. **增加 kill switch**：Redis 全局开关 + gatekeeper 下行检查
7. **失败知识复用闭环**：Research/Strategy Agent 生成 idea 时读取 `list_failures`

### P1 — 重要，影响质量与可维护性
1. **修复 services→apps/api 反向依赖**：`Settings` 下沉到 `shared/config.py`
2. **修复 SEC EDGAR URL 错配**（`config.py:63` vs `news.py:145`）
3. **接入 Backtrader/VectorBT** 或从 `pyproject.toml` 与 `AGENTS.md` 移除虚标声明
4. **DB 连接池调优** + Celery 并发/超时配置
5. **API 列表分页/排序规范**（统一 `CollectionResponse`）
6. **拆分 `paper_runtime.run_cycle`**（350 行→多个私有方法）
7. **准入阈值配置化**（消除 carry.py/admission.py 重复硬编码）
8. **接入 Email 通知 adapter**
9. **B/C/D 级数据源真实接入**（Reuters/Bloomberg/arxiv/Reddit）

### P2 — 可缓，提升体验与扩展性
1. 前端迁移 TypeScript（.jsx→.tsx）
2. 拆分 `useConsoleData` hook
3. Deflated Sharpe 通用校正接入
4. 多交易所扩展（OKX/Bybit）
5. 前端 WS 连上后暂停轮询
6. `list_ohlcv_bars` 查询优化
7. CORS 显式白名单配置

---

## 审查方法说明

本报告由 4 路并行代码级审查 + 对账表交叉验证产出：
- **路径 1**：功能完整性 + 业务流程数据闭环（对照六层架构与 status matrix，验证代码 vs 声明）
- **路径 2**：功能质量 + 前后端架构（代码质量、实现深度、分层、接口规范、前后端契约）
- **路径 3**：第三方集成（接入方式、鉴权、异常处理、声明 vs 实现背离）
- **路径 4**：安全性与性能（密钥、鉴权、注入、风控完整性、DB/并发/缓存瓶颈）

所有问题均附具体文件路径与行号证据，可在仓库中直接定位复核。
