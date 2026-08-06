# QuantDinger Selective Integration

**Status:** implemented as an isolated Shadow/research bridge.  This document
does not change the Automated Trading V2 invariants.

## Boundary

```
QuantDinger Strategy V2 source
  -> static AST manifest -> hash-bound structured Shadow signal
  -> Shadow TradeCandidate -> terminal SHADOW_MODE_NO_SUBMIT funnel record
  -> optional hash/timeframe/warmup-bound replay artifact -> differential replay

Any candidate reaching Entry Gate remains RESEARCH and is rejected before Binance Adapter.

Binance Testnet Adapter -> normalized fills -> existing immutable receipts
```

`services/automated_trading/` remains the only automated Binance Testnet order
writer.  The adapter bridge owns no gateway, scheduler, local position record,
or protection order submission.  It accepts only BTC/USDT and ETH/USDT when a
research signal is translated to a V2 candidate; a dynamic external universe is
research coverage, never execution permission.

## Source Record

| Field | Value |
| --- | --- |
| Source archive | `C:\Users\Windows11\Downloads\QuantDinger-main.zip` |
| Version | 5.0.1 |
| Archive SHA-256 | `09CD6DC32BDB790E2D671D839F0E38843F158BC163613E52A2787A5836C464D2` |
| Backend license | Apache-2.0 |
| Used concepts | Strategy API V2 manifest discovery; execution event normalization |
| Direct source copy | None |

QuantDinger copyright and Apache-2.0 attribution are retained here for the
reviewed source.  This project does not use QuantDinger branding.  Any future
direct source reuse must retain its notices and add the relevant license text.

## Explicit Exclusions

- No `live` / mainnet switch, multi-exchange automatic execution, grid worker,
  local position ledger, QuantDinger frontend, or product branding.
- No risk, leverage, stop/take-profit, net-edge, market-universe, or promotion
  threshold changes.
- No strategy source execution inside the API, Scheduler, or execution
  process.  An explicit offline/Shadow command may run a reviewed source in a
  bounded child process; imports, credentials, exchange clients, databases,
  local positions, and order writers are unavailable there.  Order APIs are
  captured as signals only.

## Verification Strategy

The bridge has deterministic manifest, source safety, execution-universe,
Shadow-no-submit, duplicate fill, partial fill, fee-evidence, and malformed
receipt tests.  Existing `TechnicalStrategyValidationService` remains the
authority for formal OOS/cost/promotion evidence.  A real natural Scheduler
entry, protection and reduce-only exit still require fresh Binance Testnet
evidence and cannot be inferred from these tests.

The offline command `scripts/run_quantdinger_shadow.py` now produces the same
hash-bound artifact contract consumed by the parser.  The latest development
window run on the checked-in Binance Vision history database (BTC/USDT 15m,
2025-01-01 through 2025-01-08, 320 bars) executed the reviewed protected-entry
source in the constrained child process and emitted 1 de-duplicated signal /
1 next-bar replay trade, with 145 duplicate-target events rejected.  The
artifact hash is
`41025a9fbeb963fec18229dabcbd5944267eae8574ce1f9864f5b5ce5456a169` and the
replay exit was `takeprofit`.  Artifact parsing passed; the current differential
report intentionally records 0 local trades and 1 unmatched external trade
because no authoritative local replay payload was preserved for this rerun.
That report is `NOT_PROMOTION_EVIDENCE`, not a claim of parity or strategy
quality.  The source remains Shadow-only until a fresh authoritative local
replay, formal OOS/cost gates, and the separate natural Testnet lifecycle
evidence all pass.

## Differential Replay

`services.validation.quantdinger_differential_replay` compares external
Shadow events with the authoritative `ReplayTrade` result. It rejects an entry
unless it is exactly one manifest timeframe after an aligned closed signal bar,
and reports mismatches in next-bar presence, entry/stop/take/exit price, exit
time/reason and fees. Comparison tolerances must be finite non-negative
`Decimal` values. The
artifact parser accepts only schema version `1`, locked QuantDinger source
version `5.0.1`, the compiled manifest hash, the compiled timeframe, and at
least the declared warmup length. It neither imports nor executes the external
source. The report is evidence-only: a consistent result cannot promote the
strategy, add it to an active manifest, or write an exchange order.

## Shadow Observability

`parse_shadow_signal()` accepts exactly one explicit event schema and binds it
to the static manifest hash before a candidate can be constructed. Finite
numeric values, timezone-aware timestamps, an unexpired signal, strategy
identity, BTC/ETH scope and manifest protection geometry are all required.

`build_shadow_funnel_record()` writes the same V2 funnel vocabulary through
`CANDIDATE_CREATED`, then terminates at `MANIFEST_EVALUATED` with
`SHADOW_MODE_NO_SUBMIT`. It deliberately never emits an intent, exchange,
position, or protection stage. This makes source behavior observable without
allowing an external evaluator to become a second order writer.
