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

- Candidate set: `operator_heuristic_v1`, `trend_momentum_v1`, `trend_breakout_v1`.
- Evidence is computed independently per candidate and symbol using a chronological 70/30 split.
- Missing, stale, ineligible, or rules-mismatched evidence rejects the main lane with `validated_edge_stats_missing_or_stale`.
- Local artifacts live under `artifacts/signal_edge_stats/` and are intentionally not committed.
- Latest 365-day local replay report: `artifacts/signal_edge_stats/auto_paper_mature_templates/reports/20260716T145629Z.json`.
- Result: no candidate/symbol reached the 30-trade OOS minimum; no active manifest was created. The main lane therefore remains non-trading by design.

## Supported Checks

```powershell
agent-python -m scripts.verify_runtime_config_sync --database-url sqlite:///.local_paper_console.db
agent-python -m scripts.audit_decision_funnel --database-url sqlite:///.local_paper_console.db --lookback-days 7
agent-python -m scripts.compute_signal_edge_stats --strategy-key auto_paper_mature_templates --database-url sqlite:///.local_paper_console.db --days 365
agent-python -m scripts.verify_config
```

Fresh verification on 2026-07-17: backend `464 passed, 4 skipped`; production-code mypy clean across 144 files; Ruff clean; frontend `35 passed`; production build passed. `scripts.verify_config.py` returned `GREEN: 19/19` with two current-build/current-scope real sampling gateway orders. Top3 scheduler coverage is 3/3 and the dynamic acceptance arms only `signal_observation`; `auto_paper_mature_templates` remains Paper-only. Historical verification/reconciliation orders do not count as proof.
