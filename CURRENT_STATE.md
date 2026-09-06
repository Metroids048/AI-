# Current State

Last updated: 2026-08-19 Asia/Shanghai

<!-- BEGIN GENERATED: canonical-strategy-manifest -->
## Canonical Strategy Runtime Truth

- Strategy: `trend_momentum_v2_enriched` / `2.0.0`
- Rules Hash: `1da72b9aaabb3b412f8c0cbbfb6c8658df1a0c773f0b80e7c39e6f92d24c5e78`
- Strategy code hash: `cec7361be36455812e1cf50738ad0cc0b543589ecf4e364543e7ac23da5e70e9`
- Strategy package hash: `e22587d69e8e935eff21fd01e5f8633a336f2c9d07d6b464696c523c495a70bb`
- Strategy source commit (provenance): `470d3d3d1c43e775bfd05062b777d3d26a2bfb15`
- Configured execution scope: BTC/USDT, ETH/USDT
- Eligible execution symbols: none
- Research scope: BTC/USDT, ETH/USDT, SOL/USDT, XRP/USDT, BNB/USDT
- Authorization: `PENDING`
- Validation conclusion: `NO_VALIDATED_EDGE`

This block is generated from the canonical strategy manifest; do not edit it by hand.
<!-- END GENERATED: canonical-strategy-manifest -->

## Final Closeout Baseline (current)

- Engineering platform: `PASS` for deterministic V2 execution, reconciliation,
  protection/recovery, V2 funnel, forensics, and desktop startup contracts.
- Strategy research: `NO_VALIDATED_EDGE`. No strategy has sufficient evidence
  for Production promotion; this is a research conclusion, not a runtime fault.
- New automatic entry: `INTENTIONALLY_PAUSED` with
  `entry_authority=NONE`, `entry_authorized=false`, and
  `NO_AUTHORIZED_PRODUCTION_STRATEGY`.
- Existing managed exposure remains eligible for protection, reconciliation and
  reduce-only exit. At this closeout snapshot there is no open managed position.
- Mainnet is disabled. A future strategy effort requires a new independent
  research cycle; this repository's sealed Development evidence must not be
  optimized further.

## Authority

Current truth is resolved in this order: current code and tests, the active runtime database and scheduler state, this file, architecture/ADR documents, then archived incident material. Files under `docs/archive/` and `scripts/archive/` are historical evidence only and do not represent the current system state; AI collaborators must not treat their diagnostic conclusions as current facts.

## Runtime

- **Execution authority:** Binance USDT-M Testnet / Binance Simulation is the authoritative execution source for the automated directional lane. SQLite/Paper records are a post-execution projection and audit/recovery cache.
- Automatic execution universe: exactly `BTC/USDT`, `ETH/USDT`. Research universes do not grant execution permission.
- Production desktop path: `一键启动.cmd` -> `launch-paper-console.ps1` -> API + independent `RuntimeScheduler` -> `automated_trading_v2_cycle`.
- The V2 execution chain is the only automatic writer. Legacy writers are disabled. `一键启动.cmd` explicitly arms the Testnet Canary; direct PowerShell invocation keeps its default mode unless `-EnableNaturalTestnet` is supplied.
- The generated Canonical Strategy block above is the only current strategy/scope/authorization claim in this document. All older strategy performance narratives below are historical evidence, not runtime authority.
- Directional lane: `auto_paper_mature_templates`. When the safe Testnet settings, exact-scope acceptance, OOS/config evidence, Gatekeeper, and runtime readiness checks pass, its mode is `binance_simulation_first`.
- Exchange-first order lifecycle: strategy/Gatekeeper authorization -> Binance submit/ack/fill -> local order and position projection using exchange average fill price and filled quantity. Local `accepted` is not a fill.
- Exchange-first close lifecycle: Binance ReduceOnly close/ack/fill -> local position reduction/closure and realized-PnL projection using the exchange exit fill.
- Local-only Paper execution is reserved for tests, mocks, deterministic replay, and explicitly local research lanes. It is not proof that 7x24 exchange automation is working.
- Manual or exchange-only positions remain unmanaged until explicitly adopted and cannot inherit historical strategy protection state.
- Acceptance/canary orders are tagged and excluded from strategy performance.
- Current runtime state must always be rechecked on the device actually running the service. Historic Testnet acceptance is engineering evidence only, not current strategy authorization.
- Mainnet remains disabled.

## Config Snapshot System (2026-07-21)

