# 任务计划：自动开平仓系统与量化策略缺陷修复

## 目标

按用户诊断报告的 Phase 0-5 remediation 计划，逐模块逐问题修复自动交易系统的架构缺陷，每步验证后再推进，不确定处必须先问用户。

## 当前阶段

Phase 0/1/2/3 均已完成并同步全部记忆文件。按用户"不用中断，下一个phase以上一个phase的结果制定方案，自动执行"的指示，正在自动推进 Phase 4（验收单与真实策略单隔离 + ExitLadder 端到端测试）与 Phase 5（跨品种相关性组合风控）。

## 各阶段

### Phase 0：ExitLadder 回放对比（核查后确认：已完成，非本轮工作）

- [x] `services/validation/technical_replay.py` 已支持 `exit_mode`（`EXIT_MODE_FIXED_2R`/`EXIT_MODE_EXIT_LADDER`），非通过 `--exit-model` CLI 参数，而是 `TechnicalStrategyValidationService(exit_mode=...)` 构造参数
- [x] 独立脚本 `scripts/run_exitladder_replay_comparison.py`（区别于 `run_top20_technical_validation.py`）驱动同层入场信号下 Fixed 2R vs ExitLadder 的对比回放，只产出审计证据、不改动自动配置
- [x] 已有测试覆盖 `exit_mode` 参数：`tests/services/test_technical_strategy_validation.py`
- [x] 已产出审计文档：`docs/audits/2026-07-12-exitladder-replay-comparison.md`
- [x] 结论已依据当前门槛判断：ExitLadder 净期望 `-0.000866` 差于 Fixed 2R 的 `0.002185`，PF `0.8817` 差于 `1.1308`，最大回撤 `1.4356` 差于 `0.5220`——**不建议**用 ExitLadder 替换/降级现有 4h/1h/15m 主链路
- **状态：** complete（本轮核查确认，实际工作发生于 2026-07-12 TASK-052，见 `.github/agent/memory/task-history.md`）

> 注：此前基于原始诊断报告的描述误判 Phase 0 尚未开始，本轮实施 Phase 1 时核对代码与既有审计文档后发现已完成，特此更正，避免重复劳动。

### Phase 1：设置断链修复（已完成）

- [x] 诊断"杠杆滑块不生效"根因：`paper_signal.py` 的 `_requested_leverage`/`_requested_notional` 因 Pydantic `default_factory` 导致 `asset_risk_tiers` 永远非空，从而始终优先于 `position_rules.max_leverage`；前端滑块只改后者（死字段），`asset_risk_tiers` 从未被 UI 真正编辑，每次保存都原样回传旧值
- [x] 用户确认方案 B：滑块联动分档表（保留 core/standard/vol_* 分档结构，随杠杆/单币敞口滑块按比例重新计算各档杠杆与仓位上限）
- [x] `services/execution/risk_tiers.py` 新增 `TIER_SCALE_RATIOS` + `scale_asset_risk_tiers()`
- [x] `apps/api/routers/runs.py::update_paper_run_auto_settings` 接入重新计算逻辑，替换掉客户端回传的旧分档表
- [x] 前端 `RuntimePanels.jsx` 修正 `DEFAULT_AUTO_SETTINGS` 的分档默认值（与后端一致），用 `renderTierPreviewRows()` 替换失效的静态预览文案
- [x] 新增/扩展测试：`tests/services/test_asset_risk_tiers.py`（3 个新用例）、`tests/api/test_paper_runtime_api.py`（断言 tier 杠杆随滑块变化）
- [x] 统一 remainder-trail 重复实现：`services/execution/exit_ladder.py` 新增共享纯函数 `next_trailed_stop_price()`；`paper_runtime.py::_apply_trailing_ratchet` 与 `technical_replay.py::_advance_open_position` 均改为调用该函数，不再各自手写 LONG/SHORT 分支
- [x] 新增 `tests/services/test_exit_ladder.py` 中 5 个 `next_trailed_stop_price` 专项用例
- [x] 运行 ruff/mypy/pytest 校验本轮改动
- [x] 更新全部项目记忆文件（本文件 + findings.md + progress.md + `.github/agent/memory/*`）
- **状态：** complete

### Phase 2：执行层默认行为修正（已完成）

- [x] 默认改为限价单（保留可配置的市价单兜底）——核查后发现**已实现**：`shared/config.py:58` 的 `execution_default_order_type="limit"` 已是默认值，`paper_signal.py:82`/`gateway.py:339` 均以 `strategy.rules.entry_rules.get("order_type", settings.execution_default_order_type)` 兜底，仅 `submit_acceptance_order`/`submit_carry_order`（基础设施探测/搬砖腿，非方向性策略入场）显式传 `"market"`。无需改动代码。
- [x] 收紧反向信号平仓逻辑——核查 `paper_runtime.py:606-830` 确认保护性触发（止损/止盈）分支已严格在反向信号平仓分支**之前** `continue`，同一根K线上止损止盈永远优先于反向信号平仓。新增回归测试 `test_runtime_stoploss_wins_over_opposite_signal_hit_on_same_bar` 锁定该行为，非行为变更。
- [x] 解决 ADR-023 重复编号（2026-07-03 与 2026-07-12 两条）——已删除错位重复条目，重新编号并迁移为 **ADR-058**。
- [x] 对齐 ADR-023 文档记录的风险比例与 `bootstrap.py` 实际值的差异——已修正为 `max_portfolio_initial_risk_fraction=0.15`（15%），与 `bootstrap.py` 中 `AUTO_PAPER_TECHNICAL_RULES` 实际值一致（原文档误写 5%）。
- **状态：** complete

