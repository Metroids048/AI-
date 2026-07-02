# Decisions Log

## ADR-001: 研究报告作为主架构真源

- Date: 2026-06-28
- Status: accepted
- Context: 用户明确要求严格按研究报告实现整体架构与闭环。
- Decision: 将 `AI_Quant_Research_Platform_完整报告.docx` 作为主真源，并在 `AGENTS.md` 中固化为仓库级约束。
- Consequences: 后续实现不能擅自简化为单策略工具或以 WorldQuant 为主架构中心。

## ADR-002: 先做治理层，再做实现层

- Date: 2026-06-28
- Status: accepted
- Context: 当前工作区基本空白，直接写业务代码容易偏离报告。
- Decision: 先建立 `AGENTS.md`、项目记忆、项目元数据与基础配置，再继续仓库骨架和代码实现。
- Consequences: 后续每轮开发都以同一套记忆与约束为准，降低架构漂移。

## ADR-003: 第一阶段主市场为 BTC/USDT 永续，但模型支持多市场

- Date: 2026-06-28
- Status: accepted
- Context: 报告要求先从加密单品种深耕，但未来扩展到多资产。
- Decision: 领域模型从第一天起设计为多市场兼容，当前主流程围绕 `BTC/USDT perpetual`。
- Consequences: 不允许把市场、交易所、标的硬编码成不可扩展结构。

## ADR-004: 平台总设计包作为仓库内第二层实施母文档

- Date: 2026-06-28
- Status: accepted
- Context: 当前仓库已有治理层与骨架层，但缺少能直接指导后续子设计与实现的母文档。
- Decision: 新增 `docs/architecture/platform-master-design.md` 及 3 个附录，作为研究报告之下、子设计文档之上的总设计包。
- Consequences: 后续领域模型、接口、数据接入、Agent 编排、执行/风控/复盘设计都必须引用此母文档，不得各自重新定义平台边界。

## ADR-005: 领域模型围绕研究闭环而非单纯策略代码或订单系统建模

- Date: 2026-06-28
- Status: accepted
- Context: 下一版项目完善需要明确统一领域模型、接口簇和任务编排骨架。
- Decision: 领域与接口设计包以 `StrategyIdea -> StrategyDraft -> Strategy -> BacktestRun -> PaperRun -> LiveRun -> ReviewReport -> FailureRecord` 为主链路，确保研究入口、验证运行、执行事实、风险事件和复盘知识都是一等对象。
- Consequences: 后续 FastAPI、任务流、数据库和 Agent 编排都应围绕这条主链路组织，不得退化为“只管策略代码”和“只管订单执行”的局部系统。

## ADR-006: 前期准备阶段先补齐完整设计包，再进入业务实现

- Date: 2026-06-28
- Status: accepted
- Context: 用户要求把剩余文件和内容尽量全部开始进行，完成项目的前期准备工作，且要求足够详细具体。
- Decision: 在实现业务代码前，先补齐数据与接入、Agent 与任务编排、执行/风控/复盘、产品规格、功能清单、路线图、环境配置与交付清单等设计资产。
- Consequences: 仓库在进入开发实现前将具备较完整的项目启动包，减少后续反复返工和架构漂移。

## ADR-007: 用 TimescaleDB 替换 PostgreSQL，并拆分存储归属

- Date: 2026-06-29
- Status: accepted
- Context: v2.0 PDF 要求时序数据（K线/funding/OI）用 TimescaleDB 超表，查询性能提升 10-100 倍。
- Decision: docker-compose 的 `postgres` 服务改为钉版 `timescale/timescaledb:2.17.2-pg16`。存储归属拆分：`infra/timescale/init.sql` 拥有时序/事件表（ohlcv_bars/market_extras/risk_events/macro_events），Alembic 拥有关系表（strategies 等），任何表不得两边创建。Order Book 只进 Redis 不落库。
- Consequences: 数据库连接串改用 `postgresql+psycopg://`；init.sql 必须幂等。

## ADR-008: 数据契约优先，统一在 shared/models 定义

