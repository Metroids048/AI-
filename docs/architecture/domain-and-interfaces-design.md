# 领域与接口设计包

## 文档定位

本文件是 `AI Quant Research Platform` 的“领域与接口设计包”。

引用关系：

- 上游真源：
  - [AI_Quant_Research_Platform_完整报告.docx](C:\Users\Windows11\Desktop\量化项目\AI_Quant_Research_Platform_完整报告.docx)
  - [AGENTS.md](C:\Users\Windows11\Desktop\量化项目\AGENTS.md)
  - [platform-master-design.md](C:\Users\Windows11\Desktop\量化项目\docs\architecture\platform-master-design.md)
- 下游实现：
  - FastAPI 主干
  - 统一领域模型
  - API schema
  - 任务编排模型
  - 数据与 Agent 子设计

本文件回答：

- 核心领域对象有哪些
- 它们之间如何关联
- 它们如何穿过六层架构流动
- 后续 API 应按什么接口簇组织
- 后续任务编排应以什么对象为核心

---

## 01 设计目标

### 1.1 核心目标

统一领域模型必须服务于研究闭环：

`交易假设 -> 规则化 -> 回测 -> 模拟盘 -> 小资金实盘 -> 复盘 -> 迭代`

不能只围绕“策略代码”建模，也不能只围绕“订单执行”建模。

### 1.2 设计原则

- 领域对象先于数据库表
- 结构化对象先于自然语言
- 生命周期状态明确
- 每个对象必须映射到六层架构中的至少一层职责
- AI Agent 之间通过结构化对象通信，不直接改写零散文件

### 1.3 本轮不做

- 不细化到数据库字段级 DDL
- 不定义最终 FastAPI request/response schema
- 不定义 Celery 任务参数细节
- 不展开数据源适配细节

这些内容留给后续子设计包与实现阶段。

---

## 02 领域边界与核心子域

本平台按核心职责拆为 6 个核心子域：

1. `Research Intake`
   - 接收交易假设、研究来源、研究备注
2. `Strategy Library`
   - 管理策略定义、版本、状态、失败与迭代
   - 含信号融合与二级仓位判定子模块（ensemble / meta-labeling），职责是把多个策略/alpha 的候选信号融合为单一交易候选，并判定是否下注与下注大小；不升级为独立子域
3. `Validation`
   - 管理回测、优化、样本外验证、模拟盘/实盘准入
4. `Execution`
   - 管理执行信号、订单、持仓、执行日志与异常
5. `Risk Control`
   - 管理风险参数、风险事件、熔断与恢复
6. `Review & Knowledge`
   - 管理复盘报告、失败模式、知识沉淀与回写

辅助子域：

- `Reference Data`
- `Source Ingestion`
- `Agent Orchestration`

---

## 03 核心领域对象

### 3.1 StrategyIdea

定义：

- 人工提出或外部研究来源发现的“交易假设原始输入”

职责：

- 作为研究入口，而不是直接作为策略对象

典型属性：

- `idea_id`
- `title`
- `origin_type`
- `origin_reference`
- `market_scope`
- `thesis_text`
- `submitted_by`
- `review_status`
- `tags`
- `created_at`

生命周期：

- `captured`
- `screened`
- `rejected`
- `accepted_for_drafting`

### 3.2 StrategyDraft

定义：

- 由 `Strategy Agent` 从 `StrategyIdea` 规则化得到的结构化草案

职责：

- 承担“自然语言 -> 规则结构”这一步

典型属性：

- `draft_id`
- `idea_id`
- `market`
- `symbol_scope`
- `timeframes`
- `market_regime`
- `entry_rules`
- `exit_rules`
- `stoploss_rules`
- `takeprofit_rules`
- `position_rules`
- `assumptions`
- `draft_status`

生命周期：

- `generated`
- `under_review`
- `approved`
- `rejected`
- `promoted_to_strategy`

### 3.3 Strategy

定义：

- 策略库中的正式策略主对象

