# Project Memory

## Identity

- 项目名：AI Quant Research Platform
- 目标：实现研究报告定义的完整量化研究平台，而不是想法体检器或单纯回测脚本
- 主市场：第一阶段以 `BTC/USDT 永续` 为主
- 主语言：Python
- 管理后台：React + Tailwind

## Stable Constraints

- 报告是主架构真源
- 六层架构不可删层
- 风控系统必须是执行层核心，而不是后补
- Review Layer 必须每日回写策略库
- WorldQuant 只作为 E级研究数据与辅助来源

## Current Phase

- Phase 0：平台骨架、统一模型与设计冻结

## Active Design Sources

- 第一层真源：研究报告
- 第二层真源：`AGENTS.md`
- 第二层实施母文档：`docs/architecture/platform-master-design.md`
- 开发入口索引：`docs/architecture/design-source-index.md`
- 工程落地细化：`AI_Quant_v2_集成方案开发任务书.pdf` + 对账文档 `docs/architecture/v2-integration-reconciliation.md`（docx 真源优先，冲突以 docx 为准）
- 已完成子设计：`docs/architecture/domain-and-interfaces-design.md`
- 已完成子设计：`docs/architecture/data-and-ingestion-design.md`
- 已完成子设计：`docs/architecture/agent-and-orchestration-design.md`
- 已完成子设计：`docs/architecture/execution-risk-review-design.md`
- 已完成子设计：`docs/architecture/validation-methodology.md`（Deflated Sharpe / walk-forward / 压力测试场景库 / 成本建模）
- 设计附录：
  - `appendix-a-repository-structure.md`
  - `appendix-b-feature-phasing.md`
  - `appendix-c-principles-and-non-goals.md`

## Additional Design Assets

- 上层产品定位：`docs/product/product-spec.md`
- 上层功能总表：`docs/product/feature-catalog.md`
- 开发验收真源：`docs/product/prd.md`
- 模块字段级承接：`docs/product/module-feature-catalog.md`
- 路线图：`docs/roadmap/phase-roadmap.md`
- 配置规范：`docs/ops/environment-and-config.md`
- 准备清单：`docs/ops/delivery-checklist.md`

## Planned Repository Structure

- `apps/api`（已接 strategies CRUD seam + config + celery_app）
- `frontend/admin`
- `shared/models`（统一 Pydantic 数据契约，跨层唯一真源）
- `services/data`
- `services/strategy_library`（已有 Strategy 18 字段 ORM）
- `services/agents`
- `services/validation`
- `services/execution`
- `services/review`
- `research_source/worldquant_adapter`（方法论移植接缝）
- `infra`（timescale/freqtrade/jesse/grafana）
- `migrations`（Alembic，关系表）
- `tests`（contracts + api）

## Phase-0 Scaffolding Status (TASK-005, 2026-06-29)

- 已落地：shared 契约、infra、docker-compose v2（8 服务）、TimescaleDB、Alembic+Strategy ORM、strategies CRUD seam、Makefile、pre-commit、CI、tests。
- 待运行环境验证：`docker compose up`、`alembic upgrade head`、`uv lock`（本地无 docker/make/uv）。
- 下一步：P0-03 `ohlcv_downloader.py`、P0-12 strategies repository（替换内存 seam）。

## Phase-0 收尾架构补强 (TASK-006, 2026-07-02)

用户基于外部量化建议反馈四点，逐一确认后落地为设计文档 + 契约字段（仍不写业务逻辑代码）：

1. 信号融合 + meta-labeling 归属 Strategy Library 子模块（`SignalEnsemble`/`MetaLabel`），不新增独立第7层。见 [domain-and-interfaces-design.md](C:\Users\Windows11\Desktop\量化项目\docs\architecture\domain-and-interfaces-design.md) §3.5a/§3.5b，契约在 `shared/models/signal.py`。
2. P1 优先级明确：资金费率/基差套利（波动率无关底仓策略）先于技术策略框架化落地，用于跑通整条 Validation Layer 流水线。见 [appendix-b-feature-phasing.md](C:\Users\Windows11\Desktop\量化项目\docs\architecture\appendix-b-feature-phasing.md)。
3. 新闻过滤/LLM 一票否决的具体触发规则已写入 [execution-risk-review-design.md §2.2a/§03a](C:\Users\Windows11\Desktop\量化项目\docs\architecture\execution-risk-review-design.md) 与 [agent-and-orchestration-design.md](C:\Users\Windows11\Desktop\量化项目\docs\architecture\agent-and-orchestration-design.md)（新增 Decision Veto Agent）。
4. 验证方法论（walk-forward/Deflated Sharpe/压力测试场景库）与成本建模（手续费/滑点/资金费率净收支）新增子设计文档 [validation-methodology.md](C:\Users\Windows11\Desktop\量化项目\docs\architecture\validation-methodology.md)，`BacktestRun`/`BacktestReport` 契约已补字段。
5. 对应 ADR：见 decisions-log.md ADR-011~014。

## Primary Deliverables For This Phase

1. 全局项目配置
2. 项目记忆体系
3. 统一领域模型
4. 后端主干
5. 初始前端骨架
6. 平台总设计包母文档
7. 领域与接口设计包
8. 数据与接入设计包
9. Agent 与任务编排设计包
10. 执行 / 风控 / 复盘设计包
11. 产品与路线规格包

## Phase-0 开发前完整方案包 (TASK-007, 2026-07-02)

用户要求做第二轮全局查缺补漏，并交付一整套开发前方案文档（技术架构/PRD/模块功能清单/
策略库机制/LLM接入/24小时运行/外部数据源/风控），全部写入 `docs/` 作为正式仓库文档，
分批交付。8 份文档已全部完成：

