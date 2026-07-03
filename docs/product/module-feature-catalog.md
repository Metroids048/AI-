# 各模块功能清单（细化版）

## 文档定位

本文件是开发前文档包的第 3 份，把 [feature-catalog.md](feature-catalog.md)（功能清单总表）
按模块展开为更细粒度的功能点清单，并为每个功能点标注：所属阶段（P0/P1/P2，与
[phase-roadmap.md](../roadmap/phase-roadmap.md)、
[appendix-b-feature-phasing.md](../architecture/appendix-b-feature-phasing.md) 一致）、
涉及的领域对象（引用 [domain-and-interfaces-design.md](../architecture/domain-and-interfaces-design.md)）、
涉及的接口簇、涉及的 Agent（若有）。

本文件不新增 `feature-catalog.md` 之外的功能大类，只做展开，不重复裁决 `prd.md` 已给出的
用户故事和验收标准（本文件回答"有哪些功能点"，PRD 回答"每个模块好不好用、达标标准是什么"）。

图例：`阶段` 列的 P0/P1/P2 含义见 `appendix-b-feature-phasing.md`；标注"框架"表示该功能在
对应阶段只要求接口/对象/调度位置就位，不要求真实数据全量在线（呼应
`platform-master-design.md` §6.2 分阶段启用原则）。

---

## 01 研究入口模块

对应目录：`apps/api`（接口）+ `services/strategy_library`（StrategyIdea 归属）。

| 功能点 | 阶段 | 领域对象 | 接口簇 | 涉及 Agent |
|---|---|---|---|---|
| 假设录入（表单/结构化提交） | P0 | `StrategyIdea` | Strategy Lifecycle APIs | — |
| 假设来源标签（人工/WorldQuant/GitHub/论文/A股系统等） | P0 | `StrategyIdea.origin_type/origin_reference` | Strategy Lifecycle APIs | — |
| 假设标签与分类（市场范围/主题标签） | P0 | `StrategyIdea.tags/market_scope` | Strategy Lifecycle APIs | — |
| 假设筛选状态查看（captured/screened/rejected/accepted_for_drafting） | P0 | `StrategyIdea.review_status` | Strategy Lifecycle APIs | — |
| 外部研究源自动导入假设 | P1（框架 P0） | `IngestionJob -> StrategyIdea` | Reference Data & Source Ingestion APIs | Research Agent |
| 假设 -> 草案规则化触发 | P0 | `StrategyIdea -> StrategyDraft` | Strategy Lifecycle APIs | Strategy Agent |
| 草案人工审核（通过/拒绝/退回修改） | P0 | `StrategyDraft.draft_status` | Strategy Lifecycle APIs | — |

---

## 02 策略库模块

对应目录：`services/strategy_library`。

| 功能点 | 阶段 | 领域对象 | 接口簇 | 涉及 Agent |
|---|---|---|---|---|
| 策略浏览（列表/筛选/搜索） | P0 | `Strategy` | Strategy Lifecycle APIs | — |
| 策略详情（规则/来源/当前状态） | P0 | `Strategy` | Strategy Lifecycle APIs | — |
| 版本管理（版本历史/变更摘要/回溯） | P0 | `StrategyVersion` | Strategy Lifecycle APIs | — |
| 状态管理（drafting -> ... -> active/paused/retired） | P0 | `Strategy.strategy_status` | Strategy Lifecycle APIs | — |
| 失败原因查看 | P0 | `FailureRecord` | Review & Reporting APIs | — |
| 策略代码工件查看/编译状态 | P0 | `StrategyCodeArtifact` | Strategy Lifecycle APIs | Coding Agent |
| 相关性矩阵查看（策略间信号相关度） | P1 | `SignalEnsemble.correlation_matrix_ref` | Strategy Lifecycle APIs | — |
| 信号融合结果查看（多策略/alpha 融合后的候选方向与置信度） | P1 | `SignalEnsemble` | Strategy Lifecycle APIs | — |
| 二级仓位判定结果查看（是否下注、下注比例、依据的三重界限标注） | P1 | `MetaLabel` | Strategy Lifecycle APIs | — |
| WorldQuant alpha 来源权重查看（作为融合层低权重投票，非独立策略展示） | P1 | `SignalEnsemble.raw_votes` | Strategy Lifecycle APIs | — |
| 策略迭代历史（`iteration_history` 字段可视化） | P0 | `Strategy`/`FailureRecord` | Strategy Lifecycle APIs | Review Agent |

---

## 03 验证模块（回测/优化/样本外/模拟盘准入）

对应目录：`services/validation`（+ 独立 `freqtrade` 容器）。