### Phase 3：多周期信号密度改造（已完成）

- [x] 从"全票通过（fail-closed AND-gate）"改为"多数票 + 权重"——核查 `services/strategy_library/ensemble.py::layered_regime_entry` 确认已实现：方向类信号源（`technical_dow_trend`/`technical_ema_trend`/`technical_adx`/`technical_mtf_ma`/`technical_macd` 等）按多数票裁决方向，`MIN_DIRECTION_SOURCE_QUORUM=3`，仅在票数不足或恰好平票时 fail-closed，不再要求全票一致。`tests/services/test_signal_ensemble.py` 已覆盖多数票/平票/不足quorum/反向票剔除四类场景，全部通过。
- **状态：** complete

### Phase 4：验收与真实策略隔离（已完成）

- [x] `TestnetAcceptanceService` 探测单与真实策略单分离——核查后确认**已通过两种不同机制分别隔离**：
  - 探测单/搬砖腿（`testnet_acceptance`、`binance_demo_reconciliation`）：`services/execution/demo_audit.py::BinanceDemoAuditService` 用专属的 `BINANCE_DEMO_AUDIT_KEY` Strategy/BacktestRun/PaperRun 三件套承载，订单 `entry_context` 显式打上 `execution_kind`/`strategy_performance_eligible=False` 标签，与真实策略单（`paper_runtime.py::_record_estimated_order_cost` 打 `execution_kind="strategy_trade"`）区分。
  - 资金费率搬砖两腿（`services/execution/carry_execution.py::CarryExecutionService`）：追踪完整调用链（`SpotCarryGateway`/`PerpCarryGateway` → `gateway.py::BinanceUsdtPerpetualGateway.submit_order`/`submit_carry_order`，全文 lines 325-524 逐行核查）确认**从未调用 `ExecutionRepository.create_order()`**；搬砖结果只作为 `CarryExecutionLegResult` 存在 `CarryExecutionStatus` 里，经 `AgentTaskRepository.update_task(output_payload=...)` 落库（`apps/api/routers/runs.py::create_carry_execution`）。由于真实策略的绩效统计只读 `OrderExecution` 表，搬砖腿天然不会污染绩效——无需新增标签，无需改代码。
  - **结论：探测单与搬砖腿两条隔离路径均已到位，本项无代码改动。**
- [x] 为 ExitLadder 补充端到端测试——新增 `tests/api/test_paper_runtime_api.py::test_paper_runtime_auto_cycle_partial_closes_via_exit_ladder`：
  - 用真实决策管线（非 `_runtime_with_position` 直接注入）走满两轮 `POST /auto-cycle`：第一轮建仓，第二轮送入触发 L1（`r_multiple=1.0, close_fraction=0.4`）的 1 分钟保护K线。
  - 通过给策略配置确定性的 `stoploss_rules={"fixed_bps": 200}`（而非依赖 ATR 等动态止损），使入场价（=最后一根 1h 收盘价）与止损距离在测试代码里可预先算出，从而能精确构造第二轮 1 分钟K线的 high/low 来命中 L1 触发价，无需读回接口响应反推。
  - 断言：`actions[0].action == "exit_ladder_partial_long"`，`decision_trace.close_fraction == 0.4`，`decision_trace.remaining_quantity > 0`，且 `runtime-status` 显示仓位未完全平仓（`open_position_symbols == ["BTC/USDT"]`）。
  - 为支持该测试，扩展了 `_create_validated_paper_run()` 增加可选的 `strategy_key`/`stoploss_rules`/`takeprofit_rules` 参数（默认值与原硬编码值完全一致，不影响既有测试）。
  - 运行 `pytest tests/api/test_paper_runtime_api.py -q`：7 passed（6 项既有 + 1 项新增）；随后运行全量 `pytest -q`：321 passed, 1 skipped；`ruff check tests/api/test_paper_runtime_api.py`：全部通过。
- **状态：** complete

### Phase 5：组合层风控（已完成核查，非新增实现）

