# 进度日志

## 会话：2026-07-13

### Phase 1（第 1 项）：设置断链修复——杠杆滑块联动分档表

- **状态：** complete
- 执行的操作：
  - 通读 `shared/models/workflow.py`、`services/execution/paper_signal.py`、`services/execution/risk_tiers.py`、前端 `RuntimePanels.jsx`/`PaperConsole.jsx`，确认"杠杆滑块不生效"根因：`asset_risk_tiers` 因 Pydantic `default_factory` 永远非空，`_requested_leverage`/`_requested_notional` 无条件优先取用它而非 `position_rules.max_leverage`；前端从未真正编辑该字段，只是原样回传旧值。
  - 通过 AskUserQuestion 向用户确认修复方向，用户选择方案 B：滑块联动分档表。
  - `services/execution/risk_tiers.py` 新增 `TIER_SCALE_RATIOS` 与 `scale_asset_risk_tiers()`，按相对风险排序重新计算各档 `leverage`/`max_position_fraction`，保留动态 ATR% 分档的 symbol 分配。
  - `apps/api/routers/runs.py::update_paper_run_auto_settings` 接入该函数，替换掉客户端回传的陈旧分档表。
  - 前端 `RuntimePanels.jsx`：修正 `DEFAULT_AUTO_SETTINGS.asset_risk_tiers` 默认值与后端一致（core 10x→20x，standard 5x→10x）；用 `renderTierPreviewRows()` 动态预览替换失效的静态文案。
  - 新增/扩展测试：`tests/services/test_asset_risk_tiers.py`（+3 用例）、`tests/api/test_paper_runtime_api.py`（新增杠杆随滑块变化断言）。
- 创建/修改的文件：
  - `services/execution/risk_tiers.py`
  - `apps/api/routers/runs.py`
  - `frontend/admin/src/components/RuntimePanels.jsx`
  - `tests/services/test_asset_risk_tiers.py`
  - `tests/api/test_paper_runtime_api.py`

### Phase 1（第 2 项）：统一 remainder-trail 重复实现

- **状态：** complete
- 执行的操作：
  - 逐行核对 `services/execution/paper_runtime.py::_apply_trailing_ratchet`（通用 `ProtectiveLevels`）与 `services/validation/technical_replay.py::_advance_open_position`（手写 `ExitLadderState`）的阶梯止盈余量移动止损逻辑，确认数学完全等价。
  - 在 `services/execution/exit_ladder.py` 新增共享纯函数 `next_trailed_stop_price()`。
  - 重构 `paper_runtime.py::_apply_trailing_ratchet` 与 `technical_replay.py::_advance_open_position` 的对应分支，改为调用该共享函数，删除各自的手写 LONG/SHORT 分支。
  - 新增 `tests/services/test_exit_ladder.py` 中 5 个 `next_trailed_stop_price` 专项用例（长/短双向触发、未触发、不改善止损、初始距离为零）。
  - 用 Read 工具重新读取两处调用点的最终代码，确认改动落地且逻辑等价（满足交付自查规则）。
- 创建/修改的文件：
  - `services/execution/exit_ladder.py`
  - `services/execution/paper_runtime.py`
  - `services/validation/technical_replay.py`
  - `tests/services/test_exit_ladder.py`

## 测试结果

| 测试 | 输入 | 预期结果 | 实际结果 | 状态 |
|------|------|---------|---------|------|
| 资产风险分档测试 | `tests/services/test_asset_risk_tiers.py` | 分档缩放随滑块变化、动态分档保留 symbol、缺省回退 | 全部通过 | PASS |
| Paper 运行时 API 测试 | `tests/api/test_paper_runtime_api.py` | auto-settings 保存后 tier 杠杆随滑块联动 | 全部通过 | PASS |
| ExitLadder 单测 | `tests/services/test_exit_ladder.py` | 阶梯止盈、`next_trailed_stop_price` 长/短双向棘轮 | 全部通过 | PASS |
| 相关模块回归 | `test_paper_runtime.py`、`test_technical_strategy_validation.py` | 无行为回归 | 37 passed | PASS |
| Ruff | `services/execution/{risk_tiers,exit_ladder,paper_runtime}.py`、`apps/api/routers/runs.py`、`services/validation/technical_replay.py`、相关测试文件 | 全部通过 | All checks passed | PASS |
| Mypy | 同上文件集合 | 无类型错误 | Success | PASS |