职责：

- 平台的核心长期资产

典型属性：

- `strategy_id`
- `strategy_key`
- `source`
- `core_thesis`
- `market`
- `symbol_scope`
- `timeframe`
- `market_regime`
- `risk_level`
- `entry_rules`
- `exit_rules`
- `stoploss_rules`
- `takeprofit_rules`
- `position_rules`
- `current_version_id`
- `backtest_status`
- `paper_status`
- `live_status`
- `strategy_status`

生命周期：

- `drafting`
- `ready_for_codegen`
- `in_validation`
- `paper_candidate`
- `live_candidate`
- `active`
- `paused`
- `retired`

### 3.4 StrategyVersion

定义：

- 策略的版本化快照对象

职责：

- 保证迭代与回溯能力

典型属性：

- `version_id`
- `strategy_id`
- `version_label`
- `change_summary`
- `rule_snapshot`
- `code_artifact_ref`
- `created_by`
- `created_at`

### 3.5 StrategyCodeArtifact

定义：

- `Coding Agent` 生成的代码工件

职责：

- 连接 Strategy Layer 与 Validation Layer

典型属性：

- `artifact_id`
- `strategy_id`
- `version_id`
- `engine_type`
- `entry_module_ref`
- `generation_notes`
- `compile_status`
- `artifact_status`

生命周期：

- `generated`
- `verified`
- `rejected`
- `superseded`

### 3.5a SignalEnsemble

定义：

- 多个策略/alpha 候选信号在同一时间窗口内的融合结果

职责：

- 承接“多信号 -> 单一交易候选”这一步，属于 Strategy Library 的信号融合子模块
- 参与融合的信号只做相关性过滤后的低相关子集，避免同质信号重复下注
- WorldQuant alpha 在此层只作为低权重投票输入之一，权重由历史验证数据迭代调整，不作为独立策略

典型属性：

- `ensemble_id`
- `strategy_refs`（参与融合的 `Strategy`/alpha 来源列表）
- `fusion_method`
- `correlation_matrix_ref`
- `raw_votes`（各来源方向 + 初始权重）
- `fused_direction`
- `fused_confidence`
- `created_at`

生命周期：

- `formed`
- `passed_to_meta_label`
- `discarded_low_confidence`

### 3.5b MetaLabel

定义：

- 对 `SignalEnsemble` 做二级仓位判定的结果对象（meta-labeling）

职责：

- 一级信号（技术策略/alpha/融合结果）只回答方向与机会；本对象回答“要不要真的下注、下多大仓位”
- 判定依据三重界限法（triple-barrier：止盈线/止损线/时间限，看价格先触达哪个）标注的历史样本训练得到
- 判定模型定位为轻量模型（如逻辑回归），不要求复杂度，模型细节留给 Phase 1/2 实现

典型属性：

- `meta_label_id`
- `ensemble_id`
- `triple_barrier_result`
- `bet_decision`
- `position_size_fraction`
- `model_ref`
- `training_window_ref`

生命周期：

- `pending`
- `labeled`
- `bet_taken`
- `bet_skipped`

### 3.6 BacktestRun

定义：

- 一次完整的历史回测执行记录

典型属性：

- `backtest_run_id`
- `strategy_id`
- `version_id`
- `dataset_scope`
- `execution_engine`
- `parameter_set`
- `market_regime_coverage`
- `sample_split_plan`
- `cost_model_ref`（手续费 maker/taker 费率、订单簿深度滑点估算方法、资金费率净收支核算口径的引用，方法论细节见 [validation-methodology.md](C:\Users\Windows11\Desktop\量化项目\docs\architecture\validation-methodology.md)）
- `validation_methodology`（walk-forward 滚动窗口参数 + Deflated Sharpe 所需的“测试过组合数”记录，用于多重检验偏差校正）
- `stress_test_scenarios`（引用预置压力测试场景库，如 LUNA 崩盘/312/交易所宕机/极端插针）
- `metrics_summary`
- `run_status`
- `eligibility_result`