- [x] 核查跨品种相关性组合风控是否已存在：
  - `services/execution/portfolio_risk.py`：`correlation()`（Pearson 相关系数，样本<2或方差为0返回`None`触发fail-closed）、`close_returns()`（要求至少61根K线收盘价，产出60个收益率，样本不足返回`None`）、`signed_exposure()`（按`TradeSide`带符号的名义敞口/账户权益）。
  - `services/execution/paper_signal.py::_build_risk_state()`（lines 391-486）：对每个已持仓品种（跳过同品种）用最近61根1h K线收益率与候选品种计算相关系数；同方向且相关系数>0.70计入`high_correlation_peer_count`/`correlated_cluster_exposure`；任一品种收益率不足60根或计算失败则整体`portfolio_correlation_available=False`（fail-closed，非fail-open）；存在同方向高相关对手仓位时按`max(0.5, 1-corr)`折算下单名义本金（地板0.5，避免与gatekeeper的双对手强拒绝叠加导致临界单被过度压缩至近零）。
  - `services/execution/gatekeeper.py::_evaluate_numeric_risk()`（lines 203-213）：`portfolio_correlation_available=False` → 拒绝（`portfolio_correlation_unavailable`，且仅对新开仓生效，`close_only_mode`不受影响）；`high_correlation_peer_count>=2` → 拒绝（`correlated_exposure_limit_exceeded`）；`correlated_cluster_exposure+requested_fraction>0.35` → 拒绝（`correlated_cluster_exposure_exceeded`）；`net_directional_exposure`超±0.40 → 拒绝（`net_directional_exposure_exceeded`）。
  - 测试覆盖：`tests/services/test_execution_gatekeeper.py`（`test_gatekeeper_rejects_two_high_correlation_peers`、`test_gatekeeper_rejects_correlated_cluster_and_net_directional_exposure`、`test_gatekeeper_rejects_missing_portfolio_correlation_for_new_order_but_not_close`、`test_portfolio_return_correlation_requires_full_60_bar_window`）全部覆盖上述四条拒绝路径与60根窗口边界。
  - 追溯 `.github/agent/memory/task-history.md` 的 `[TASK-048]` 条目：该相关性风控与fail-closed设计已在更早的任务中实现完毕，早于本轮Phase划分——**本项无需新代码，仅需核查+补充记忆归档**。
  - `services/strategy_library/playbook.py` 中的路线图项 `portfolio-correlation-risk`（P0，"补齐单品种、相关性簇和组合净敞口限制"）描述的能力已被上述实现覆盖，路线图状态字段（`StrategyRoadmapState`）默认仍为 `pending`（由运营手动标记，非代码自动同步），不影响功能已落地的事实。
  - 运行 `pytest tests/services/test_execution_gatekeeper.py tests/services/test_paper_runtime.py tests/services/test_signal_ensemble.py -q`：43 passed。
- **状态：** complete（核查性任务，无代码改动）

## 关键问题

1. 每一步都要先给独立分析，再对照用户第 3 点给出的方案/Prompt 顺序执行，任何不确定必须先问用户。
2. Phase 1 的具体技术方案（分档表联动、remainder-trail 统一方式）已获用户明确授权由我决定（"这部分你定吧"），但架构方向仍需用户对齐确认（已通过 AskUserQuestion 完成方案 B 的确认）。
3. 完成任务后必须更新全部项目文件以保持进度同步——这是用户的明确标准要求，不是可选项。

## 已做决策

| 决策 | 理由 |
|------|------|
| Phase 1 采用方案 B：滑块联动分档表 | 用户通过 AskUserQuestion 明确选择，保留分档结构的同时让滑块真正驱动生效值。 |
| remainder-trail 统一提取到 `exit_ladder.py::next_trailed_stop_price()` | 确认 `paper_runtime.py` 与 `technical_replay.py` 的手写逻辑在数学上完全等价（均为"价格向有利方向移动 trail_after_r*initial_distance 后止损归零风险"），提取共享纯函数消除重复而不改变行为；用户已将此实现细节授权给我决定。 |

## 遇到的错误

本轮暂无运行时错误；Phase 1 测试一次性通过（37/37）；Phase 2/3 核查+新增测试后全量 `pytest -q` 通过（320 passed, 1 skipped），ruff/mypy 全部通过。

---

## 任务计划：第零节 —— LLM 决策否决链路诊断与修复

> 与上方 Phase 0-5（自动开平仓系统缺陷修复）是不同的独立任务线。用户明确要求本节范围严格限定在"第零节"（诊断为何 LLM 辅助决策否决链路从未被真正调用+用免费额度验证修复），一~五节（跨品种策略、对抗式审查网关、MetaLabel 训练升级、RAG 向量化、Review Agent LLM 归因）在用户明确要求前不得触碰。Docker Desktop 相关验证被用户明确推迟为单独决策，不在本节范围内。

### 目标（第零节）

诊断并修复 LLM 决策否决链（`decision_veto_agent` / `pre_execution_veto_llm`）为何在没有任何日志、任何报错的情况下被静默跳过，然后用免费额度（OpenRouter / GitHub Models）+ SQLite 测试 harness（`tests/conftest.py`）端到端验证修复确实生效。

### 已完成的修复（第零节）