## 错误日志

本轮暂无运行时错误或需要重试的失败用例。

## 五问重启检查（Phase 1 完成时点，历史记录保留）

| 问题 | 答案 |
|------|------|
| 我在哪里？ | Phase 1 两项子任务均已完成并通过测试/静态检查，正在同步项目记忆文件。 |
| 我要去哪里？ | 完成记忆文件同步后，进入 Phase 0（ExitLadder 回放对比）的方案制定，提交给用户确认后再实施。 |
| 目标是什么？ | 按用户 Phase 0-5 remediation 计划逐模块修复自动交易系统缺陷，每步验证、不确定必问。 |
| 我学到了什么？ | 见 `findings.md`。 |
| 我做了什么？ | 见上方记录。 |

## Phase 2/3：执行层默认行为核查 + 多数票信号融合核查（2026-07-13）

- **状态：** complete
- 执行的操作：
  - 用户在本次指令中追加两项要求：（a）杠杆/仓位设置整体适当激进化 + 修复 0.04% 仓位不按设置开单的 bug（已在此前会话完成，本轮通过 `bootstrap.py` 数值核查再确认：`risk_per_trade=0.025`、`max_portfolio_initial_risk_fraction=0.15`、`max_leverage=25`、`max_position_fraction=0.20`）；（b）开单判断纳入 MACD 金叉死叉、RSI 超买超卖、多周期 MA、FVG 补缺口等指标层面信号（已在此前会话完成，本轮通过重新读取 `tests/services/test_technical_signals.py`、`tests/services/test_signal_ensemble.py` 全量内容再确认）。
  - 按用户"继续完成所有任务，不用中断，自动执行"的指示，对 Phase 2（执行层默认行为修正）与 Phase 3（多周期信号密度改造）逐项核查代码现状。
  - Phase 2 第 1 项：Grep+Read `shared/config.py`、`services/execution/paper_signal.py`、`services/execution/gateway.py`，确认"默认限价单+市价单兜底"已实现，无需改动。
  - Phase 2 第 2 项：Read `services/execution/paper_runtime.py` 600-840 行，确认保护性止损止盈优先于反向信号平仓的顺序已正确；核对 `tests/services/test_paper_runtime.py` 中既有回归测试 `test_runtime_stoploss_wins_over_opposite_signal_hit_on_same_bar` 已覆盖该行为。
  - Phase 2 第 3/4 项：Read `.github/agent/memory/decisions-log.md` 第 1-9 行，确认 ADR-023→ADR-058 重新编号与 15% 数值修正在此前会话中已落地且保持完整。
  - Phase 3：核查 `services/strategy_library/ensemble.py::layered_regime_entry` 与 `tests/services/test_signal_ensemble.py`，确认多数票+quorum fail-closed 机制已取代旧的全票 AND-gate。
  - 运行全量测试与静态检查：`pytest -q` 320 passed / 1 skipped；ruff、mypy 全部通过。
  - 同步更新 `task_plan.md`（当前阶段、遇到的错误、Phase 2/3 checklist 全部标记完成）、`findings.md`（新增 Phase 2/3 研究发现小节）、本文件（本条目）、`.github/agent/memory/project-memory.md`（新增顶部条目 + 修正"5%→15%"历史遗留错误数值）。
- 创建/修改的文件：
  - `task_plan.md`
  - `findings.md`
  - `progress.md`
  - `.github/agent/memory/project-memory.md`

