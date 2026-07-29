# Strategy Refactor Audit

- 审计时间：2026-07-29（Asia/Shanghai）
- 工程真源：当前工作目录内容；不以 `main`、远端分支或历史报告替代当前文件。
- 当前辅助 Git HEAD：`7351110595bc063f3db69afa1b5554cdb8de7d3a`
- 当前辅助分支名：`fix/v2-production-closure`
- `source_tree_hash`：`1d5ecf4b8f95b2e1a30d996527ae366c08270c18b7af86651cb021426294ffd2`
- `source_tree_hash` 口径：对当前目录下 `apps/`、`services/`、`shared/`、`scripts/`、
  `tests/`、`migrations/`、`infra/`、`frontend/`、`research_source/`、`.github/`
  的源码/配置/文档文件，以及根级工程配置文件，按规范化相对路径排序；逐文件计算
  SHA256，再对 `relative_path<TAB>file_sha256<LF>` 串计算 SHA256。排除 `.git`、
  缓存、依赖、构建输出、数据库、日志、审计产物和策略基线产物，避免自引用。
- 哈希文件数：681

## 当前测试基线

命令：

```text
py -3 -m pytest -q tests/services/test_automated_trading_decision_funnel.py tests/services/test_testnet_sampling_v2.py tests/services/test_automated_trading_ai_review.py tests/services/test_technical_strategy_validation.py tests/test_candidate_registry.py tests/services/test_regime_routing.py tests/services/test_exit_ladder.py tests/services/test_validation_admission.py tests/integration/test_v2_scheduler_entry_fact_chain.py
```

结果：

```text
87 passed in 27.77s
```

这只是策略/V2 相关回归基线，不代表真实 Testnet 自然开平仓闭环完成。

## 当前真实策略与执行调用链

当前 V2 Scheduler 路径是：

```text
services/execution/v2_scheduler_entry.py
  execute_v2_automated_trading_cycles()
  -> _load_v2_entry_timeframe(symbol, "15m")
  -> CycleRequest(entry_timeframe=...)
services/automated_trading/application/cycle_service.py
  run_automated_trading_cycle()
  -> DecisionContext(
       lane=TESTNET_SAMPLING,
       strategy_id="testnet_sampling_v2"
     )
services/automated_trading/application/decision_service.py
  evaluate_symbol()
  -> evaluate_sampling_signal()
  -> TradeCandidate
services/automated_trading/application/entry_service.py
  evaluate_entry()
  -> execute_entry()
services/automated_trading/application/protection_service.py
  build_protection_plan(real average_fill_price)
  -> ensure_protection()
services/automated_trading/application/reconciliation_service.py
  exchange-first reconciliation/recovery
```

已验证的关键事实：

- V2 Scheduler 常量 `V2_CYCLE_TIMEFRAME = "15m"`，每个标的只加载 200 根 15m
  已闭合 K 线；当前没有加载 1m、5m、1h、4h。
- `evaluate_sampling_signal()` 仍使用 EMA50、MACD histogram、RSI14、ATR14。
- V2 运行时硬编码 `testnet_sampling_v2`、`TESTNET_SAMPLING`，其结果
  `non_promotable=true`，不能作为策略晋级证据。
- 当前 V2 没有通过 legacy `DecisionPipeline -> TradeIntent`；本次重构必须接入
  V2 `TradeCandidate`，不得为满足旧 Prompt 文案而绕回冻结链路。

## 当前执行合同

`services/automated_trading/domain/candidates.py::TradeCandidate` 是冻结接缝：

```text
candidate_id / cycle_id
strategy_id / strategy_version
lane / candidate_type
symbol / side
signal_candle_close_time
signal_reference_price
confidence
stop_distance
take_profit_distance | None
max_entry_drift_bps
expires_at
non_promotable
signal_context
```

