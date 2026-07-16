# Current Trading Pipeline

The active automatic research loop scans BTC/USDT, ETH/USDT, and SOL/USDT on closed bars. It preserves the platform's Strategy, Validation, Execution, Risk, and Review boundaries.

## Directional Lane

1. Generate role-specific technical signals: 4h direction, 1h state, and 15m entry.
2. Fail closed when timeframe direction and state do not confirm the entry.
3. Fuse eligible signals through `SignalEnsemble`.
4. Attach a structured `MetaLabel`; the automatic lane does not use a locally tuned win-rate threshold as execution evidence.
5. Load candidate-, symbol-, and rules-hash-scoped OOS evidence.
6. Treat LLM output as advisory unless a deterministic blocking risk event exists.
7. Submit to Gatekeeper, which retains stop, leverage, exposure, correlation, loss, drawdown, and anti-Martingale rules.

Missing or invalid evidence produces `validated_edge_stats_missing_or_stale`. Evidence returns are already net of modeled fees and slippage, so Gatekeeper uses OOS net expectancy directly and does not deduct costs a second time.

## Observation Lane

`signal_observation_technical` uses the same signal machinery with relaxed sampling filters. It is local Paper only, cannot mirror to Binance, and sets `strategy_performance_eligible=false`. Its raw-bar proxy is marked `raw_bar_proxy_non_authoritative`.

## Evidence Candidates

- `operator_heuristic_v1`: current ten-signal baseline.
- `trend_momentum_v1`: EMA Trend + ADX direction/state, MACD entry.
- `trend_breakout_v1`: Dow Trend + ADX direction/state, Price Action + FVG entry.

All candidates use the same fixed 2R exit and cost model. Validation runs independently per symbol with a 70/30 chronological split and requires at least 30 OOS trades plus the canonical Sharpe, Profit Factor, drawdown, expectancy, and data-quality gates.
