# Market Alpha Meta-Label Research Audit

**Status:** `CURRENT_MARKET_ALPHA_SOURCE_EXHAUSTED`

This was a read-only research run. The V2 execution plane, risk settings, scheduler,
Binance adapter, credentials, database schema, and sealed holdout were not changed.

## Scope

- Development range: `2023-01-29T00:00:00Z` through `2026-01-29T00:00:00Z`
- Holdout: not accessed (`holdout_accessed=false`)
- Events: `37,856`
- Arms: 5 feature sets x 2 models x 4 target RRs = `40` total
- Features: price-only baseline, derivatives pressure, spot/perpetual dislocation, BTC/ETH lead-lag, all alpha
- Models: regularized Logistic Regression and Gradient Boosting
- Positioning: equal initial stop risk; target candidates `1.5R`, `1.75R`, `2.0R`, `2.25R`

## Method Correction

The final runner sorts each train/validation/OOS slice by event time. Thresholds are
selected only from validation using expected Net-R after base cost and a 15-trade
minimum; final acceptance is evaluated separately. This prevents validation gates
from manufacturing an all-zero report while preserving the locked historical gates.

## Result

Only `6` arms had a configured walk-forward window and only `3` arms produced any OOS
trades. The best non-zero arm was `BTC_ETH_LEAD_LAG / GBM / 1.75R`:

For context, the current Testnet live baseline is 25 closed episodes at 48% win rate,
approximately `0.577` Net-R payoff, PF `0.337`, and negative expectancy. The new arm
does not qualify as an improvement claim: its sample is only 4 OOS trades and its
expectancy is negative.

| Metric | OOS result | Locked requirement |
|---|---:|---:|
| Trades | 4 | >=100 |
| Win rate | 50.00% | >=55% |
| Net-R payoff | 1.23 | >=1.15 |
| Profit factor | 1.23 | >=1.40 |
| Expectancy | +0.14R | >=+0.10R |
| Expectancy LCB95 | -1.43R | >0 |
| Positive windows | 1/8 | >=6/8 |
| Max drawdown | 2.49R | <=20R |
| 1.5x-cost expectancy | +0.02R | >0 |
| 1.5x-cost PF | 1.04 | >1.10 |

`accepted_arms=0`. The report is stored at
`artifacts/market_alpha/reports/market_alpha_meta_research.json`; canonical events
are stored at `artifacts/market_alpha/canonical/market_alpha_events.jsonl`.

## Decision

No candidate is registered, promoted, armed, or sent to Testnet. The next valid
research change must be a newly authorized information source or hypothesis; another
OHLCV/structure/trend parameter pass is out of scope.
