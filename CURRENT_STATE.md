# Current State

Last updated: 2026-07-20 Asia/Shanghai

## Authority

Current truth is resolved in this order: current code and tests, the active runtime database and scheduler state, this file, architecture/ADR documents, then archived incident material. Files under `docs/archive/` and `scripts/archive/` are historical evidence only and do not represent the current system state; AI collaborators must not treat their diagnostic conclusions as current facts.

## Runtime

- Environment: Paper / Binance Simulation only; mainnet remains disabled.
- Automatic execution universe is manifest-scoped: currently `BTC/USDT`, `ETH/USDT`.
- Offline technical research universe: Top10 liquid contracts (`BTC/USDT`, `ETH/USDT`, `SOL/USDT`, `XRP/USDT`, `BNB/USDT`, `DOGE/USDT`, `ADA/USDT`, `LINK/USDT`, `AVAX/USDT`, `TRX/USDT`). Research coverage never grants execution permission.
- Scheduled lanes: `auto_paper_mature_templates` and Binance-Simulation sampling lane `signal_observation_technical`.
- Directional lane: requires fresh symbol-scoped OOS evidence whose candidate and rules hash match runtime rules.
- Observation lane: uses real technical signals and may submit to Binance Simulation only after exact Top3 acceptance. It remains non-authoritative and excluded from strategy performance.
- Runtime readiness is based on the active execution scope (`BTC/USDT`, `ETH/USDT`, `SOL/USDT`), not legacy hard-coded Top20 counts.
- Current Top3 acceptance: run `da7edfd9-c1d4-4b04-8b66-02fe82e4af89`, 6/6 fills at 40x, BTC/ETH/SOL each received STOP_MARKET + TAKE_PROFIT_MARKET ReduceOnly refs, final 0 positions / 0 open orders. **Note: this run covered BTC/ETH/SOL (3 symbols); the current execution scope is BTC/ETH (2 symbols). `find_verified_testnet_acceptance` uses exact-match on `completed_symbols`, so this run does NOT satisfy the 2-symbol check. A new acceptance run must be triggered for exactly `["BTC/USDT","ETH/USDT"]` before execution_ready can be true.**
- Acceptance script now automatically cleans non-zero Testnet/Simulation state before the acceptance run via `services/execution/testnet_cleanup.py::testnet_account_cleanup` (Testnet-only guard; fail-closed on any error).
- Real sampling evidence: `signal_observation` produced BTC gateway order `22305428148` and SOL gateway order `3246292050` on the current build/scope. Both were market entries with 40x and native dual protection; the observation lane remains excluded from strategy performance.
- Reconciliation hardening: Binance Algo orders are included in acceptance/final state; transient missing positions must remain absent across two scheduler cycles before local close; exchange-only positions are recovered locally; missing Stop/TP is re-armed or fail-closed to ReduceOnly close; ReduceOnly `-2022` is only treated as flat after a fresh exchange-flat confirmation.
- LLM failures are advisory. Deterministic blocking risk events remain authoritative.

## Paper Risk

The operator-selected aggressive sampling profile remains active: 5% single-trade risk, 40x leverage ceiling, 35% symbol exposure, 90% total exposure, and 20% daily loss limit. It is forbidden for live trading and must be revalidated and tightened before any live phase.

## Evidence

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
- 依据报告：`20260718T071508Z.json`（运行时生成报告仍保留在本地 artifacts）
- OOS验证指标（基于manifest生成报告）：BTC OOS 36笔 Sharpe 3.02 PF 1.752 MaxDD 10.9%；ETH OOS 73笔 Sharpe 2.42 PF 1.375 MaxDD 19.4%

### 仓位参数（2026-07-18 高密度 Paper 档）

`services/execution/bootstrap.py` `AUTO_PAPER_TECHNICAL_RULES` `position_rules`：
- `risk_per_trade`: 0.05
- `max_leverage`: 40
- `max_position_fraction`: 0.35

执行范围固定为 `BTC/USDT`、`ETH/USDT`；组合初始风险上限 25%、组合敞口 90%、日损失上限 20%。`paper-btc-eth-sampling-v1` 是当前 Paper 采样配置。`BINANCE_AUTO_EXECUTE` 的代码与示例环境默认值均为 `false`；本次重构没有重新武装调度器。当前 blocker 至少包括配置漂移迁移尚未完成、调度心跳需重新验证，以及 BTC/ETH 精确范围验收未完成。旧验收记录 da7edfd9 覆盖 BTC/ETH/SOL，与当前 BTC/ETH 范围不匹配；重新验收属于外部状态变更，必须另行明确授权。

- Missing, stale, ineligible, or rules-mismatched evidence rejects the main lane with `validated_edge_stats_missing_or_stale`.
- Local candidate reports live under `artifacts/signal_edge_stats/`; active manifest is the committed, CI-verified exception.

## Supported Checks

```powershell
agent-python -m scripts.verify_runtime_config_sync --database-url sqlite:///.local_paper_console.db
agent-python -m scripts.audit_decision_funnel --database-url sqlite:///.local_paper_console.db --lookback-days 7
agent-python -m scripts.compute_signal_edge_stats --strategy-key auto_paper_mature_templates --database-url sqlite:///.local_paper_console.db --days 365
agent-python -m scripts.verify_config
```

Fresh verification on 2026-07-18: backend `474 passed, 2 skipped, 2 deselected`; targeted Ruff and Mypy clean; frontend `37 passed`; production build passed. Scheduler state now reports BTC/ETH execution coverage 2 while preserving SOL research coverage. Historical verification/reconciliation orders do not count as proof.