生命周期：

- `queued`
- `running`
- `completed`
- `failed`
- `accepted`
- `rejected`

### 3.7 OptimizationRun

定义：

- 一次参数优化执行记录

典型属性：

- `optimization_run_id`
- `strategy_id`
- `version_id`
- `search_space_ref`
- `optimization_method`
- `best_candidate_summary`
- `run_status`

### 3.8 PaperRun

定义：

- 一次模拟盘运行对象

典型属性：

- `paper_run_id`
- `strategy_id`
- `version_id`
- `exchange`
- `symbol_scope`
- `run_window`
- `execution_profile`
- `paper_metrics_summary`
- `paper_status`

生命周期：

- `queued`
- `running`
- `paused`
- `completed`
- `failed`
- `promoted`
- `rejected`

### 3.9 LiveRun

定义：

- 一次小资金实盘运行对象

典型属性：

- `live_run_id`
- `strategy_id`
- `version_id`
- `exchange`
- `capital_tier`
- `live_status`
- `risk_profile_ref`
- `live_metrics_summary`

### 3.10 ExecutionSignal

定义：

- 验证通过后进入执行层的单次策略信号

典型属性：

- `signal_id`
- `strategy_id`
- `version_id`
- `signal_time`
- `symbol`
- `direction`
- `entry_context`
- `stoploss_plan`
- `takeprofit_plan`
- `signal_status`

### 3.11 OrderExecution

定义：

- 订单执行事实对象

典型属性：

- `execution_id`
- `signal_id`
- `exchange_order_ref`
- `order_type`
- `requested_price`
- `executed_price`
- `quantity`
- `slippage`
- `execution_status`

### 3.12 PositionSnapshot

定义：

- 某一时刻持仓状态快照

职责：

- 供 Risk Engine 与 Review Layer 使用

### 3.13 RiskProfile

定义：

- 策略或账户运行时适用的风险约束集合

典型属性：

- `risk_profile_id`
- `single_trade_risk_limit`
- `max_symbol_exposure`
- `max_total_exposure`
- `max_open_positions`
- `max_leverage`
- `daily_loss_limit`
- `weekly_loss_limit`
- `drawdown_limit`
- `hard_stop_drawdown_limit`

### 3.14 RiskEvent

定义：

- 风险事件主对象

职责：

- 统一表达宏观、系统、交易、数据和执行异常

典型属性：

- `risk_event_id`
- `event_type`
- `event_source`
- `severity`
- `affected_scope`
- `trigger_payload`
- `recommended_action`
- `resolution_status`
- `occurred_at`

事件类型建议：

- `macro_event`
- `news_risk`
- `social_event`
- `market_structure_risk`
- `exchange_incident`
- `api_failure`
- `data_gap`
- `risk_limit_breach`
- `execution_anomaly`

### 3.15 ReviewReport

定义：

- 每日或周期性复盘报告对象

典型属性：

- `review_report_id`
- `report_date`
- `scope_type`
- `strategy_refs`
- `worst_performer_refs`
- `failure_patterns`
- `deviation_analysis`
- `recommendations`
- `report_status`

### 3.16 FailureRecord

定义：

- 单条失败沉淀记录

职责：

- 成为策略知识沉淀的重要单元

典型属性：

- `failure_record_id`
- `strategy_id`
- `version_id`
- `origin_run_type`
- `origin_run_id`
- `failure_type`
- `failure_summary`
- `evidence_refs`
- `recommended_change`

### 3.17 IngestionJob

定义：

- 外部数据或研究源接入任务对象

典型属性：

- `ingestion_job_id`
- `source_family`
- `source_name`
- `job_type`
- `schedule_mode`
- `job_status`
- `input_window`
- `output_ref`

### 3.18 AgentTask

定义：

- 平台内所有 Agent 的统一任务载体

典型属性：