- [x] **环境变量兼容修复**：`shared/config.py` 的 `claude_api_key` 字段增加 `AliasChoices("CLAUDE_API_KEY", "ANTHROPIC_API_KEY")`，兼容两种环境变量命名。
- [x] **无可用 provider 时的日志**：`services/agents/llm_factory.py` 在没有任何 provider 配置成功、回退到 `UnavailableLLMRuntime` 时，以及成功构建出可用 runtime 时，均补充结构化日志（此前完全静默，是"链路从未被调用却零报错"现象的根因之一）。
- [x] **异常拆分处理**：`services/agents/service.py::_execute_llm_veto` 将原来笼统的异常捕获拆分为 `TimeoutError` / `RuntimeError`（含 `LLMProviderUnavailable`）/ 其余 `Exception` 三条分支，分别落不同的失败原因到 `AgentTask.output_payload`，不再一律吞掉成不可区分的通用失败。
- [x] **诊断接口**：新增 `GET /agents/llm-status`（`apps/api/routers/agents.py`），暴露当前实际生效的 provider 链路状态，供运营/未来自查使用，不依赖翻日志。
- [x] **首次失败通知**：（见上一会话记录，此会话中重新 Read 确认已正确落地，未改动）。
- [x] **顺带修复 `bootstrap.py` 的 `llm_veto_enabled` 重启重置 bug**（用户批准"顺带修掉 (推荐)"）：`_ensure_auto_paper_run` 的 `preserved_keys` 元组补充 `"llm_veto_enabled"`，否则运营手动关闭 LLM 否决后，每次 bootstrap 重启都会被硬编码的 `True` 静默改回启用。
- [x] **新发现并修复：OpenRouter 免费模型种子列表过期**：端到端连通性验证时发现 `llm_factory.py::_OPENROUTER_SEED_MODELS` 中两个模型 ID（`meta-llama/llama-3.1-8b-instruct:free`、`google/gemma-3-27b-it:free`）已被 OpenRouter 下架，导致所有 OpenRouter 请求 404。这与用户最初报告的"链路静默失效"是同一类症状的另一个真实成因，不是本次五点计划原本预期的问题，但必须一并修掉才能让免费额度验证真正通过。已替换为验证通过的存活免费模型（`nvidia/nemotron-3-super-120b-a12b:free`、`nvidia/nemotron-3-nano-30b-a3b:free`）。
- [x] **GitHub Models 免费额度验证受阻，已按用户选择跳过**：诊断确认该 PAT 缺少 "Models" 权限范围（`models.inference.ai.azure.com` 返回 401 "The `models` permission is required"，`models.github.ai/inference` 返回 403 "no_access"；但同一 token 能正常访问 `models.github.ai/catalog/models` 目录列表 GET 接口，证明是 token 权限配置问题，不是代码缺陷）。已用 AskUserQuestion 向用户说明，用户选择"先跳过,只用OpenRouter验证 (推荐)"——本节的"用免费额度验证"要求由 OpenRouter 单独满足。
- [x] **端到端验证**：新增可选启用集成测试 `tests/integration/test_llm_decision_veto_live.py`（沿用项目既有的 `@pytest.mark.integration` + `RUN_<NAME>_INTEGRATION` 环境变量门控约定，参照 `tests/integration/test_binance_public_smoke.py`），通过真实 `AgentTaskService.submit_task()` → `_execute_llm_veto()` → `AgentTaskRepository` 落库路径，用 SQLite harness（`tests/conftest.py::db_session`）+ 真实 OpenRouter 免费模型跑通全链路。`RUN_LLM_VETO_INTEGRATION=1 pytest tests/integration/test_llm_decision_veto_live.py -v` → PASSED（14.60s）。
- [x] 运行全量 `pytest -q`（321 passed, 2 skipped）与 `ruff check` / `mypy`（全部通过），确认本节改动未引入回归。
- [ ] 更新全部项目记忆文件（本文件 + `findings.md` + `progress.md` + `.github/agent/memory/*`）——进行中。

### 关键决策与用户澄清（第零节）

| 决策 | 理由 |
| --- | --- |
| 用户明确授权将真实 OpenRouter/GitHub Models key 直接硬编码写入 `.env` | 用户原话："现在项目只有自己用，直接写死的了，我让你删除你在删，别让我一遍一遍地给你了"——本人独用项目，之后由用户主动要求才删除，此前不再重复索要。 |
| GitHub Models 验证跳过，仅用 OpenRouter 验证 | 诊断确认为 token 权限范围问题（缺 "Models" 读取权限），非代码问题；询问用户后用户选择"先跳过,只用OpenRouter验证 (推荐)"，跳过不代表放弃，只是本节验收范围收窄为 OpenRouter。 |
| 替换 OpenRouter 种子模型而非仅报告 404 | 端到端验证是本节的硬性验收标准之一；模型下架导致的 404 与用户最初报告的症状同属"链路失效"范畴，必须修掉才能声明验证通过，不能以"这不在原计划五点内"为由回避。 |

### 遇到的错误（第零节）

- OpenRouter 两个硬编码免费种子模型已被下架（404）→ 查询存活免费模型目录后替换。
- 替换后一次运行遇到 `RemoteProtocolError('Server disconnected...')` 被误判为 JSON 解析失败 → 开 DEBUG 日志复核后确认是网络瞬断，重跑 3 次全部成功，非代码缺陷。
- GitHub Models 401/403 → 诊断为 token 权限范围问题，按用户选择跳过，不视为需要继续修的代码问题。
- `shared/models/__init__.py` 编辑后 `ruff check` 报 `I001` 导入顺序错误 → 重新排序 `LLMProviderStatus, LLMStatusResponse` 到 `LiveRunRequest` 与 `ManualOrderRequest` 之间的正确字母序位置，修复后通过。

---

## 任务计划：模块0-9 —— 用户诊断报告分模块实施方案（独立任务线，来源为对话原文粘贴，非文件）