- Date: 2026-06-29
- Status: accepted
- Context: PDF 原则二要求所有框架输出在进入 services 前转换为内部统一 Pydantic 模型；domain 文档与 PDF 存在命名漂移。
- Decision: 新建 `shared/models/` 作为唯一跨层契约源，任何 service 不得各自定义。命名裁决：`BacktestReport`（引擎指标载荷）= 领域对象 `BacktestRun.metrics_summary`，两名并存；`RiskEvent` 采用 domain 超集（event_type/severity/affected_scope/resolution_status），PDF `level`→`severity`，DB 表存子集投影。
- Consequences: SQLAlchemy ORM 视为存储细节而非契约，须与 `StrategyContract` 双向映射。

## ADR-009: WorldQuant 本地 alpha 库移植方法论而非搬运表达式

- Date: 2026-06-29
- Status: accepted
- Context: 本地 `Desktop/alpha` 是成熟的 WorldQuant **美股**挖掘流水线（~67万表达式），基于基本面字段，不能直接套用到 BTC/USDT 永续。
- Decision: `research_source/worldquant_adapter/` 只移植算子词表与因子构造方法（纯 pandas/numpy），重新生成加密因子；不导入美股原始表达式。`.env` 用 `WORLDQUANT_ALPHA_LOCAL_PATH` 引用本地路径，不上传 Brain session。
- Consequences: 符合 AGENTS.md 不可谈判项 #5（WorldQuant 是来源非主干）；该模块不被 apps/api import。

## ADR-010: 保留 setuptools 构建后端，依赖锁定用 uv；指标库用 pandas-ta

- Date: 2026-06-29
- Status: accepted
- Context: 需要可复现依赖，但不想迁移构建后端；TA-Lib 需系统 C 库，Windows/slim 容器安装困难。
- Decision: 保留 PEP621 + setuptools，仅引入 `uv lock`（uv 直读 pyproject）生成 `uv.lock`；指标库用纯 Python 的 pandas-ta，TA-Lib 仅注释可选。WorldQuant 算子移植为纯 pandas/numpy，不在 Phase0 关键路径依赖 TA-Lib。
- Consequences: `make lock/sync` 需安装 uv；首次锁定需在装有 uv 的环境执行。

## ADR-011: 资金费率/基差套利定为 Phase 1 第一个落地策略

- Date: 2026-07-02
- Status: accepted
- Context: 用户基于外部量化建议反馈，指出方向性技术策略确定性低，应先用波动率无关/方向无关的资金费率套利把 Validation Layer 全流程（回测→样本外→模拟盘）跑稳，再叠加更冒险的策略。
- Decision: `appendix-b-feature-phasing.md` P1 明确排序：资金费率/基差套利策略优先落地并验收，验收通过后才进入技术策略框架化（缠论/道氏等）与信号融合接入。
- Consequences: P1 任务拆解与验收标准需围绕这一策略先行设计；技术策略框架化的启动时间点依赖前一项验收结果，不并行抢跑。

## ADR-012: 信号融合与二级仓位判定归属 Strategy Layer 子模块，不新增第7层

- Date: 2026-07-02
- Status: accepted
- Context: 用户反馈建议引入"信号融合"与"meta-labeling 二级过滤"（一级信号只回答方向，二级轻量模型用三重界限法判定是否下注/下多大仓位）。需要决定是否新增独立架构层。
- Decision: 新增 `SignalEnsemble`（信号融合结果）与 `MetaLabel`（二级仓位判定）两个领域对象，归属 Strategy Layer 下的子模块（`services/strategy_library/ensemble/`），六层架构本身不变。主链路调整为 `...StrategyCodeArtifact -> SignalEnsemble -> MetaLabel -> BacktestRun...`。
- Consequences: 回测评估对象从"单策略信号"变为"融合后的候选交易"；`Strategy` ORM 表结构不受影响，新对象走独立契约（`shared/models/signal.py`），不污染 `strategies` 表。

## ADR-013: LLM 只承担一票否决与复盘生成，否决时机限定在信号融合之后、执行之前

