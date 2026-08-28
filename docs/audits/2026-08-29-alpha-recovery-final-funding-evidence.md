# Alpha Edge Recovery — Final Funding Evidence Closure

## Scope and freeze

This is a read-only closure of the two pre-registered candidates only:
`H1_ENTRY_CONFIRMATION_1BAR` and `H2_SHORT_ONLY`. It creates no strategy,
parameter, execution, risk, gate, Shadow, Testnet, or Promotion change.

## Economic ledger semantics

`v2_managed_positions.realized_pnl` is persisted as price PnL less entry and
exit commission (`services/automated_trading/application/fact_persistence.py`).
Consequently the ledger formula is:

```text
Economic Net PnL = realized PnL after commission + Funding - exact Slippage
```

Commission is reconciled as a diagnostic only; it is not subtracted twice.
For the frozen BTC/ETH `testnet_sampling_v2` scope, 73 closed Episodes reconcile
to `440.62436149 USDT` commission and `-626.20834760 USDT` realized PnL.

## Actual Testnet Funding evidence

The existing Binance Testnet adapter was queried read-only for raw
`incomeType=FUNDING_FEE` history from 2026-08-01 through 2026-08-30. Its
BTC/ETH normalized source contains 64 events and has SHA-256
`c60ca24345e1892d4c22e0136e93a538a177aa03fe38809b8e650b322b3b192f`.
Raw income responses and credentials are not written to the repository.

Event attribution uses exactly `open_time < funding_time <= close_time` and a
single active in-scope filled Episode for the symbol. Results are:

| Status | Episodes |
|---|---:|
| `FUNDING_EXACT` | 17 |
| `FUNDING_ZERO_BY_NO_EVENT` | 53 |
| `AMBIGUOUS_FUNDING_ATTRIBUTION` | 0 |
| `FUNDING_MISSING` | 3 |

The 21 exact events total `-2.00335706 USDT`. Exact historical slippage remains
`UNAVAILABLE`: the persisted receipts lack contemporaneous reference
bid/ask/mid for every fill, and none was backfilled or invented.

## Frozen OOS adjudication

The candidates' frozen chronological 70/30 OOS replay is 2023-01-01 through
2026-07-29. It has no actual historical Funding income source. The current
Testnet account's August 2026 income cannot be attributed to counterfactual
historical candidate positions; public funding-rate rows are not a substitute
for actual funding income evidence.

| Candidate | Frozen OOS expectancy | PF | Funding credit required just to break even |
|---|---:|---:|---:|
| H1 one-bar confirmation | `-0.2331134152R` | `0.6848650952` | `+0.2331134152R/trade` |
| H2 short-only | `-0.2503088674R` | `0.6657651350` | `+0.2503088674R/trade` |

Re-running the old no-Funding replay would not change Funding evidence from
incomplete to complete, so it was not run a second time. No candidate is
validated, and neither may enter Shadow or Testnet Canary.

## Final campaign decision

```text
ALPHA_EDGE_RECOVERY = BLOCKED_BY_FUNDING_EVIDENCE
```

This is a closure of the prescribed recovery campaign, not a claim that no
future strategy can have alpha. The only remaining blocker is exact historical
Funding evidence for the frozen 2023-2026 OOS ledger; no additional recovery
hypothesis is authorized by this campaign.
