# Current State

Last updated: 2026-07-17 20:20 Asia/Shanghai

## Authority

Current truth is resolved in this order: current code and tests, the active runtime database and scheduler state, this file, architecture/ADR documents, then archived incident material. Files under `docs/archive/` and `scripts/archive/` are historical evidence only.

## Runtime

- Environment: Paper / Binance Simulation only; mainnet remains disabled.
- Automatic research universe: `BTC/USDT`, `ETH/USDT`, `SOL/USDT`.
- Scheduled lanes: `auto_paper_mature_templates` and Binance-Simulation sampling lane `signal_observation_technical`.
- Directional lane: requires fresh symbol-scoped OOS evidence whose candidate and rules hash match runtime rules.
- Observation lane: uses real technical signals and may submit to Binance Simulation only after exact Top3 acceptance. It remains non-authoritative and excluded from strategy performance.
- Runtime readiness is based on the active execution scope (`BTC/USDT`, `ETH/USDT`, `SOL/USDT`), not legacy hard-coded Top20 counts.
- Current Top3 acceptance: run `da7edfd9-c1d4-4b04-8b66-02fe82e4af89`, 6/6 fills at 40x, BTC/ETH/SOL each received STOP_MARKET + TAKE_PROFIT_MARKET ReduceOnly refs, final 0 positions / 0 open orders. `execution_ready=true`.
- Real sampling evidence: `signal_observation` produced BTC gateway order `22305428148` and SOL gateway order `3246292050` on the current build/scope. Both were market entries with 40x and native dual protection; the observation lane remains excluded from strategy performance.
- Reconciliation hardening: Binance Algo orders are included in acceptance/final state; transient missing positions must remain absent across two scheduler cycles before local close; exchange-only positions are recovered locally; missing Stop/TP is re-armed or fail-closed to ReduceOnly close; ReduceOnly `-2022` is only treated as flat after a fresh exchange-flat confirmation.
- LLM failures are advisory. Deterministic blocking risk events remain authoritative.

## Paper Risk

The operator-selected aggressive sampling profile remains active: 5% single-trade risk, 40x leverage ceiling, 35% symbol exposure, 90% total exposure, and 20% daily loss limit. It is forbidden for live trading and must be revalidated and tightened before any live phase.

## Evidence

Last updated: 2026-07-18

### Sharpe 计算修正（2026-07-18）

`services/validation/technical_replay.py` 原先用 M15 K线频率（35,040根/年）作为 `periods_per_year`，但 `net_returns` 是逐笔交易收益，导致 Sharpe 被放大约9-10倍（修正前BTC/ETH显示33-40，SOL显示-38到-40）。已修正为基于实际交易频率的年化因子。修正后数值在合理范围（BTC 2.0-3.0，ETH 2.4-2.6，SOL -4.1到-3.8），不影响 min_sharpe=1.0 的通过/失败判定结论。

### 五候选竞赛报告（2026-07-18，修正 Sharpe 后）

报告：`docs/audits/2026-07-18-five-candidate-competition.json`，status=completed，评估区间 2026-03-30 ~ 2026-07-18（~110天），5候选×3币种=15行，70/30 时间顺序拆分。

**通过全部门槛的候选（无failed_reasons）：**

| 候选 | 币种 | OOS笔数 | 胜率 | 净期望 | Sharpe | PF | 最大回撤 |
|---|---|---|---|---|---|---|---|
| trend_momentum_v1 | BTC/USDT | 35 | 42.9% | +0.00664 | 2.01 | 1.465 | 18.0% |
| trend_momentum_v1 | ETH/USDT | 64 | 43.8% | +0.00661 | 2.65 | 1.449 | 16.5% |
| trend_breakout_v1 | BTC/USDT | 34 | 44.1% | +0.00719 | 2.15 | 1.501 | 13.8% |

**SOL排除原因：** 全部5个候选在SOL上均显示78-87%最大回撤、负期望，推断110天内SOL走势对所有趋势跟随策略系统性不利。本轮不开SOL，不为SOL放宽任何阈值。

**新候选结论：** `pandas_ta_broad_screen_v1` 在所有币种上仅产生1-4笔信号，不具可用性。`operator_heuristic_v1/v2_relaxed` 在BTC上OOS笔数27/28笔，仅差2-3笔未过30笔门槛，可作为下一轮候选跟踪。

### 当前活跃 Manifest

- 路径：`artifacts/signal_edge_stats/auto_paper_mature_templates/active-manifest.json`
- 选中候选：`trend_momentum_v1`
- 可交易范围：`BTC/USDT`、`ETH/USDT`（SOL/USDT不在eligible_symbols中）
- 依据报告：`artifacts/signal_edge_stats/auto_paper_mature_templates/reports/20260718T071508Z.json`
- OOS验证指标（基于manifest生成报告）：BTC OOS 36笔 Sharpe 3.02 PF 1.752 MaxDD 10.9%；ETH OOS 73笔 Sharpe 2.42 PF 1.375 MaxDD 19.4%

### 仓位参数（2026-07-18 保守档）

`services/execution/bootstrap.py` `AUTO_PAPER_TECHNICAL_RULES` `position_rules`：
- `risk_per_trade`: 0.01（原0.05）
- `max_leverage`: 8（原40）
- `max_position_fraction`: 0.12（原0.35）

⚠️ 配置已修改，**必须重启系统才能生效**：运行 `完全重启系统.cmd`，等待启动后运行 `python scripts/verify_config.py` 验证。

- Missing, stale, ineligible, or rules-mismatched evidence rejects the main lane with `validated_edge_stats_missing_or_stale`.
- Local artifacts live under `artifacts/signal_edge_stats/` and are intentionally not committed.

## Supported Checks

```powershell
agent-python -m scripts.verify_runtime_config_sync --database-url sqlite:///.local_paper_console.db
agent-python -m scripts.audit_decision_funnel --database-url sqlite:///.local_paper_console.db --lookback-days 7
agent-python -m scripts.compute_signal_edge_stats --strategy-key auto_paper_mature_templates --database-url sqlite:///.local_paper_console.db --days 365
agent-python -m scripts.verify_config
```

Fresh verification on 2026-07-17: backend `464 passed, 4 skipped`; production-code mypy clean across 144 files; Ruff clean; frontend `35 passed`; production build passed. `scripts.verify_config.py` returned `GREEN: 19/19` with two current-build/current-scope real sampling gateway orders. Top3 scheduler coverage is 3/3 and the dynamic acceptance arms only `signal_observation`; `auto_paper_mature_templates` remains Paper-only. Historical verification/reconciliation orders do not count as proof.