- Runtime configuration is now persisted as immutable `ConfigSnapshot` records keyed by `paper_run_id`.
- Bootstrap for the directional lane (`auto_paper_mature_templates`) preserves Testnet authorization across restarts (`preserve_testnet_authorization=True`).
- Observation lane (`signal_observation_technical`) is forced `paper_only=True`; any previously-set simulation authorization is cleared on bootstrap.
- `scripts/migrate_runtime_config_snapshot.py` — stage current evidenced rules as an immutable snapshot without mutating the Strategy row.
- `scripts/arm_validated_testnet_execution.py` — arm the OOS-validated directional run from an existing exact-scope Testnet acceptance proof.
- `scripts/publish_active_edge_evidence.py` — copy local OOS edge-stats pointers to `docs/evidence/active-edge-stats/` for CI-checkable committed evidence.
- Active edge-stats evidence committed at `docs/evidence/active-edge-stats/auto_paper_mature_templates/trend_momentum_v1/`.

## Binance Simulation Risk

The operator-selected aggressive sampling profile remains active: 5% single-trade risk, 40x leverage ceiling, 35% symbol exposure, 90% total exposure, and 20% daily loss limit. It is forbidden for live trading and must be revalidated and tightened before any live phase.

## Historical Evidence (superseded for runtime and authorization)

The material below is retained for audit only. It must not be read as a current
claim that a strategy is approved, that Canary is trading, or that new entry is
authorized. The Final Closeout Baseline and generated manifest block above are
authoritative for current strategy state.

Last updated: 2026-07-19

### Sharpe 计算修正（2026-07-18）

`services/validation/technical_replay.py` 原先用 M15 K线频率（35,040根/年）作为 `periods_per_year`，但 `net_returns` 是逐笔交易收益，导致 Sharpe 被放大约9-10倍（修正前BTC/ETH显示33-40，SOL显示-38到-40）。已修正为基于实际交易频率的年化因子。修正后数值在合理范围（BTC 2.0-3.0，ETH 2.4-2.6，SOL -4.1到-3.8），不影响 min_sharpe=1.0 的通过/失败判定结论。

### 五候选竞赛报告（2026-07-19，含 Bootstrap CI）

报告：`docs/audits/2026-07-19-five-candidate-competition.json`，status=completed，365天窗口70/30时序切分，5候选×10币种，CI列：90% bootstrap置信区间（1000次重采样，百分位法）。

**通过全部门槛的候选（无failed_reasons）：**

| 候选 | 币种 | OOS笔数 | 胜率 | 净期望 | 净期望CI90% | Sharpe | Sharpe CI90% | PF | 最大回撤 |
|---|---|---|---|---|---|---|---|---|---|
| trend_momentum_v1 | BTC/USDT | 35 | 45.7% | +0.006984 | [-0.002629, 0.017698] | 2.1054 | [-0.8355, 5.3801] | 1.4910 | 18.0% |
| trend_breakout_v1 | BTC/USDT | 35 | 42.9% | +0.006657 | [-0.004057, 0.017371] | 2.0252 | [-1.3458, 5.3149] | 1.4669 | 14.6% |
| trend_momentum_v1 | ETH/USDT | 65 | 44.6% | +0.006569 | [-0.000815, 0.013953] | 2.6352 | [-0.3403, 5.6087] | 1.4527 | 16.5% |

**CI解读（重要）：** OOS样本量（35-65笔）较小，所有90%置信区间均包含0（负值端），说明单次历史窗口不足以精确估计长期期望；结论应理解为"中位点估计为正，但区间宽"，不应过度自信。积累更多样本后需重新评估。

**SOL排除原因（与上期一致）：** 全部候选在SOL上均显示78-87%最大回撤、负期望，推断SOL走势对所有趋势跟随策略系统性不利。本轮不开SOL。

**新候选结论：** `pandas_ta_broad_screen_v1` 在所有币种上仅产生1-4笔信号，不具可用性。`operator_heuristic_v1/v2_relaxed` 在BTC上OOS笔数28/29笔，仅差1-2笔未过30笔门槛，可作为下一轮候选跟踪。与上期结论一致（样本窗口自然推进±1天）。

### trend_momentum_v1 MAE/MFE 诊断（2026-07-18）

报告：`docs/audits/2026-07-18-trend_momentum_v1-mae_mfe.json` 与逐笔明细 CSV。BTC/ETH 完整可用历史共 716 笔：胜单 MFE 中位数/P75/P90 均为 2R；止盈交易 186 笔，其中 45 笔（24.2%）MFE 超过 2R，中位数仍为 2R；亏单 MAE 中位数为 -1R。`trend_momentum_v2_trailing` 与 `trend_momentum_v2_early_entry` 的预设门控均未通过，因此未新增候选、未修改 `trend_momentum_v1` 或 active manifest。

