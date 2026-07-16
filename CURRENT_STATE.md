# Current State

Last updated: 2026-07-16 23:05 Asia/Shanghai

## Authority

Current truth is resolved in this order: current code and tests, the active runtime database and scheduler state, this file, architecture/ADR documents, then archived incident material. Files under `docs/archive/` and `scripts/archive/` are historical evidence only.

## Runtime

- Environment: Paper / Binance Simulation only; mainnet remains disabled.
- Automatic research universe: `BTC/USDT`, `ETH/USDT`, `SOL/USDT`.
- Scheduled lanes: `auto_paper_mature_templates` and local-only `signal_observation_technical`.
- Directional lane: requires fresh symbol-scoped OOS evidence whose candidate and rules hash match runtime rules.
- Observation lane: may use the 47-bar proxy for diagnostics, but it is non-authoritative, cannot mirror to a gateway, and is excluded from strategy performance.
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

Fresh verification on 2026-07-16: backend `451 passed, 2 skipped`; mypy clean; Ruff clean; frontend `34 passed`; production build passed; runtime config sync passed; Top3 data completeness passed; `verify_config.py` returned `GREEN: 19/19`; funnel audit reported 3,496 decisions over the last 7 days. The replay command above intentionally returned `accepted=False` because local OOS samples are insufficient.