> 与上方"Phase 0-5"及"第零节"均是不同的独立任务线。本节内容来自用户在对话中直接粘贴的《全项目审查 + 自动开平仓/量化策略问题根因诊断报告》与《分模块实施方案》原文，此前从未存入仓库任何文件，在一次上下文压缩中丢失过一次，现从会话原始 JSONL 记录中恢复并固化于此，避免再次丢失。

### 背景结论（诊断报告核心，决定了后续模块的优先级和"为什么这么做"）

- 现在这套 8~10 信号技术策略，扣除真实手续费/滑点后，历史期望值为负（`docs/audits/2026-07-12-top20-technical-validation.md`：OOS 净期望值 -0.0023）；资金费率单币 carry 与跨品种 carry 同样覆盖不了成本（ADR-063）。**拒单是系统在正确保护模拟盘，不是阈值设置错误**——不能靠继续调低门槛解决。
- "改了一版也无事发生"，大概率不是代码没改对，而是运行进程没有真正吃到新代码/新配置（历史上至少复现两次，最近一次是 2026-07-13/14 野进程绕过标准启动脚本、连错数据库地址）。
- "只有BTC/ETH真正开单"是组合相关性风控（同向相关系数>0.7 且≥2个持仓拒单）在正确工作，因为山寨币和BTC/ETH天然高相关——如果目的是测试Top20各币独立信号表现，这个阈值对该目的而言过严，这是需要用户明确取舍的点，不是bug。
- 分批止盈（ExitLadder）已用真实数据对比过固定2R，ExitLadder 全面更差（净期望 -0.000866 vs +0.002185），已按数据结论改回固定2R，非遗漏。
- 缠论买卖点是用户人工交易里唯一还未代码化的个人经验，是最值得补的真边际来源，但需要用户先手工标注20-30段买卖点K线区间才能开始。

### 总体顺序（有依赖关系，不能跳着做，原文规定）

```text
模块0 运行时对齐核查工具  ← 必须最先做，且每次改完代码都要重新跑一遍
模块1 仓位计算诊断日志
模块2 链路验证单隔离       ← 1、2 互相独立，可以并行
模块3 相关性风控阈值可配置化
模块4 Top20 数据完整性巡检 ← 3、4 互相独立，可以并行
模块5 真实边际统计流水线化 ← 依赖模块0
模块6 缠论买卖点信号        ← 独立分支，随时可以单独推进
模块7 分批止盈 A/B 测试框架通用化
模块8 失败知识回流（P2，可选）
模块9 Agent 执行器补齐（P2，可选）
```

### 模块0：运行时对齐核查工具 —— 状态：已完成

- [x] `scripts/verify_runtime_config_sync.py` 已存在，核实与原文规格一致（对比数据库 `strategy_key='auto_paper_mature_templates'` 的 rules JSON 与代码内 `AUTO_PAPER_TECHNICAL_RULES`）。

### 模块1：仓位计算诊断日志 —— 状态：已完成

- [x] `services/execution/paper_signal.py:39` `MIN_SANE_NOTIONAL_FRACTION = 0.005`；`sizing_sentinel_triggered` 日志（约 line 495/497）；`correlation_risk_discount_triggered` 日志（约 line 580）均核查在位。

### 模块2：链路验证单与策略表现单隔离 —— 状态：已完成

目标：提供一种"专门测试下单/止损/止盈/平仓链路本身"的模式，产生的订单不混进策略表现统计，也不因 `net_edge_after_cost_negative` 被拒（因为目的是测链路不是测盈利）。

**与原文规格的偏差（已与用户确认，用户选择方案二）：**

原文规格假设"关闭 `entry_rules.meta_label_min_win_rate`/`min_estimated_net_edge_bps` 门禁"就能让该 lane 绕开 `net_edge_after_cost_negative` 拒绝。实际核查 `services/execution/net_edge.py::net_edge_rejection_codes()` 与 `decision_pipeline.py` 后发现该假设不成立：真实的成本门禁读取的是 `entry_context["meta_label_win_rate"/"meta_label_average_win"/"meta_label_average_loss"/"round_trip_fee_rate"/"round_trip_slippage_rate"]`，这些值来自真实 ensemble/meta-label 计算结果（`_edge_stats_for_gate()`），与 `entry_rules.meta_label_min_win_rate` 配置项完全无关（后者只影响 meta-labeling 内部一个更早的"是否要下注"判断）。此外继续走真实信号路径还会引入 `ensemble_discarded`/`meta_label_bet_skipped` 等非确定性早退，与"链路验证必须稳定产单"的目标冲突。已就此向用户提出两个方案并由用户选择方案二（推荐）：新建专用决策分支，完全跳过真实信号/ensemble/meta-label 评估，直接构造固定方向、固定有利 edge 的订单。

**实际实现：**