- `agent_task_id`
- `agent_type`
- `task_type`
- `input_ref`
- `output_ref`
- `priority`
- `task_status`
- `error_summary`
- `scheduled_at`

---

## 04 对象关系与聚合边界

### 4.1 主聚合

主聚合建议如下：

- `Strategy` 聚合
  - 持有 `StrategyVersion`
  - 关联 `StrategyCodeArtifact`
  - 关联 `FailureRecord`
  - 关联 `SignalEnsemble` / `MetaLabel`（信号融合与二级仓位判定，多个 `Strategy` 可共同参与同一个 `SignalEnsemble`）
- `ValidationRun` 聚合
  - `BacktestRun`
  - `OptimizationRun`
  - `PaperRun`
  - `LiveRun`
- `Execution` 聚合
  - `ExecutionSignal`
  - `OrderExecution`
  - `PositionSnapshot`
- `Risk` 聚合
  - `RiskProfile`
  - `RiskEvent`
- `Review` 聚合
  - `ReviewReport`
  - `FailureRecord`

### 4.2 主链路关系

主链路应固定为：

`StrategyIdea -> StrategyDraft -> Strategy -> StrategyVersion -> StrategyCodeArtifact -> SignalEnsemble -> MetaLabel -> BacktestRun -> PaperRun -> LiveRun -> ReviewReport -> FailureRecord -> Strategy`

信号融合与二级仓位判定（`SignalEnsemble -> MetaLabel`）发生在单策略信号生成之后、进入回测/执行之前，即历史回测阶段就要按融合后的候选交易来评估，不能只回测单策略信号。

这条链路的意义：

- 保证所有策略都能追溯到研究起点
- 保证失败与迭代能够回写到策略主资产
- 保证多信号叠加时经过相关性过滤与二级仓位判定，而不是各信号各自开仓

### 4.3 辅助链路关系

- `IngestionJob -> ResearchSource -> StrategyIdea`
- `RiskEvent -> PaperRun/LiveRun/ExecutionSignal`
- `ReviewReport -> StrategyVersion / FailureRecord`
- `AgentTask -> 任意核心对象`

---

## 05 状态流设计

### 5.1 Strategy 主状态

建议状态机：

- `drafting`
- `ready_for_codegen`
- `code_generated`
- `in_backtest`
- `backtest_rejected`
- `paper_candidate`
- `in_paper_run`
- `paper_rejected`
- `live_candidate`
- `in_live_run`
- `active`
- `paused`
- `retired`

### 5.2 Validation 准入状态

建议准入判断对象统一输出：

- `eligible_for_backtest`
- `eligible_for_paper`
- `eligible_for_live`
- `rejected_with_reason`

### 5.3 RiskEvent 处理状态

- `detected`
- `acknowledged`
- `mitigated`
- `resolved`
- `archived`

### 5.4 Review 结论状态

- `continue`
- `tune`
- `pause`
- `retire`
- `investigate`

---

## 06 六层架构中的对象分配

### Data Layer

- `IngestionJob`
- 市场/事件/研究源原始记录对象

### Strategy Layer

- `StrategyIdea`
- `StrategyDraft`
- `Strategy`
- `StrategyVersion`
- `SignalEnsemble`（信号融合子模块）
- `MetaLabel`（二级仓位判定子模块）

### AI Agent Layer

- `AgentTask`
- `StrategyCodeArtifact`
- 各 Agent 分析输出对象

### Validation Layer

- `BacktestRun`
- `OptimizationRun`
- `PaperRun`
- `LiveRun`

### Execution Layer

- `ExecutionSignal`
- `OrderExecution`
- `PositionSnapshot`
- `RiskProfile`
- `RiskEvent`

### Review Layer

- `ReviewReport`
- `FailureRecord`

---

## 07 接口簇设计

本轮只定义接口簇，不定最终字段级 schema。

### 7.1 Strategy Lifecycle APIs

职责：

- 交易假设录入
- 规则化草案生成与审核
- 策略创建、版本化、状态迁移

应覆盖：

