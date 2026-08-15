# Market Alpha + Meta-Label Research Plan

**Status:** CURRENT_MARKET_ALPHA_SOURCE_EXHAUSTED

This plan supersedes the prior strategy-family tuning loop. It does not change any
Layer-1 execution invariant or authorize production trading.

## Frozen Scope Contract

### MUST_CHANGE

- `services/strategy_library/market_alpha.py`
- `scripts/run_market_alpha_meta_research.py`
- matching research tests and immutable artifacts under `artifacts/market_alpha/`
- this plan and `execution-manifest.yaml` research status/contract fields

### MAY_CHANGE

- research-only feature extraction, dataset manifests, Logistic Regression baseline,
  Gradient Boosting comparison, nested walk-forward ledger, and reports
- point-in-time public Binance market-data acquisition cache

### MUST_NOT_CHANGE

- all V2 scheduler, single-writer, entry/exit/protection, reconciliation, recovery,
  Binance adapter, risk ceilings, leverage, symbols, database schema, credentials,
  mainnet/testnet switches, and existing execution manifests for active runtime
- all previous strategy-family implementations; they remain research references only
- sealed holdout data after `2026-01-29T00:00:00Z`

## Research Contract

- Data sources: futures/spot 1h public Binance klines (taker-buy volume and quote
  volume), point-in-time funding history, and BTC/ETH cross-asset returns.
- Baseline: PRICE_ONLY opportunity generator with equal-risk 1R stop and target
  candidates `1.5R`, `1.75R`, `2.0R`, `2.25R`.
- Alpha: DERIVATIVES_PRESSURE, SPOT_FUTURES_DISLOCATION, and BTC_ETH_LEAD_LAG.
- Models: regularized Logistic Regression first; Gradient Boosting only if its
  nested OOS result beats Logistic on the frozen acceptance metrics.
- Gate: calibrated `P(TP_FIRST)` plus expected Net-R after cost; rejected events are
  `NO_TRADE`. All research trades use equal initial stop risk.
- Historical acceptance requires every locked threshold from the operator contract:
  100+ OOS trades, win rate >=55%, Net-R payoff >=1.15, PF >=1.40, expectancy >=0.10R,
  LCB95 >0, 6/8 positive windows, max DD <=20%, and 1.5x-cost expectancy >0/PF>1.10.
- Testnet acceptance is a separate forward contract: >=30 closed trades and >=14
  days, win rate >=52%, Net-R payoff >=1.10, PF >=1.15, positive Net Expectancy and
  Net PnL. Until then status cannot be `RESULT_ACCEPTED`.

## Historical Research Result (2026-08-14)

The corrected runner evaluated 40 declared arms across 5 feature sets, 2 models,
and 4 target RRs on 37,856 point-in-time events from 2023-01-29 through the sealed
boundary. Training and validation were time-ordered; validation selected only on
expected Net-R after base cost with a 15-trade minimum, while final acceptance used
the locked thresholds above plus 1.5x cost stress. Only 6 arms had any configured
walk-forward windows and only 3 produced OOS trades; the best non-zero arm had 4
OOS trades, 50% win rate, 1.23 payoff, PF 1.23, expectancy +0.14R, LCB95 -1.43R,
and 1.5x-cost expectancy +0.02R with stress PF 1.04. `accepted_arms=0`;
`holdout_accessed=false`.

The result is `CURRENT_MARKET_ALPHA_SOURCE_EXHAUSTED`, not a production candidate
and not Testnet forward evidence. No execution, risk, scheduler, exchange, or
credential code was changed.

## Completion States

- `CURRENT_MARKET_ALPHA_SOURCE_EXHAUSTED`: all declared source/model combinations
  exhausted without historical acceptance.
- `HISTORICAL_ALPHA_PASS`: historical gates pass; sealed holdout remains untouched.
- `RESULT_ACCEPTED`: only after the separate Testnet forward contract passes.

---

# Archived: Aggressive Multi-Regime V1 Implementation Plan

**Status:** IMPLEMENTATION_COMPLETE_PENDING_OOS
**Base:** `backup/2026-08-10-wip@7b98e75311d51551aae212038e29045d3a0e65c4`

## Objective

Replace only the upstream strategy brain with `aggressive_multi_regime_v1`:
regime scoring, candidate generation/selection, single-target geometry projection,
authorized production adaptation, and score-based risk tier selection.

## Frozen Execution Contract

The following remain behaviorally unchanged: scheduler lease and single-writer
coordination, cycle claim/fencing, `run_automated_trading_cycle`, entry gate,
intent/order/fill persistence, Binance adapter, managed position projection,
protection submission, exits, reconciliation, recovery, and emergency close.

## Authorized Files

- `services/strategy_library/regime/scorer_v2.py`
- `services/strategy_library/candidates/trend_pullback_v2.py`
- `services/strategy_library/candidates/range_sweep_reversion_v1.py`
- `services/strategy_library/candidates/breakout_continuation_v1.py`
- `services/strategy_library/candidates/registry.py`
- `services/strategy_library/proposal_pipeline.py`
- `services/strategy_library/ensemble/selector_v2.py`
- `services/strategy_library/v2_projection.py`
- `services/automated_trading/application/production_strategy.py`
- `services/execution/v2_scheduler_entry.py`
- `services/validation/proposal_replay.py`
- `services/validation/strategy_promotion.py`
- `scripts/run_proposal_research_replay.py`
- matching tests under `tests/services/strategy_library/` and listed V2/replay tests

## Explicit Non-Goals

No execution service, exchange adapter, scheduler coordination, database schema,
launcher, protection implementation, or hard risk ceiling changes. No new symbols.
No Final Holdout parameter tuning. No Canary fallback when an authorized strategy
has no proposal.

## TDD Gates

Red tests cover timeframe conflict, valid/invalid breakout, range routing,
selector conflict and score floor, deterministic projection, risk tiers,
no-Canary fallback, and preservation of the original cycle call. Green requires
focused strategy tests plus the complete V2 execution regression suite.