1. [technical-architecture-plan.md](C:\Users\Windows11\Desktop\量化项目\docs\architecture\technical-architecture-plan.md) — 六层到物理部署的映射、四环境 compose 拓扑规划、API/队列/存储/配置技术规则、§12 已知技术缺口追踪表（当前仓库骨架与目标态之间的具体缺口清单）。
2. [prd.md](C:\Users\Windows11\Desktop\量化项目\docs\product\prd.md) — 产品愿景与反成功标准、两角色、六个人工决策点作为产品硬约束、七模块用户故事+验收标准。
3. [module-feature-catalog.md](C:\Users\Windows11\Desktop\量化项目\docs\product\module-feature-catalog.md) — 逐模块功能点表，精确交叉引用 domain-and-interfaces-design.md 的对象/接口簇字段名。
4. [strategy-library-collection-and-scoring.md](C:\Users\Windows11\Desktop\量化项目\docs\architecture\strategy-library-collection-and-scoring.md) — 六类来源分类、单策略/组合两级评分机制、六类淘汰触发信号，只设计机制不写具体参数。
5. [llm-integration-plan.md](C:\Users\Windows11\Desktop\量化项目\docs\architecture\llm-integration-plan.md) — 逐 Agent LLM 使用边界与禁止清单、四段式 Prompt 结构、成本控制、LangChain/LlamaIndex 仅服务 Research Agent 的 RAG 边界，只做方案不写代码。
6. [24x7-operations-plan.md](C:\Users\Windows11\Desktop\量化项目\docs\architecture\24x7-operations-plan.md) — 进程/连接/数据三层监督模型、心跳与指数退避重连、五级降级状态机、通知告警分级规则。
7. [external-data-source-integration-plan.md](C:\Users\Windows11\Desktop\量化项目\docs\architecture\external-data-source-integration-plan.md) — 五级数据源的具体 API/SDK/拉取频率/存储路径，补全 data-and-ingestion-design.md 有意留白的具体参数。
8. [risk-control-and-safeguards-plan.md](C:\Users\Windows11\Desktop\量化项目\docs\architecture\risk-control-and-safeguards-plan.md) — 裁决此前四处留白：LLM 否决超时选"超时即否决"、Key 权限自检强制拒绝启动规则、`RiskProfile` 具体阈值默认值、熔断触发规则具体化。

对应 ADR：见 decisions-log.md ADR-015~018。

## Design Convergence Blueprint (TASK-008, 2026-07-02)

- 新增 `docs/architecture/design-source-index.md` 作为开发入口索引，固定真源层级、Phase 语义、文档职责边界。
- 明确 `Phase 0` = 平台骨架 + 统一模型 + 设计冻结；`appendix-b-feature-phasing.md` 中的 `P0/P1/P2` 仅是实现 tranche 标签。
- README / `report-alignment.md` / 产品上层文档已同步说明：`product-spec.md` + `feature-catalog.md` 负责上层定位，`prd.md` + `module-feature-catalog.md` 负责开发验收。
- `策略库/笔记.docx` 被正式定义为研究素材池，只能通过 `StrategyIdea -> StrategyDraft -> StrategyContract` 流程进入主链路。
- 代码层补齐：`RiskProfile` 契约、工作流生命周期对象契约、六大接口簇 API skeleton、全量 `Settings` 环境变量入口、`services/data` 包骨架。
## First Persisted Vertical Slice (TASK-009, 2026-07-02)

- The repository now has persisted SQLAlchemy-backed repositories for `StrategyIdea`, `StrategyDraft`, `Strategy`, `StrategyVersion`, `BacktestRun`, `IngestionJob`, and `PaperRun`.
- `apps/api/routers/{strategies,backtests,ingestion,runs}.py` now use real repositories via `get_db_session` instead of in-memory dictionaries.
- Binance A-level helpers now exist in `services/data/binance.py` for `Top20` universe selection, OHLCV normalization, and funding-rate normalization.
- `services/data/service.py` defines the current fallback Binance `Top20` universe list for ingestion jobs until live exchange ranking is wired in.
- `services/validation/carry.py` contains the first carry backtest service with settlement-window handling, spot/perp/funding cost reconciliation, and `conditional` gate behavior when deflated Sharpe is absent.
- `services/execution/paper.py` makes `BTC/USDT` and `ETH/USDT` the default first simulated symbols for paper preparation.
- Celery now has first real task entrypoints for ingestion, backtests, and paper runs.
- Alembic `0001` now manages the relational tables for this slice instead of only the original `strategies` table.
- Current verified path: `StrategyIdea -> StrategyDraft -> Strategy -> StrategyVersion -> BacktestRun -> GateDecision -> PaperRun`.

## Persisted Carry Application Flow (TASK-010, 2026-07-02)

- `services/data/repository.py` now provides the first real timeseries repository for `ohlcv_bars` and `market_extras`, including store/list helpers plus gap and freshness checks for Phase-1 carry validation.
- `shared.models.CarryBacktestRequest` is now the application-layer submission contract for the persisted carry lane.
- `services/validation/application.py` now defines `CarryBacktestApplicationService`, which loads persisted spot/perp/funding data, applies settlement-window data-quality checks, runs the carry backtest, and persists the resulting `BacktestRun`.
- `apps/api/routers/backtests.py` now exposes `/backtests/carry` as the first API path that executes a backtest from persisted market data instead of accepting only a pre-built `BacktestRun` payload.
- `services.validation.tasks.enqueue_carry_backtest` is wired as the queue-side entrypoint for the same application flow, but local import smoke for Celery remains pending until the environment installs `celery`.