1. `services/execution/paper_signal.py`：新增 `_link_verification_decision()` 方法（跳过真实信号，直接返回 `direction=LONG`、`should_trade=True`、`trace` 中硬编码 `meta_label_win_rate=1.0`/`meta_label_average_win=1.0`/`meta_label_average_loss=0.0`/`round_trip_fee_rate=0.0`/`round_trip_slippage_rate=0.0`，保证真实成本门禁计算出正 edge 而非绕过判断本身）；新增 `_is_link_verification_strategy()` 辅助函数（与 `_is_carry_strategy`/`_is_cross_sectional_strategy` 同模式，优先看 `paper_run.execution_profile["strategy_lane"]`，回退到 `entry_rules.link_verification_only`），在 `_decision_for_strategy()` 中作为第一优先级分支检查；`generate_order()` 的 `entry_context` 新增 `strategy_lane`/`strategy_performance_eligible` 两个标记键，`strategy_performance_eligible` 在 `strategy_lane=="link_verification"` 时为 `False`（复用 `demo_audit.py` 的隔离标记惯例）。
2. `services/execution/bootstrap.py`：新增 `LINK_VERIFICATION_STRATEGY_KEY`/`LINK_VERIFICATION_RUNTIME_KEY` 常量、`LINK_VERIFICATION_RULES`（`entry_rules.link_verification_only=True`、`default_enabled_for_auto_trading=False`；`position_rules.notional_usdt=100`）、`bootstrap_link_verification_strategy()`（复用既有 `_ensure_auto_paper_run()`）。已核查**未**加入 `bootstrap_local_paper_runtime()` 的启动序列。
3. `apps/api/routers/runs.py`：新增 `POST /api/v1/execution/link-verification/bootstrap` 端点，调用 `bootstrap_link_verification_strategy()` 返回 `TaskSubmission`（`resource_type="paper_run"`）。
4. Review 聚合核查结论沿用之前的结论：当前 `ReviewService`/`build_daily_report` 不读 `OrderExecution`/`strategy_lane`，无策略表现聚合代码需要排除，故按用户此前确认的"仅做标记隔离"范围执行，未新增 Review 聚合代码。

**测试与验证：**

- `tests/services/test_paper_bootstrap.py`：新增 3 个测试（`bootstrap_link_verification_strategy` 创建隔离 PaperRun、幂等性、`bootstrap_local_paper_runtime` 不会自动创建该 PaperRun）。
- `tests/services/test_paper_signal.py`：新增 `TestLinkVerificationLane`（4 个测试：无信号数据也能产生固定 LONG 订单、`strategy_performance_eligible=False` 标记、仅靠 `entry_rules` 标记识别、`net_edge_rejection_codes()` 断言为空列表——直接验证不会被真实成本门禁拒绝）。
- `tests/api/test_paper_runtime_api.py`：新增端点级测试，断言 202 + `resource_type=paper_run` + 落库的 `execution_profile.strategy_lane=="link_verification"`。
- `ruff check`/`mypy` 通过；相关测试文件全部通过（26+8+46 用例），无回归。已用 Read 工具逐一核对新增代码逻辑落地。

### 模块3：组合相关性风控阈值可配置化 —— 状态：已完成

- [x] `shared/models/workflow.py:168-171` 四个字段（`correlation_peer_threshold`/`correlated_peer_count_limit`/`correlated_cluster_exposure_limit`/`net_directional_exposure_limit`）默认值与原文一致；`paper_signal.py:155-160,538-539` 从 `execution_profile` 读取；`gatekeeper.py:207-218` 消费四个阈值。核查确认与原文规格完全吻合。

### 模块4：Top20 数据完整性巡检 —— 状态：已完成

**与原文规格的偏差（已与用户确认，用户选择方案B）：**

原文规格假设只需统计 `portfolio_correlation_unavailable`/`technical_signals_insufficient`/`confirmation_unavailable_fail_closed` 三种拒绝原因在过去7天的占比即可。实际核查发现：`portfolio_correlation_unavailable` 产生于 `gatekeeper.py:213`（`_evaluate_numeric_risk()` 内部），该分支始终会调用 `submit_order()` 从而落库一条 `OrderExecution` 记录，因此可从 `ExecutionRepository.list_orders()` 直接统计。但 `technical_signals_insufficient`（`decision_pipeline.py:154`）与 `confirmation_unavailable_fail_closed`（`decision_pipeline.py:441`）走的是 `paper_runtime.py::run_cycle()` 的 `skip_no_trade_decision` 分支（约607-622行），该分支**从不**调用 `gatekeeper.submit_order()`，故不产生 `OrderExecution` 行；这两个原因此前唯一的落地位置是 `paper_metrics_summary["last_cycle_decisions"]`，但该字段每个 cycle 被整体重建覆盖（非追加），因此在此前代码状态下这两个拒绝原因**没有任何真正的历史持久化**，无法做7天统计。已就此向用户提出方案，用户选择方案B（推荐）：新增一张决策快照历史表，把每个 cycle 中带 `decision_trace` 的 `PaperRuntimeAction` 追加落库，作为可查询的历史依据。

**实际实现：**