合同只携带相对风险距离。绝对止损/止盈必须在真实交易所成交后，通过
`resolve_protection_prices(average_fill_price)` 解析。V2 保护服务支持
`take_profit_distance=None`，因此新策略可以先只挂硬止损，再由现有 ReduceOnly
部分退出能力执行 TP 梯级。

## 当前活跃 Manifest 与策略状态

- legacy Active Manifest：
  `docs/evidence/active-manifests/auto_paper_mature_templates.json`
  指向 `trend_momentum_v1`，只允许 BTC/USDT、ETH/USDT。
- `services/execution/bootstrap.py::resolve_auto_paper_technical_evidence()` 在
  Manifest 缺失、损坏、规则哈希不一致或无可执行标的时返回
  `manifest_entry_enabled=false` 和空 eligible symbols，当前实现已经 fail-closed。
- V2 当前路径不读取上述 legacy Manifest；V2 的事实活跃候选仍是硬编码的
  `testnet_sampling_v2`。
- `services/strategy_library/candidates/registry.py` 当前注册：
  `operator_heuristic_v1`、`trend_momentum_v1`、`trend_breakout_v1`、
  `pandas_ta_broad_screen_v1`、`operator_heuristic_v2_relaxed`、
  `trend_pullback_v1`。它们尚未统一具备 `BASELINE_ONLY` /
  `execution_eligible=false` 元数据，这是后续 Task 4–6 的显式迁移项。

## 当前回测入口与统计口径

- 旧技术策略回放：
  `services/validation/technical_replay.py::TechnicalStrategyValidationService`。
- 现有脚本入口：
  `scripts/run_top20_technical_validation.py`、
  `scripts/run_candidate_competition.py`。
- 旧回放默认 `fixed_2r`，成交和成本口径只覆盖当前 legacy baseline 需要，
  不满足新策略最终现实模型。
- `services/validation/metrics.py::bootstrap_ci()` 是普通 IID percentile
  bootstrap，默认 1,000 次、90% 双侧区间；不能作为最终晋级 CI。
- `profit_factor()` 在有盈利但无亏损时返回 `9.99` 哨兵；后续统计任务必须改为
  `undefined` 并拒绝晋级。Golden Baseline 必须保留并明确标注当前旧口径，
  不得在 Task 1 美化历史结果。

## 当前 AI 调用与短路条件

- V2 有 Candidate 且 Entry Risk Gate 通过后，只有 `persist_facts=true` 才调用
  `cycle_service._run_trade_review()`。
- 无 Candidate 时记录 `NO_CANDIDATE` skip；Risk Gate 拒绝时记录
  `RISK_BLOCKED:<reason>` skip。
- 调用复用 `AgentTaskService + build_configured_llm_runtime()`，当前角色仅
  `review_agent/trade_review_llm`，输出是 advisory
  `bias/confidence/risk_flags/summary`。
- provider 不可用、超时或 schema 失败会记录失败；该 advisory 输出不改变
  V2 Entry，也不阻止 Exit。
- 尚无四角色 Committee、Proposal 排序、`[-0.15, 0.15]` 调整、配对 A/B
  晋级门槛。

## 当前固定 Gate 与退出

- legacy 候选仍普遍配置 `4h_direction_15m_entry`、1h state 和 15m entry；
  多周期 gate/ensemble 属于旧基准，不能继续修补为新内核。
- 当前 V2 sampling 是单一 15m 路径，不存在新的概率型 RegimeScore。
- V2 sampling 的止损距离为 `max(1.2 × ATR14, reference_price × 0.0035)`，
  TP 为固定 `1.5 × stop_distance`。
- legacy 技术回放默认固定 2R。
- V2 `exit_service` 已支持 `PARTIALLY_REDUCED`、真实剩余数量和 ReduceOnly，
  但当前 cycle 尚无策略独立 Adaptive Exit 编排。

## 与生产闭环任务冲突的当前文件

以下文件在本次审计时已有未提交修改，Task 0/1 不得编辑或纳入提交：