- Date: 2026-07-02
- Status: accepted
- Context: 用户反馈强调 LLM 在杠杆环境下直接输出买卖指令的幻觉代价过大，应改为"否决与复盘"角色，且新闻/消息面数据应作为过滤器而非开单触发器。这与既有 AGENTS.md"AI 是研究员不是交易员"原则一致，但缺少具体机制。
- Decision: 新增 `Decision Veto Agent`（`agent-and-orchestration-design.md` 2.11），输入 `SignalEnsemble`/`MetaLabel`/近期 `RiskEvent`，只输出 `veto: bool + veto_reason`，不得输出方向/仓位/价格；否决为 true 的信号不得进入 `ExecutionSignal`。新闻分级触发规则写入 `execution-risk-review-design.md` §2.2a：高严重度暂停开仓 N 分钟（可配置）+ 止损收紧，中/低严重度仅记录不触发。
- Consequences: Execution Layer 前置检查新增两条：信号必须已完成二级仓位判定（`bet_taken`）且未被否决（`veto=false`），才能生成 `ExecutionSignal`。

## ADR-014: 验证方法论与成本建模纳入 BacktestRun/BacktestReport 契约

- Date: 2026-07-02
- Status: accepted
- Context: 用户反馈指出回测若不做 walk-forward、不做多重检验偏差校正（Deflated Sharpe）、不覆盖极端压力场景、不建模真实成本（手续费/滑点/资金费率净收支），回测结果是自我欺骗。现有 `BacktestRun` 只有字段占位，没有方法论文档。
- Decision: 新增子设计文档 `docs/architecture/validation-methodology.md`，定义 walk-forward 滚动窗口规则、Deflated Sharpe 校正要求（配套 `trials_count`/`deflated_sharpe` 字段）、压力测试场景库（LUNA崩盘/312/交易所宕机/极端插针）、三项成本建模口径（maker/taker费率、订单簿深度滑点、资金费率净收支，统一用 `total_cost_bps`）。`BacktestRun`（domain doc）与 `BacktestReport`（`shared/models/backtest.py`）契约同步补充对应字段，均为可选字段，不破坏现有测试。
- Consequences: 模拟盘准入判断口径调整为优先看 `deflated_sharpe` 而非原始 Sharpe，且收益指标必须是扣除成本后的净值；具体计算公式与滑点估算算法留给 Phase 1 实现，Phase 0 只固定契约与方法论边界。

## ADR-015: LLM 只承担否决/分类/复盘角色，永不直接输出交易指令——写入独立方案文档并覆盖全部 Agent

- Date: 2026-07-02
- Status: accepted
- Context: 用户要求为"接入 LLM API 辅助完成功能、整体分析"单独产出设计方案（只做设计不写代码）。需要把 ADR-013 已确定的 Decision Veto Agent 边界，扩展到覆盖 News/Twitter/Telegram/Research/Review 等全部会调用 LLM 的 Agent，避免各 Agent 各自发明使用边界。
- Decision: 新增 `docs/architecture/llm-integration-plan.md`，逐 Agent 定义允许用途与禁止清单（核心禁止项：任何 Agent 都不得输出方向/价格/仓位；News/Twitter/Telegram 的分类结果必须经过既有 `RiskEvent` 分级流程才能影响执行，不得直接触发下单）；四段式 Prompt 模板（SYSTEM/CONTEXT/TASK/OUTPUT SCHEMA）；LLM 输出必须过 Pydantic 校验才能落地，不接受宽松部分解析；LangChain/LlamaIndex 的 RAG 范围限定为 Research Agent 的 E 级检索，明确排除任何执行路径 Agent。
- Consequences: `CLAUDE_MODEL` 建议改为按 Agent 类型可配置而非单一全局环境变量（P1 任务）；LLM 不可用时禁止自动放宽风控阈值或静默降级为本地规则引擎而不打标签，与"风控优先于收益"直接挂钩。

## ADR-016: 24 小时运行的可靠性分三层监督，故障响应为"自动降级、人工恢复"，仅两类例外可自动恢复