1. `shared/models/decision_snapshot.py`（新建）：`DecisionSnapshot` Pydantic 契约，字段 `paper_run_id`/`symbol`/`action`/`pipeline_status`（从 `decision_trace["pipeline_status"]` 提取,便于索引查询）/`reason`/`decision_trace`（完整 JSON）/`cycle_time`/`created_at`。`shared/models/__init__.py` 导出。
2. `services/strategy_library/models.py`：新增 ORM 类 `DecisionSnapshot(Base)`，表名 `decision_snapshots`，`paper_run_id` 外键指向 `paper_runs.paper_run_id`，`symbol`/`pipeline_status`/`cycle_time` 均建索引以支撑按币种/拒绝原因/时间窗口过滤。
3. `migrations/versions/0008_decision_snapshots.py`（新建）：Alembic 迁移，`down_revision="0007"`，创建表+4个索引+外键约束。
4. `services/strategy_library/repository.py`：新增 `DecisionSnapshotRepository`（`create_snapshot()`/`list_snapshots(paper_run_id, symbol, pipeline_status, since, limit)`）与 `_decision_snapshot_from_orm()` 映射函数。`services/strategy_library/__init__.py` 导出。
5. `services/execution/paper_runtime.py`：`PaperRuntimeService.__init__` 内新增 `self.decision_snapshot_repo = DecisionSnapshotRepository(execution_repo.session)`（复用已注入的 `execution_repo.session`，**未改动构造函数签名**，避免触碰全部8个外部调用点）。在 `run_cycle()` 末尾、`self.paper_repo.update_paper_run(...)` 调用之前，新增写入循环：遍历本轮 `actions`，对每个 `action.decision_trace` 非空的动作追加一条 `DecisionSnapshot`（与既有 `last_cycle_decisions` 列表推导式用的过滤条件完全一致，保证是既有逻辑的持久化超集而非另起判断）。整个写入循环包裹在 `with suppress(Exception):` 内（复用既有 `strategy_repo.update_lifecycle_status` 的失败保护惯例），确保快照持久化失败不会打断实盘/模拟盘交易 cycle。
6. `scripts/audit_symbol_data_completeness.py`（新建）：遍历 `DEFAULT_BINANCE_TOP20`，调用 `data_repo.list_ohlcv_bars(symbol, timeframe="1h", limit=61)` 统计实际根数/最早最新时间/数据滞后小时数/异常缺口；调用 `ExecutionRepository.list_orders()`（客户端按 `created_at>=since` 过滤,7天窗口）统计 `portfolio_correlation_unavailable` 占比；调用新增的 `DecisionSnapshotRepository.list_snapshots(symbol=..., since=...)` 统计 `technical_signals_insufficient`/`confirmation_unavailable_fail_closed` 占比；输出 Markdown 表格到 stdout，`--output` 可选落盘。

**测试与验证：**

- `tests/repositories/test_decision_snapshot_repository.py`（新建，2 用例）：`create_snapshot`+`list_snapshots` 基本读写、按 `pipeline_status`/`since` 过滤。
- `tests/services/test_paper_runtime.py`（新增1用例 `test_runtime_persists_decision_snapshot_for_skip_no_trade_decision`）：monkeypatch `decision_pipeline.evaluate` 强制返回 `technical_signals_insufficient` 的 `skip_no_trade_decision`，断言 `run_cycle()` 后 `DecisionSnapshotRepository` 能查到对应快照且字段落地正确。全量 `test_paper_runtime.py`（18用例）+ `test_decision_snapshot_repository.py`（2用例）+ `test_strategy_repository.py`（回归）全部通过，无回归。
- 端到端脚本 smoke test：临时 sqlite（`create_relational_schema()`+`create_timeseries_schema()`）中造1条K线+1条 `DecisionSnapshot`（`technical_signals_insufficient`），运行 `scripts/audit_symbol_data_completeness.py --database-url ...`，确认 Markdown 报告正确渲染出 BTC/USDT 的 `technical_signals_insufficient` 占比100%，脚本 exit_code=0。测试脚本用后已删除。
- `ruff check` 通过（含一次自动修复的 `I001` 导入顺序问题）。已用 Read 工具逐一核对：`decision_snapshot.py`、`services/strategy_library/models.py`（`DecisionSnapshot` ORM 类）、`services/strategy_library/__init__.py`（导出）、`paper_runtime.py`（持久化写入循环）——预期逻辑均已落地。

### 模块5：真实边际统计流水线化 —— 状态：已完成

依赖模块0（已完成）。原文规格：把 `scripts/compute_signal_edge_stats.py::main()` 核心逻辑抽出为 `compute_and_write_edge_stats(strategy_key, days, min_trade_samples, max_age_days)`；`services/execution/tasks.py` 新增 `refresh_signal_edge_stats` Celery task；`apps/api/celery_app.py` 的 `beat_schedule` 新增每周日 04:00 的调度；结果发通知到 Notification Outbox。

**实际实现（与原文规格一致，无偏差）：**

