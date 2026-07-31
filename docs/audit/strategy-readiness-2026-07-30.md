# Strategy Readiness Report

**Observed:** 2026-07-30
**Scope:** BTC/USDT and ETH/USDT, Binance USDT-M Testnet execution lane

## Verdict

`DATA_READY` / `ACTIVE_STRATEGY_REJECTED` / `OPTIMIZATION_NOT_READY`

The V2 exchange-first execution loop is verified and the required 42-month
five-timeframe history is now complete. The frozen legacy candidate baseline is
not eligible for promotion: it fails every current performance gate, and the
validation path still lacks next-bar parity, point-in-time funding and a final
dependent bootstrap. This is a negative strategy result, not an execution
failure.

## Evidence

| Dimension | Status | Evidence |
|---|---|---|
| K-line source | PASS | Binance Vision USDT-M archives with official SHA-256 checksums; 1m bars are aggregated deterministically into 5m/15m/1h/4h. |
| Historical coverage | PASS | BTC and ETH each contain 1,879,200 1m; 375,840 5m; 125,280 15m; 31,320 1h; and 7,830 4h bars from 2023-01-01 through the 2026-07-29 cutoff. |
| Time consistency | PASS for frozen OHLCV | All ten required series report zero gaps, zero missing bars and zero incomplete aggregation buckets. |
| Final Holdout | SEALED | 2026-01-29 through 2026-07-29 is frozen by range/hash; `holdout_results_accessed=false`. |
| Lookahead / next-bar execution | IN_PROGRESS / NOT_READY | Replay now models a closed-bar signal followed by next-bar-open fill, with regression coverage; full backtest/live input-hash parity is still outstanding. |
| Fees / slippage | PARTIAL | Current rule fees/slippage are included. Funding, latency, spread and partial fills are explicitly not modeled. |
| Walk-forward / OOS | PARTIAL | Coverage supports 12 training months plus eight 3-month OOS windows, but this baseline is one aggregate pre-holdout replay, not a per-window refit/evaluation ledger. |
| Bootstrap / confidence interval | NOT_PROMOTION_ELIGIBLE | Current CI is a 90% IID percentile bootstrap. Portfolio expectancy CI is `[-0.002045, 0.001463]` and Sharpe CI is `[-1.1325, 0.7844]`; both cross zero. |
| Manifest binding | PASS but ineligible | Active manifest resolves to `trend_momentum_v1`; its rules hash matches current config, while `execution_eligible=false`. |

## Frozen Legacy Result

| Scope | Trades | Win rate | Net expectancy | Sharpe | Profit factor | Max drawdown | Net return |
|---|---:|---:|---:|---:|---:|---:|---:|
| BTC/USDT | 434 | 34.0% | negative | -0.13 | 0.98 | 41% | -17% |
| ETH/USDT | 692 | 35.0% | negative | -0.03 | 1.00 | 76% | -6% |
| Portfolio | 1,126 | 34.64% | -0.000202 | -0.1096 | 0.9882 | 77.14% | -22.71% |

The active candidate fails the repository gates (`Sharpe > 1.0`, `PF > 1.3`,
`MaxDD < 25%`, `Expectancy > 0`). No holdout result was used to reach this
decision.

## Decision

Do not change MACD/RSI thresholds, leverage, position sizing, stop/take-profit,
net-edge gates or promotion thresholds to rescue this candidate. Do not promote
the active manifest.

The approved V2 strategy plan may proceed only to Phase 1 data/feature parity:

1. Implement point-in-time `MarketSnapshot` and shared feature computation.
2. Prove backtest/live input-hash parity and next-bar execution semantics.
3. Add point-in-time funding and realistic spread/latency/partial-fill costs.
4. Add per-window walk-forward ledgers and a dependent/block bootstrap.
5. Optimize new candidate families only inside training/OOS; keep Final Holdout
   sealed until the methodology and promotion gates are complete.

## Phase 1 Progress (2026-07-31)

The first parity slice is implemented in
`services/validation/technical_replay.py`: a signal is evaluated only after the
bar is closed, and the historical fill uses the following bar's open and
timestamp. A signal on the final available bar is rejected as unfilled rather
than being fabricated as an `end_of_window` trade.

Evidence: `tests/services/test_technical_strategy_validation.py` now covers the
next-bar-open fill, no-following-bar boundary, and both `end_at` boundaries; the
focused replay suite passes 14 tests and related validation tests pass 21 tests.

This intentionally changes the replay semantics after the frozen legacy
baseline. `baseline-20260729-0000Z-r4` remains a historical pre-parity artifact;
its source hash is no longer a binding claim for the current working tree. No
new baseline or Final Holdout result was generated in this slice.

The corrected pre-parity artifact is
`artifacts/strategy_refactor/baseline-20260729-0000Z-r4`. The original directory
is retained as immutable evidence of the JSONL serialization defect. `r1` is
also retained but superseded because a concurrent stale generator captured a
source-tree hash before the final knowledge sync. `r2` is retained but
superseded because concurrent research-candidate commits changed the source
tree during its replay.
`r3` is retained but superseded because the repository-mandated generated
pytest block was refreshed after its replay.