- Date: 2026-07-02
- Status: accepted
- Context: 用户要求为 7x24 自动实时交易单独产出运行方案。现有设计文档未定义进程崩溃/连接静默断连/数据缺口等运行时故障的检测与响应机制，通知/告警层此前在多处文档中被标记为缺口但从未定义触发规则。
- Decision: 新增 `docs/architecture/24x7-operations-plan.md`，分进程层/连接层/数据层三层监督；连接层采用应用层心跳（不依赖 socket 状态）+ 指数退避带抖动重连；故障响应对齐 `execution-risk-review-design.md` 已有的风控结果枚举（`pause_strategy`/`pause_account`/`hard_stop`），默认"自动降级、人工恢复"，仅"高严重度新闻事件计时结束"与"数据中断已恢复且新鲜度确认通过"两种客观可验证场景允许自动恢复；定义"只允许平仓"模式的具体行为边界（不得撤销已有止损）；定义通知告警三级触发规则。
- Consequences: 该文档成为风控措施与保障方案（ADR-018）裁决具体熔断时长/阈值时的运行时行为基础，两文档不重复定义触发动作分类，只有取值和最终裁决在风控文档。

## ADR-017: 外部数据源接入的具体 API/SDK/频率选型独立成文，不并入抽象的数据接入设计

- Date: 2026-07-02
- Status: accepted
- Context: `data-and-ingestion-design.md` 有意保持抽象，不点名具体供应商与轮询频率。用户要求的开发前方案包需要"外部数据源、信息源的接入"具体到可执行程度，且原文档的抽象定位不应被破坏（避免未来供应商变更导致要重写架构文档）。
- Decision: 新增 `docs/architecture/external-data-source-integration-plan.md`，逐级（A/B/C/D/E）给出具体 API/SDK 选型（CCXT/ForexFactory RSS/Trading Economics/金十+CoinDesk+TheBlock+SEC EDGAR RSS/Twitter API v2+Telegram Bot API/GitHub+arXiv API）与拉取频率原则；`IngestionJob.source_family`/`job_type`/`schedule_mode`/`job_status` 建议枚举取值；原始数据 P0/P1 阶段落文件存储、P2 迁移对象存储的路径规划；明确 Reddit（PRAW）/YouTube Data API 是当前真实缺口，定为 P2；澄清 Telegram 三重角色（D级采集/E级采集可共用 Token，运维出站告警必须用不同 Token）。
- Consequences: 供应商/SDK/频率属于本文件而非 `data-and-ingestion-design.md` 的维护范围，未来更换数据供应商只需修订本文件；`IngestionJob` 尚未代码化，枚举取值是 P1 实现时的建议值而非已落地约束。

## ADR-018: 风控措施与保障方案裁决四项此前留白的风险容忍度选择

- Date: 2026-07-02
- Status: accepted
- Context: `llm-integration-plan.md` §3.2、`execution-risk-review-design.md` §2.2a、`technical-architecture-plan.md` §8.3、`domain-and-interfaces-design.md` §3.13 均把具体取值/选择显式留白，交给风控方案文档裁决。用户要求的开发前方案包必须包含"风控措施和保障"维度，且这些留白必须在进入开发前有明确答案，不能带着未裁决的分支进入实现阶段。
- Decision: 新增 `docs/architecture/risk-control-and-safeguards-plan.md`：(1) LLM 否决超时选"超时即否决"（选项 A），理由是选项 B 在市场剧烈波动时可能恰好放行风险最高的信号，与风控优先级原则冲突；(2) `paper`/`live` 环境交易所 Key 启动期强制自检提现权限，若检测到提现权限则拒绝启动而非仅告警，`dev`/`test` 环境按"是否涉及真实资金"为界例外；(3) 给出 `RiskProfile` 全字段针对 BTC/USDT 永续第一阶段的具体默认阈值表（单笔1%/单品种20%/总仓位60%/最大杠杆3倍/当日3%/单周8%/回撤10%触发收紧/回撤20%硬停止），明确扩展市场不可直接复用；(4) 具体化熔断触发条件表，且明确"自动降级、人工恢复"为默认原则，仅两类客观可验证场景例外允许自动恢复（与 ADR-016 一致）。
- Consequences: 后续如需修改这四项裁决的具体数值/选择，应作为对本文件的修订，不得绕开本文件直接改动被引用的源文档；`RiskProfile` 阈值表是 P1 代码化时的默认配置，运行时仍需可配置，不是硬编码常量。