### 当前活跃 Manifest

- 路径：`docs/evidence/active-manifests/auto_paper_mature_templates.json`
- 选中候选：`trend_momentum_v1`
- 规则哈希：`41a4c796502b5d6d2a739714bc945d455882acd0e7ab97c21a8d00c2938124b2`
- 可交易范围：`BTC/USDT`、`ETH/USDT`（SOL/USDT不在eligible_symbols中）
- 依据报告：`20260721T025226Z.json`（运行时生成报告保留在本地 artifacts；提交的 CI 可核验证据见 `docs/evidence/active-edge-stats/auto_paper_mature_templates/trend_momentum_v1/`）
- OOS验证指标（基于manifest生成报告）：BTC OOS 36笔 Sharpe 3.10 PF 1.779 MaxDD 10.9%；ETH OOS 74笔 Sharpe 2.40 PF 1.376 MaxDD 19.4%

### 仓位参数（2026-07-18 高密度 Paper 档）

`services/execution/bootstrap.py` `AUTO_PAPER_TECHNICAL_RULES` `position_rules`：
- `risk_per_trade`: 0.05
- `max_leverage`: 40
- `max_position_fraction`: 0.35

执行范围固定为 `BTC/USDT`、`ETH/USDT`；组合初始风险上限 25%、组合敞口 90%、日损失上限 20%。`paper-btc-eth-sampling-v1` 是当前 Paper 采样配置。自动 BTC/ETH 方向通道在安全 Testnet 条件满足时默认采用 `binance_simulation_first`；示例配置启用 `BINANCE_AUTO_EXECUTE=true`，同时保持 `BINANCE_USE_TESTNET=true` 与 `LIVE_TRADING_ENABLED=false`。实际是否就绪以运行设备上的 `scripts.check_execution_blockers` 和 `/api/runs/trading-status` 为准。

- Missing, stale, ineligible, or rules-mismatched evidence rejects the main lane with `validated_edge_stats_missing_or_stale`.
- Local candidate reports live under `artifacts/signal_edge_stats/`; active manifest is the committed, CI-verified exception.

## Supported Checks

```powershell
agent-python -m scripts.verify_runtime_config_sync --database-url sqlite:///.local_paper_console.db
agent-python -m scripts.audit_decision_funnel --database-url sqlite:///.local_paper_console.db --lookback-days 7
agent-python -m scripts.compute_signal_edge_stats --strategy-key auto_paper_mature_templates --database-url sqlite:///.local_paper_console.db --days 365
agent-python -m scripts.verify_config
```

<!-- BEGIN GENERATED: pytest-verification -->
Backend pytest (pytest -q -m not integration) as of 2026-09-06 03:42 UTC: 2076 passed, 0 failed, 0 error, 16 skipped -- green.

This block is generated by `scripts/refresh_current_state.py` from a real run. Do not edit it by hand; a hand-typed count is a claim, not evidence.
<!-- END GENERATED: pytest-verification -->

Targeted Ruff and Mypy clean; frontend `37 passed`; production build passed. Scheduler state now reports BTC/ETH execution coverage 2 while preserving SOL research coverage. Historical verification/reconciliation orders do not count as proof.

## 2026-07-24 Directional Throughput Fix

- The exchange-first BTC/ETH lane remains armed by exact-scope Testnet acceptance; local SQLite is post-fill projection only.
- Historical runtime evidence showed the dominant no-order causes occurred before Gatekeeper: `technical_signals_insufficient` and strict `multi_timeframe_disagreement`.
- The validated primary `trend_momentum_v1` remains first choice. In safe Binance Testnet only, primary starvation may invoke the existing `operator_heuristic_v2_relaxed` sampling fallback, requiring the 15m entry direction plus at least one matching 1h/4h direction.
- Sampling fallback decisions are tagged `decision_variant=simulation_sampling_fallback` and `testnet_sampling_mode=true`; they retain current BTC/ETH fixed sizing, leverage, stops, targets and account-risk gates. They never run on mainnet or local-only Paper.
- Bootstrap stages the packaged active manifest into ConfigSnapshot so stale database rules cannot silently override deployed strategy rules.
- Offline verification command: `py -3 -m scripts.verify_directional_exchange_first`. It proves the real decision/orchestration path reaches a strict fake Binance fill and projects the confirmed exchange price/quantity. It does not claim real Binance connectivity.