## Phase 4：验收/搬砖订单来源隔离核查 + ExitLadder 端到端测试（2026-07-13）

- **状态：** complete
- 执行的操作：
  - 第 1 项（订单来源隔离核查）：逐行读取 `services/execution/gateway.py`（325-524 行全部方法）与 `services/execution/carry_execution.py`（全文 234 行），并对 `execution_repo|ExecutionRepository|OrderExecution` 做全文 grep，确认资金费率搬砖两腿从未产生 `OrderExecution` 行——搬砖结果只落在 `AgentTaskRepository.update_task(output_payload=...)`，绩效统计管道读不到它，天然隔离。结合此前已确认的 `demo_audit.py` 显式标签隔离机制（验收探测单），判定**两条隔离路径均已到位，本项无需改代码**。
  - 第 2 项（ExitLadder 端到端测试）：核查确认此前只有服务层直接注入测试与回测重放测试覆盖阶梯止盈逻辑，没有真正走 HTTP `/auto-cycle` + 真实决策管线的端到端测试。设计并新增 `tests/api/test_paper_runtime_api.py::test_paper_runtime_auto_cycle_partial_closes_via_exit_ladder`：给策略配置确定性 `stoploss_rules={"fixed_bps": 200}` 使止损距离可提前算出，两轮 `/auto-cycle` 调用（建仓→送入命中 L1 触发价的 1 分钟保护K线→部分平仓），断言 `action=="exit_ladder_partial_long"` 且剩余仓位未清零。为支持该测试，扩展 `_create_validated_paper_run()` 增加可选 `strategy_key`/`stoploss_rules`/`takeprofit_rules` 参数（默认值不变，不影响既有测试）。
  - 验证：`pytest tests/api/test_paper_runtime_api.py -q` → 7 passed（6 项既有 + 1 项新增）；全量 `pytest -q` → 321 passed, 1 skipped；`ruff check tests/api/test_paper_runtime_api.py` → 全部通过。按交付自查规则，用 Read 工具重新读取了新增测试函数的完整落地代码，逐行核对与设计意图一致后才在此记录完成。
  - 同步更新 `task_plan.md`（Phase 4 checklist 全部勾选，状态改为 complete）、`findings.md`（新增"研究发现（Phase 4）"与对应技术决策/资源小节）、本文件（本条目）；`.github/agent/memory/task-history.md` 待随后一并更新。
- 创建/修改的文件：
  - `tests/api/test_paper_runtime_api.py`（新增测试函数 + 扩展 fixture 参数）
  - `task_plan.md`
  - `findings.md`
  - `progress.md`

## Phase 5：跨品种相关性组合风控核查（2026-07-13）

- **状态：** complete（核查性任务，确认已实现，无代码改动）
- 执行的操作：
  - 全文读取 `shared/models/risk.py`（103行），确认 `RiskProfile` 本身无相关性字段（`max_symbol_exposure`为单品种上限，`max_total_exposure`为组合总上限，均不考虑品种间相关性）。
  - 全文 grep `correlation` 关键词跨 `services/`、`shared/`、`apps/`，发现 `services/execution/portfolio_risk.py`（`correlation`/`close_returns`/`signed_exposure`）、`services/execution/paper_signal.py::_build_risk_state`（391-486行，实际相关性计算与折价调用点）、`services/execution/gatekeeper.py::_evaluate_numeric_risk`（203-213行，四条相关性相关拒绝规则）均已完整实现。
  - 核查 `tests/services/test_execution_gatekeeper.py` 四条既有测试（双对手强拒绝、簇敞口+净方向敞口双拒绝、数据不足fail-closed区分新开仓/平仓、60根窗口边界），确认关键路径与边界均被锁定。
  - 运行 `pytest tests/services/test_execution_gatekeeper.py tests/services/test_paper_runtime.py tests/services/test_signal_ensemble.py -q` → 43 passed。
  - 追溯 `.github/agent/memory/task-history.md` 的 `[TASK-048]` 条目，确认该实现完成于本轮任务之前——判定 Phase 5 是核查性任务，用户报告中"没有考虑相关性"与代码现状不符，**本项无需新代码**。
  - 同步更新 `task_plan.md`（Phase 5 checklist 勾选，状态改为 complete）、`findings.md`（新增"研究发现（Phase 5）"及对应技术决策/资源小节）、本文件（本条目）；随后一并更新 `.github/agent/memory/task-history.md`。
  - 同时修正此前 Phase 4 条目在 `task_plan.md`/本文件/`.github/agent/memory/task-history.md` 中的记录笔误："8 passed"更正为"7 passed"（实际 `pytest tests/api/test_paper_runtime_api.py -q` 输出为 `7 passed, 1 warning`，6项既有+1项新增，此前记录有误，全量套件"321 passed, 1 skipped"的结论不受影响）。