## ADR-019: 用单独索引文档固定真源层级、产品文档分层与 Phase 语义

- Date: 2026-07-02
- Status: accepted
- Context: 仓库在第二轮设计补强后已同时存在主报告、母文档、专项方案、旧版产品文档与新版 PRD/模块清单。若继续只靠 README 或记忆文件口头说明，会再次出现“哪份文档说了算”“Phase 0 和 P0/P1 是否同义”的理解漂移。
- Decision: 新增 `docs/architecture/design-source-index.md` 作为开发入口索引，固定真源层级为“研究报告 > AGENTS.md > 母文档 > 第二轮专项方案 > 产品细化文档 > README/记忆索引”；明确 `product-spec.md` + `feature-catalog.md` 是上层定位与总表，`prd.md` + `module-feature-catalog.md` 是开发验收真源；明确当前仓库整体仍处于 `Phase 0`（平台骨架 + 统一模型 + 设计冻结），而 `appendix-b-feature-phasing.md` 中的 `P0/P1/P2` 只是实现 tranche 标签。
- Consequences: 后续任何新增文档都必须先放入该索引的层级体系，再决定它是否有裁决权；README、`report-alignment.md`、记忆文件只能同步入口和现状，不得绕过该索引重新发明真源链。
## ADR-020: The first executable slice expands into Binance Top20 ingestion plus BTC/ETH-first paper preparation
- Date: 2026-07-02
- Status: accepted
- Context: After the design convergence pass, the user confirmed that the first implementation tranche should no longer stop strictly before paper trading. The tranche must now cover Binance top-universe ingestion metadata and a simulated paper-run preparation path, while still keeping live execution, WebSocket streaming, and LLM veto outside this code drop.
- Decision: Keep the first real exchange integration Binance-only, but widen the scope to include persisted `IngestionJob`, `BacktestRun`, and `PaperRun` objects; a default Binance `Top20` quote-volume universe fallback for ingestion jobs; and a paper-run orchestration default that always prioritizes `BTC/USDT` and `ETH/USDT` as the first simulated symbols. `GateDecision` is extended to explicit `accepted` / `conditional` / `rejected_with_reason` statuses so the paper-preparation boundary can be expressed without overloading a boolean.
- Consequences: The API and repository implementation must cover `StrategyIdea -> StrategyDraft -> Strategy -> StrategyVersion -> BacktestRun -> GateDecision -> PaperRun`; Celery gets first real ingestion/backtest/paper task entrypoints; Alembic must manage all relational tables for this slice; and later Binance live collectors can replace the fallback universe source without changing the public contract shape.

## ADR-021: Persisted carry backtests execute through an application service over timeseries repositories
- Date: 2026-07-02
- Status: accepted
- Context: After the first persisted slice landed, the remaining gap was that backtests could only be created from already-built payloads. The project still lacked a real application flow that reads persisted `ohlcv_bars` / `market_extras`, evaluates data quality, runs carry validation, and writes the resulting `BacktestRun`.
- Decision: Add a dedicated `CarryBacktestApplicationService` and `CarryBacktestRequest` contract. The service reads Binance spot/perp/funding history from a `DataRepository`, uses settlement-window cadence for the carry lane's gap check, runs the existing carry backtest engine, enriches `validation_methodology` with persisted data-quality evidence, and persists the run through `ValidationRepository`. Expose the flow through `/backtests/carry` and a matching `enqueue_carry_backtest` Celery task rather than overloading generic CRUD endpoints.
- Consequences: The verified backtest chain now includes persisted market data as an explicit dependency. Future Freqtrade/VectorBT adapters can reuse the same application boundary, and later live exchange collectors can feed the same repository contract without changing the API shape.