| 功能点 | 阶段 | 领域对象 | 接口簇 | 涉及 Agent |
|---|---|---|---|---|
| 回测任务提交与查询 | P0 | `BacktestRun` | Backtest & Optimization APIs | Backtest Agent |
| 回测报告查看（指标摘要） | P0 | `BacktestRun.metrics_summary`（= `BacktestReport`） | Backtest & Optimization APIs | — |
| Deflated Sharpe / `trials_count` 展示（多重检验偏差校正） | P1 | `BacktestRun.metrics_summary.deflated_sharpe` | Backtest & Optimization APIs | — |
| 成本建模结果展示（手续费/滑点/资金费率净收支） | P1 | `BacktestRun.cost_model_ref`/`total_cost_bps` | Backtest & Optimization APIs | — |
| Walk-Forward 滚动验证结果展示 | P1 | `BacktestRun.validation_methodology`/`sample_split_plan` | Backtest & Optimization APIs | — |
| 压力测试场景覆盖展示（LUNA崩盘/312/交易所宕机/极端插针） | P1 | `BacktestRun.stress_test_scenarios` | Backtest & Optimization APIs | — |
| 参数优化任务提交与查询 | P0 | `OptimizationRun` | Backtest & Optimization APIs | Optimization Agent |
| 参数敏感性/过拟合风险展示 | P1 | `OptimizationRun.best_candidate_summary` | Backtest & Optimization APIs | Optimization Agent |
| 样本外验证结果查看 | P0 | `BacktestRun.sample_split_plan` | Backtest & Optimization APIs | Backtest Agent |
| 模拟盘准入结论查看（eligible/rejected_with_reason） | P0 | `BacktestRun.eligibility_result` | Backtest & Optimization APIs | — |
| 模拟盘准入人工确认操作 | P0 | `PaperRun`（创建） | Paper/Live Run APIs | — |

---

## 04 执行模块（模拟盘/实盘）

对应目录：`services/execution`（+ Risk Engine）。

| 功能点 | 阶段 | 领域对象 | 接口簇 | 涉及 Agent |
|---|---|---|---|---|
| 模拟盘运行视图（状态/持仓/指标） | P0 | `PaperRun`/`PositionSnapshot` | Paper/Live Run APIs | — |
| 模拟盘启动/暂停/停止操作 | P0 | `PaperRun.paper_status` | Paper/Live Run APIs | — |
| 实盘运行视图（状态/持仓/指标） | P1 | `LiveRun`/`PositionSnapshot` | Paper/Live Run APIs | — |
| 实盘启动/暂停/停止操作（人工关口） | P1 | `LiveRun.live_status` | Paper/Live Run APIs | — |
| 小资金实盘晋升人工确认 | P1 | `LiveRun`（创建） | Paper/Live Run APIs | — |
| 执行日志（订单/信号/异常） | P0 | `OrderExecution`/`ExecutionSignal` | Paper/Live Run APIs | — |
| 执行前置检查结果展示（融合/否决/止损计划是否具备） | P1 | `ExecutionSignal.signal_status` | Paper/Live Run APIs | Decision Veto Agent |
| Decision Veto Agent 否决记录查看 | P1 | `AgentTask`（veto 输出） | Paper/Live Run APIs | Decision Veto Agent |
| 硬拒绝原因展示（无止损/超限/熔断中/数据中断） | P0 | `ExecutionSignal`/`RiskEvent` | Risk Event APIs | — |

---

## 05 风控模块

对应目录：`services/execution`（Risk Engine 子模块，与 Execution 共享目录，逻辑独立）。

| 功能点 | 阶段 | 领域对象 | 接口簇 | 涉及 Agent |
|---|---|---|---|---|
| 风险规则配置（单笔/单品种/总持仓/并发/杠杆等） | P0 | `RiskProfile` | Risk Event APIs | — |
| 日/周最大亏损、连续亏损、最大回撤规则配置 | P0 | `RiskProfile` | Risk Event APIs | — |
| 极端回撤熔断配置与状态查看 | P0 | `RiskProfile`/`RiskEvent` | Risk Event APIs | Risk Agent |
| 风险事件流查看（按 severity 分级） | P0 | `RiskEvent` | Risk Event APIs | Risk Agent |
| 宏观/新闻分级触发规则配置（高/中/低严重度对应动作） | P1 | `RiskEvent.severity`/`recommended_action` | Risk Event APIs | News Agent / Risk Agent |
| 熔断状态与恢复流程（detected -> acknowledged -> mitigated -> resolved） | P0 | `RiskEvent.resolution_status` | Risk Event APIs | — |
| 熔断人工恢复操作 | P0 | `RiskEvent.resolution_status` | Risk Event APIs | — |
| API 连续失败/数据中断自动降级状态查看 | P1 | `RiskEvent`（`api_failure`/`data_gap`） | Risk Event APIs | — |
| 稳定币异常应急处理状态查看 | P1 | `RiskEvent`（`market_structure_risk`） | Risk Event APIs | Risk Agent |
| 交易所 API Key 权限自检结果查看（是否含提现权限） | P1 | 无独立领域对象，属于启动期检查（见技术架构方案 §8.3） | — | — |

---

## 06 复盘模块