```text
apps/api/routers/automated_trading.py
services/automated_trading/application/cycle_service.py
services/automated_trading/application/fact_persistence.py
services/automated_trading/application/reconciliation_service.py
services/automated_trading/application/recovery_executor.py
services/automated_trading/infrastructure/binance_adapter.py
services/automated_trading/infrastructure/market_snapshot_provider.py
services/automated_trading/infrastructure/repository.py
services/execution/v2_scheduler_entry.py
tests/integration/test_v2_scheduler_entry_fact_chain.py
tests/services/test_automated_trading_binance_adapter.py
tests/services/test_automated_trading_cycle.py
tests/services/test_automated_trading_database_integrity.py
tests/services/test_automated_trading_fact_persistence.py
tests/services/test_automated_trading_reconciliation.py
tests/services/test_automated_trading_recovery.py
tests/services/test_paper_bootstrap.py
```

前端与全局验收审计目录也存在未提交/已暂存改动，本任务不触碰。

进入首次必须修改 `cycle_service.py` 或 `v2_scheduler_entry.py` 的阶段前，需重新
检查工作树；若这些闭环修改仍未 checkpoint，则策略重构停止在该边界。

## 变更范围

计划保留：

```text
services/data/**
services/automated_trading/domain/**
services/automated_trading/application/entry_service.py
services/automated_trading/application/protection_service.py
services/automated_trading/application/reconciliation_service.py
services/automated_trading/application/recovery_service.py
services/automated_trading/application/exit_service.py
services/automated_trading/infrastructure/**
services/execution/paper_*
前端
```

Task 0/1 计划新增：

```text
docs/audits/2026-07-29-strategy-refactor-audit.md
scripts/generate_strategy_golden_baseline.py
tests/scripts/test_generate_strategy_golden_baseline.py
artifacts/strategy_refactor/baseline/**
```

后续计划新增或扩展：

```text
services/strategy_library/context.py
services/strategy_library/regime/scorer_v2.py
services/strategy_library/candidates/failed_breakout_reversal_v1.py
services/strategy_library/candidates/trend_pullback_v2.py
services/strategy_library/candidates/range_sweep_reversion_v1.py
services/strategy_library/ensemble/selector_v2.py
services/strategy_library/exit/adaptive_exit.py
services/agents/market_committee.py
services/validation/dependent_bootstrap.py
services/validation/promotion_gate_v2.py
services/validation/trial_ledger.py
```

后续窄修改候选：

```text
services/strategy_library/candidates/registry.py
services/strategy_library/models.py
services/strategy_library/runner.py
services/agents/llm_runtime.py
services/agents/service.py
services/validation/technical_replay.py
services/validation/metrics.py
services/validation/walk_forward.py
services/automated_trading/application/decision_service.py
services/automated_trading/application/cycle_service.py
services/execution/v2_scheduler_entry.py
```

计划停用为历史基准：

```text
operator_heuristic_v1
trend_momentum_v1
trend_breakout_v1
pandas_ta_broad_screen_v1
operator_heuristic_v2_relaxed
trend_pullback_v1
legacy 1d/4h swing
```

目标状态统一为 `BASELINE_ONLY`、`execution_eligible=false`；不删除实现和历史
报告。`testnet_sampling_v2` 保留为 `non_promotable` 链路验证工具，进入
Forward 后对 BTC/ETH 禁用。

## Task 0 结论

Task 0 不改变运行行为。后续唯一允许的接入方向是：

```text
Point-in-time MarketContext
-> RegimeScore
-> StrategyProposal
-> deterministic Selector / TradePlan
-> V2 TradeCandidate
-> 现有 Entry / Protection / Reconciliation / ReduceOnly Exit
```

Golden Baseline 必须先证明数据覆盖并冻结 Holdout；数据不足时输出
`DATA_COVERAGE_INSUFFICIENT`，不得用参数调整或旧报告回填伪造覆盖。