- `StrategyIdea`
- `StrategyDraft`
- `Strategy`
- `StrategyVersion`

### 7.2 Backtest & Optimization APIs

职责：

- 提交回测任务
- 获取回测结果
- 提交优化任务
- 查询准入结论

应覆盖：

- `BacktestRun`
- `OptimizationRun`

### 7.3 Paper/Live Run APIs

职责：

- 启动/暂停/停止模拟盘
- 启动/暂停/停止小资金实盘
- 查询运行指标与状态

应覆盖：

- `PaperRun`
- `LiveRun`

### 7.4 Risk Event APIs

职责：

- 接收风险事件
- 查看风险开关
- 确认熔断与恢复

应覆盖：

- `RiskProfile`
- `RiskEvent`

### 7.5 Review & Reporting APIs

职责：

- 查询日报
- 查询策略复盘结论
- 查询失败记录

应覆盖：

- `ReviewReport`
- `FailureRecord`

### 7.6 Reference Data & Source Ingestion APIs

职责：

- 管理研究源/新闻源/社媒源/宏观源接入任务
- 查询接入任务状态与输出引用

应覆盖：

- `IngestionJob`

---

## 08 Agent 输入输出对象设计

### 8.1 Strategy Agent

输入：

- `StrategyIdea`

输出：

- `StrategyDraft`

### 8.2 Coding Agent

输入：

- `Strategy`
- `StrategyVersion`

输出：

- `StrategyCodeArtifact`

### 8.3 Backtest Agent

输入：

- `StrategyCodeArtifact`
- `BacktestRun`

输出：

- 完成后的 `BacktestRun`

### 8.4 Optimization Agent

输入：

- `StrategyCodeArtifact`
- `OptimizationRun`

输出：

- 完成后的 `OptimizationRun`

### 8.5 Research Agent

输入：

- `IngestionJob`
- 外部研究源记录

输出：

- 新的 `StrategyIdea`

### 8.6 News/Twitter/Telegram/Risk/Review Agent

输入：

- `IngestionJob` 或运行结果对象

输出：

- `RiskEvent`
- `ReviewReport`
- 分类摘要对象

---

## 09 任务编排骨架

### 9.1 编排目标

任务编排必须围绕结构化对象，不围绕文件路径。

### 9.2 主任务链

建议主任务链：

1. `capture_strategy_idea`
2. `generate_strategy_draft`
3. `approve_draft`
4. `create_strategy_version`
5. `generate_code_artifact`
6. `run_backtest`
7. `run_optimization`
8. `evaluate_validation_result`
9. `start_paper_run`
10. `monitor_risk_events`
11. `generate_review_report`
12. `write_failure_record`
13. `decide_next_strategy_state`

### 9.3 编排约束

- 不允许未通过 `approve_draft` 直接进入 `create_strategy_version`
- 不允许未通过验证层直接进入 `start_paper_run`
- 不允许未通过风险约束直接进入执行层
- 不允许 Review 结果只停留在报告层，必须可回写

### 9.4 调度角色

- FastAPI：同步接口入口
- Celery：异步任务执行
- Redis：短期任务状态、缓存和协调辅助
- PostgreSQL：事实记录与最终状态来源

---

## 10 下一步实现承接建议

本设计包完成后，下一步实现优先级建议为：

1. 先把这些对象固化为 Python 领域模型
2. 再按接口簇组织 FastAPI 路由骨架
3. 再进入“数据与接入设计包”
4. 然后进入“Agent 与任务编排设计包”
5. 最后细化“执行/风控/复盘设计包”

---

## 结论

领域与接口设计包的核心成果，不是把字段写到数据库，而是把平台的“主语”先定下来：

- 什么是研究入口
- 什么是正式策略资产
- 什么是验证运行对象
- 什么是执行事实对象
- 什么是风险事件
- 什么是复盘知识对象
- 它们如何在六层架构之间流动

这些对象一旦稳定，后面的 API、任务编排和实现就不会失焦。