- 创建/修改的文件：
  - `task_plan.md`（Phase 4 笔误修正 + Phase 5 核查结论）
  - `findings.md`（新增"研究发现/技术决策/资源（Phase 5）"）
  - `progress.md`（本条目 + Phase 4 笔误修正）
  - `.github/agent/memory/task-history.md`（Phase 4 笔误修正，Phase 5 条目待新增）

## 五问重启检查（Phase 0-5 remediation 完成时点，历史记录保留）

| 问题 | 答案 |
|------|------|
| 我在哪里？ | Phase 0/1/2/3/4/5 全部完成并已通过 Read 复核验证；项目跟踪文件（`task_plan.md`/`findings.md`/`progress.md`）已同步 Phase 5 结论，`.github/agent/memory/task-history.md` 待新增 Phase 5 条目。 |
| 我要去哪里？ | 按用户"不用中断，下一个phase以上一个phase的结果制定方案，自动执行"的指示，更新完 `.github/agent/memory/task-history.md` 后核查是否还有未覆盖的用户原始报告条目（MACD/RSI/MA/FVG等指标已在Phase 0-3确认落地；杠杆仓位已在Thread A修复；若五个Phase均已闭环，则本轮remediation任务整体完成，向用户汇报总结）。 |
| 目标是什么？ | 按用户 Phase 0-5 remediation 计划逐模块修复自动交易系统缺陷，每步验证、不确定必问；Phase 5 结论有完整代码追踪+既有测试实证支撑，无新增不确定点。 |
| 我学到了什么？ | 见 `findings.md` 新增"研究发现（Phase 5）"小节：跨品种相关性组合风控（含fail-closed设计、双对手强拒绝、簇敞口+净方向敞口双维度限制）已在更早的`TASK-048`中完整实现，本轮只需核查+归档，不应重复实现造成语义冲突。 |
| 我做了什么？ | 见上方"Phase 5"条目记录，以及本次对 Phase 4 "8 passed"笔误的三处修正。 |

---

## 会话：2026-07-13（续）—— 第零节：LLM 决策否决链路诊断与修复

> 与上方 Phase 0-5（自动开平仓系统缺陷修复）是不同的独立任务线，见 `task_plan.md`/`findings.md` 对应新增小节。

### 执行的操作