对应目录：`services/review`。

| 功能点 | 阶段 | 领域对象 | 接口簇 | 涉及 Agent |
|---|---|---|---|---|
| 每日复盘日报生成与查看 | P0 | `ReviewReport` | Review & Reporting APIs | Review Agent |
| 表现最差策略识别 | P0 | `ReviewReport.worst_performer_refs` | Review & Reporting APIs | Review Agent |
| 偏差归因（市场/参数/执行/风控） | P0 | `ReviewReport.deviation_analysis` | Review & Reporting APIs | Review Agent |
| 继续/调参/暂停/淘汰建议 | P0 | `ReviewReport.recommendations` | Review & Reporting APIs | Review Agent |
| 失败记录查看与检索 | P0 | `FailureRecord` | Review & Reporting APIs | — |
| 失败记录 -> 策略/研究复用标记 | P1 | `FailureRecord.recommended_change` | Review & Reporting APIs | Research Agent / Strategy Agent |
| 复盘结论回写策略状态 | P0 | `Strategy.strategy_status`（受 `ReviewReport` 驱动） | Strategy Lifecycle APIs | Review Agent |
| LLM 生成内容标注与溯源展示 | P1 | `ReviewReport`（标注生成来源） | Review & Reporting APIs | Review Agent |

---

## 07 数据模块

对应目录：`services/data`。

| 功能点 | 阶段 | 领域对象 | 接口簇 | 涉及 Agent |
|---|---|---|---|---|
| A 级市场数据接入（OHLCV/资金费率/OI/多空比/清算/订单簿） | P0 | `IngestionJob`（`source_family=A`）+ TimescaleDB 表 | Reference Data & Source Ingestion APIs | — |
| A 级数据质量/延迟/缺口检查 | P1 | `IngestionJob.job_status` | Reference Data & Source Ingestion APIs | — |
| B 级宏观事件接入（框架先行） | P1（框架 P0） | `IngestionJob`（`source_family=B`） | Reference Data & Source Ingestion APIs | — |
| C 级新闻数据接入（框架先行） | P1（框架 P0） | `IngestionJob`（`source_family=C`） | Reference Data & Source Ingestion APIs | News Agent |
| D 级社媒数据接入（框架先行） | P1（框架 P0） | `IngestionJob`（`source_family=D`） | Reference Data & Source Ingestion APIs | Twitter Agent / Telegram Agent |
| E 级研究数据接入（框架先行） | P1（框架 P0） | `IngestionJob`（`source_family=E`） | Reference Data & Source Ingestion APIs | Research Agent |
| 数据源接入状态面板（在线/离线/框架未启用） | P0 | `IngestionJob` 汇总视图 | Reference Data & Source Ingestion APIs | — |
| WorldQuant 本地 alpha 方法论移植（算子/因子构造模式） | P1 | 不进入主执行链路，归属 `research_source/worldquant_adapter` | — | Research Agent |

---

## 08 跨模块 / 平台级功能（不属于 feature-catalog.md 单一分类，但被多模块依赖）

这类功能不新增业务大类，只是把散落在多个设计文档中的、feature-catalog.md 未单列但已被
承诺存在的能力集中登记，避免遗漏排期。

| 功能点 | 阶段 | 说明 |
|---|---|---|
| AgentTask 统一任务查看/重试/取消 | P0 | 承接 `agent-and-orchestration-design.md` §03，是所有 Agent 任务的统一管理入口，不属于单一业务模块 |
| 人工决策点操作留痕（操作者/时间/原因） | P0 | 承接 PRD §5.2 可审计性要求，横跨策略库/验证/执行/风控四个模块 |
| 通知/告警出站推送（Telegram/邮件/Webhook） | P1 | 当前完全缺失（见技术架构方案 §12 缺口表），承接高严重度风险事件、模拟盘/实盘异常、每日复盘日报的主动触达，具体触发规则由"24 小时自动实时交易运行方案"文档裁决 |
| 环境隔离状态可视化（当前 dev/test/paper/live 各自运行状态） | P1 | 承接 `environment-and-config.md` 四环境声明，避免研究者混淆环境 |
| Decision Veto Agent 否决记录的平台级审计视图 | P1 | 跨执行模块与 Agent 任务视图，属于 ADR-013 的产品化落地 |

---

## 09 与 delivery-checklist.md 的关系

`delivery-checklist.md` "后续进入开发前必须具备" 清单中的 6 项（领域模型代码/API schema/
数据接入抽象/Celery 任务图/风险规则实现/Review 回写实现）都不是功能点，而是实现前提，
本文件不重复登记，只在此提示：本文件列出的每个功能点，在真正排期前都必须先确认对应的
实现前提已具备（例如"风险规则配置"功能点依赖"风险规则实现"这一前提）。

---

## 10 下一步

本文件完成后，下一份交付是量化策略收集/评分/淘汰机制方案（不写具体策略参数，只设计机制）。