1. `scripts/compute_signal_edge_stats.py`：新增 `EdgeStatsComputationResult` dataclass（`accepted`/`strategy_key`/`total_trades`/`win_rate`/`average_win`/`average_loss`/`min_trade_samples`/`artifact_path`/`evaluation_start`/`evaluation_end`）与 `compute_and_write_edge_stats(strategy_key, days=60, min_trade_samples=30, max_age_days=30, reuse_stored_data=False)` 函数，把原 `main()` 内的回放+样本量判断+写 `active.json` 核心逻辑完整迁出；不支持的 `strategy_key` 现在抛 `ValueError`（而非 `SystemExit`），交由调用方决定如何处理。`main()` 保留原有 CLI 行为（打印+`REJECTED`/`ACCEPTED`+对应 exit code），只是内部委托给新函数，对现有脚本调用方式（命令行参数、输出格式、退出码）零破坏。
2. `services/execution/tasks.py`：新增 `refresh_signal_edge_stats(*, strategy_key=None, days=60, min_trade_samples=30, max_age_days=30, reuse_stored_data=True)` Celery task（`queue="ops_queue"`，与 `refresh_volatility_asset_risk_tiers` 同队列同模式）。`reuse_stored_data` 默认 `True`——因为 OHLCV 已有独立的 `enqueue_binance_ingestion` 定时任务负责抓取，此任务不应在每周调度时再次直连 Binance。任务结果（accepted 或 rejected 两种情况）都会写入一条 `NotificationOutboxItem`（`event_type` 分别为 `signal_edge_stats_refreshed`/`signal_edge_stats_rejected`，`severity` 分别为 `low`/`medium`），复用既有 `NotificationRepository.create_notification()` 的按 `notification_id` 去重幂等模式（`notification_id` 含日期，同日重复运行不会产生重复通知）。
3. `apps/api/celery_app.py`：`task_routes` 新增 `services.execution.tasks.refresh_signal_edge_stats`→`ops_queue`（同时补上此前漏注册的 `refresh_volatility_asset_risk_tiers` 路由）；`beat_schedule` 新增 `signal-edge-stats-weekly`（`crontab(hour=4, minute=0, day_of_week="sun")`，即每周日 UTC 04:00）。

**测试与验证：**

- `tests/services/test_compute_signal_edge_stats.py`（新建，5 用例）：`TestComputeAndWriteEdgeStats`（样本量不足时拒绝且不写文件、样本量达标时正确写入 `active.json` 且字段与回放结果一致、不支持的 `strategy_key` 抛 `ValueError`）；`TestRefreshSignalEdgeStatsTask`（拒绝/接受两种结果都能在 `NotificationRepository` 里查到对应通知，`source_ref` 含策略键）。通过 monkeypatch `_load_or_backfill`/`_load_stored`（避免真实连 Binance）与 `TechnicalStrategyValidationService`（返回构造好的 `ReplayMetrics`）隔离网络/回放依赖，只测本模块新增的编排逻辑。
- `tests/services/test_celery_schedule.py`：新增断言 `schedule["signal-edge-stats-weekly"]["task"] == "services.execution.tasks.refresh_signal_edge_stats"`。
- 回归测试：`test_compute_signal_edge_stats.py`（5）+`test_celery_schedule.py`（2）+`test_signal_edge_stats.py`（5）+`test_edge_stats_for_gate.py`+`test_technical_strategy_validation.py`（7）+`test_paper_runtime.py`（18）+`test_decision_snapshot_repository.py`（2）合计42用例全部通过，无回归。
- `ruff check` 通过（修复了测试文件中未使用的 `timedelta`/`Decimal` 导入与 `assert False` → `pytest.raises` 的规范问题）。已用 Read 工具逐一核对：`scripts/compute_signal_edge_stats.py`（`compute_and_write_edge_stats`+`main()` 委托）、`services/execution/tasks.py`（`refresh_signal_edge_stats` 任务体+两种通知分支）、`apps/api/celery_app.py`（`task_routes`+`beat_schedule` 新增项）——预期逻辑均已落地。

### 模块6：缠论买卖点信号 —— 状态：阻塞（等待用户完成人工标注）

第一步（用户手工做，非Claude Code）：整理 20-30 段用户认可的一/二/三买卖点K线区间CSV（币种+时间范围+类型+K线时间戳）。**在用户交付此CSV前，不得开始第二步（`services/strategy_library/technical/chan_theory.py` 顶底分型+笔识别）。**

### 模块7：分批止盈 A/B 测试框架通用化 —— 状态：待实施

涉及文件：`services/validation/technical_replay.py`。原文规格：抽出通用函数 `compare_exit_policies(entry_config, exit_policy_a, exit_policy_b, symbols, date_range) -> ComparisonReport`；新增 `scripts/compare_exit_policies_cli.py`，输出格式对齐 `docs/audits/2026-07-12-exitladder-replay-comparison.md`。验收：用新工具重跑一次 ExitLadder vs 固定2R，数字必须与该审计文档历史记录一致（回归验证）。

### 模块8（P2，可选）：失败知识回流 —— 状态：待实施

涉及 `services/agents/service.py`（`scan_local_alpha`）、`shared/models/workflow.py`（`FailureRecord`）。原文规格：新增 `failure_type="negative_edge_confirmed"` 记录，`scan_local_alpha` 生成新 `StrategyIdea` 前先查是否已有相同 `enabled_signals` 签名对应的该记录。

### 模块9（P2，可选，排期最后）：Agent 执行器补齐 —— 状态：待实施

Coding/Backtest/Optimization/Risk 四个 Agent 目前零 executor。原文明确建议排在模块5、6 有实质进展之后再做。

### 执行顺序提示（原文，用户要求遵守）

不要一次性打包成一个大 PR；模块0做完先跑验收标准两个场景；确认后再做模块1+3（已完成）；用模块0工具确认新代码生效后再做模块2；模块4、5可在模块0-3稳定后并行推进；模块6第一步必须用户先做完；模块7/8/9不着急，等前面验收通过、有客观数字支撑后再排期。