- 核实本地环境，写入用户提供的真实 OpenRouter/GitHub Models key 到 `.env`（用户明确授权硬编码，不再重复索要）。
- 实施第零节 5 点代码修复：`shared/config.py` 环境变量别名兼容、`llm_factory.py` provider 日志、`service.py::_execute_llm_veto` 三支异常拆分、`GET /agents/llm-status` 诊断接口、首次失败通知（复核确认此前已落地）。
- 顺带修复 `bootstrap.py::_ensure_auto_paper_run` 的 `preserved_keys` 缺 `llm_veto_enabled` 导致的重启重置 bug（用户批准"顺带修掉"）。
- 用真实 key 跑 `build_configured_llm_runtime()` 端到端连通性验证，发现并修复 `_OPENROUTER_SEED_MODELS` 两个免费模型已被 OpenRouter 下架（404）的问题，替换为存活模型并连续验证 3 次成功。
- 诊断 GitHub Models 401/403 为 token 权限范围问题（非代码缺陷），用 AskUserQuestion 询问用户后，用户选择"先跳过,只用OpenRouter验证 (推荐)"。
- 新增 opt-in 集成测试 `tests/integration/test_llm_decision_veto_live.py`，用 SQLite harness（`tests/conftest.py::db_session`）+ 真实 OpenRouter 免费模型，走通 `AgentTaskService.submit_task()` → `_execute_llm_veto()` → `AgentTaskRepository` 落库全链路。
- 运行全量测试与静态检查：`pytest -q` 321 passed, 2 skipped；`ruff check`、`mypy` 全部通过。
- 修复 `shared/models/__init__.py` 因导入顺序错误触发的 `ruff I001`（`LLMProviderStatus, LLMStatusResponse` 重新排到正确字母序位置）。
- 同步更新 `task_plan.md`、`findings.md`、本文件、`.github/agent/memory/*` 三个文件。

### 创建/修改的文件

- `shared/config.py`
- `services/agents/llm_factory.py`
- `services/agents/service.py`
- `apps/api/routers/agents.py`
- `services/execution/bootstrap.py`
- `shared/models/__init__.py`（导入顺序修复）
- `tests/integration/test_llm_decision_veto_live.py`（新增）
- `task_plan.md` / `findings.md` / `progress.md`
- `.github/agent/memory/project-memory.md` / `task-history.md` / `decisions-log.md`

### 测试结果

| 测试 | 输入 | 预期结果 | 实际结果 | 状态 |
|------|------|---------|---------|------|
| LLM 否决链路端到端集成测试 | `RUN_LLM_VETO_INTEGRATION=1 pytest tests/integration/test_llm_decision_veto_live.py -v` | 真实 OpenRouter 调用 + agent_tasks 落库成功 | PASSED（14.60s） | PASS |
| 全量单测 | `pytest -q` | 无回归 | 321 passed, 2 skipped | PASS |
| Ruff | 本节全部改动文件 | 全部通过 | All checks passed | PASS |
| Mypy | 本节全部改动文件 | 无类型错误 | Success | PASS |

### 错误日志（第零节）

- OpenRouter 两个种子模型 404（已下架）→ 查询实时目录后替换为存活模型，修复。
- 一次瞬时 `RemoteProtocolError` 被误判为 JSON 解析失败 → DEBUG 日志复核确认为网络瞬断，重跑 3 次全部成功，非代码问题。
- GitHub Models 401/403 → 诊断为 token 权限范围缺失（非代码问题），按用户选择跳过，仅用 OpenRouter 验证。
- `shared/models/__init__.py` 导入顺序触发 `ruff I001` → 重新排序后通过。

### 五问重启检查（第零节完成时点）

| 问题 | 答案 |
|------|------|
| 我在哪里？ | 第零节 5 点代码修复 + `bootstrap.py` 顺带修复 + OpenRouter 种子模型修复全部完成，端到端集成测试通过，全量测试/静态检查无回归；正在完成记忆文件同步的最后一步（Read 复核）。 |
| 我要去哪里？ | 完成本次记忆文件 Read 复核后，向用户汇报第零节完成情况。一~五节在用户明确要求前不启动。 |
| 目标是什么？ | 诊断并修复 LLM 决策否决链路静默失效问题，用免费额度端到端验证修复生效——已达成（GitHub Models 部分按用户选择跳过，OpenRouter 已完整验证）。 |
| 我学到了什么？ | 见 `findings.md` 新增"研究发现（第零节）"小节：静默失效有两个独立根因（provider 未配置零日志 + OpenRouter 种子模型下架），且 GitHub Models 的失败是账号侧 token 权限问题而非代码缺陷。 |
| 我做了什么？ | 见上方"执行的操作"记录。 |
