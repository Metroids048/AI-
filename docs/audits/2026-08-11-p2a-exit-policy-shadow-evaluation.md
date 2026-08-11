# P2-A Exit Policy Shadow Evaluation — Results

- **Date:** 2026-08-11
- **Frozen baseline:** `720a36e`
- **Status:** `P2A_IMPLEMENTATION_COMPLETE_PENDING_REVIEW`
- **Governing contract:** [ADR-004](../adr/ADR-004-strategy-owns-geometry-execution-owns-safety.md), `.p2a-execution-manifest.yaml`
- **Raw run output:** [2026-08-11-p2a-exit-policy-shadow-run.txt](2026-08-11-p2a-exit-policy-shadow-run.txt)

## Headline

**The sample is too small to promote anything.** 9 real entries. Every business verdict
is `INSUFFICIENT_SAMPLE` against the repository's existing 30-trade floor. What follows is
observation, not evidence of edge.

Two findings are nonetheless worth acting on:

1. **The 0.35% floor binds on 10 out of 10 real trades.** The ATR term
   (`1.2 * ATR14`) never once exceeded the percentage floor, so the "adaptive" stop has
   been a fixed 0.35% / 0.525% / RR 1.5 geometry in production, for every symbol, side and
   volatility level. This is now measured, not inferred.
2. **All 9 entries had a positive favourable excursion.** Over a fixed 24h horizon, mean
   MFE was 3.20R against mean MAE of -0.82R. This is the single most promising number in
   the run — and at n=9 it is not a conclusion.

## Sample

| Field | Value |
|---|---|
| `SAMPLE_START` | 2026-08-07T10:00:39Z |
| `SAMPLE_END` | 2026-08-11T05:45:32Z |
| `REAL_CONTROL_TRADES` | 9 replayed (10 positions exist; 1 still `PROTECTED`, excluded) |
| `BTC_TRADES` | 5 |
| `ETH_TRADES` | 4 |
| `SKIPPED_NO_BARS` | 0 |
| `SKIPPED_NO_POINT_IN_TIME_ATR` | 0 |
| `OUTCOME_ROWS` | 45 (9 entries x 5 policies) |
| Side distribution | 7 short / 2 long |

Every entry is a real `testnet_sampling_v2` Binance Testnet fill with a real exchange order
ID. No synthetic trade entered these results.

## Policy comparison (overall, after cost)

| POLICY | TRADES | NET_EXP | PF | AVG_R | MAX_DD | CAPTURE | FEE_DRAG | HOLD_MIN | AMBIG |
|---|---|---|---|---|---|---|---|---|---|
| A_CURRENT_CONTROL | 9 | +2.38 | 2.57 | 0.38 | 23.30 | 69.9% | 20.84 | 128 | 0 |
| B_ATR_ADAPTIVE | 9 | +0.29 | 2.24 | -0.11 | 18.16 | 73.0% | 20.86 | 75 | 0 |
| **C_STRUCTURE_INVALIDATION** | 9 | **+4.15** | **5.74** | 0.22 | **15.27** | 73.1% | 20.84 | 127 | 0 |
| D_SCALE_OUT_RUNNER | 9 | +0.17 | 2.18 | -0.17 | 18.16 | 48.8% | 20.86 | 89 | 0 |
| E_REGIME_AWARE | 9 | +2.38 | 2.57 | 0.38 | 23.30 | 69.9% | 20.84 | 128 | 0 |

- `CURRENT_CONTROL_RANK` = **2 / 5** by net expectancy
- `BEST_OBSERVED_POLICY` = **C_STRUCTURE_INVALIDATION** (+4.15 vs +2.38 USDT/trade)
- `BEST_TREND_POLICY` / `BEST_RANGE_POLICY` = **undetermined** (no regime classifier; see below)

`BEST_OBSERVED_POLICY` means best in this 9-trade sample. It is not a promotion signal.

### Per symbol

| POLICY | BTC NET_EXP | BTC AVG_R | ETH NET_EXP | ETH AVG_R |
|---|---|---|---|---|
| A_CURRENT_CONTROL | +1.95 | 0.21 | +2.90 | 0.59 |
| B_ATR_ADAPTIVE | -2.36 | -0.68 | +3.60 | 0.60 |
| C_STRUCTURE_INVALIDATION | +3.82 | -0.06 | +4.57 | 0.57 |
| D_SCALE_OUT_RUNNER | -2.66 | -0.79 | +3.72 | 0.62 |
| E_REGIME_AWARE | +1.95 | 0.21 | +2.90 | 0.59 |

B and D invert sign between BTC (negative) and ETH (positive) on 5 and 4 trades
respectively. At this sample size that is noise, not a symbol effect.

## Per-trade detail (baseline policy A)

| SYMBOL | SIDE | ENTRY | REASON | NET | MFE_R | MAE_R | CAPTURE | HOLD_MIN |
|---|---|---|---|---|---|---|---|---|
| BTC | long | 64996.20 | STOP | -11.34 | 0.68 | -1.04 | -190.2% | 976 |
| BTC | short | 64009.30 | STOP | -11.64 | 0.60 | -1.01 | -215.4% | 243 |
| ETH | short | 1871.62 | STOP | -11.66 | 0.18 | -1.01 | undefined | 128 |
| BTC | short | 64390.84 | TARGET | +10.98 | 1.52 | -0.97 | 79.8% | 51 |
| BTC | short | 65007.70 | TARGET | +10.78 | 1.58 | -0.62 | 77.0% | 206 |
| BTC | short | 64767.49 | TARGET | +10.99 | 1.73 | -0.27 | 70.2% | 54 |
| ETH | short | 1906.00 | TARGET | +10.99 | 1.52 | -0.15 | 79.9% | 24 |
| ETH | long | 1912.52 | TARGET | +1.50 | 2.17 | -0.09 | 55.8% | 127 |
| ETH | short | 1918.00 | TARGET | +10.78 | 1.75 | -0.60 | 69.5% | 160 |

6 targets, 3 stops. Winners capture 56-80% of their own MFE, which is close to the
arithmetic ceiling for a fixed 1.5R target — once the target fires, the remaining
favourable move is unreachable by construction.

The three losers each showed 0.18-0.68R of favourable movement before reversing into the
stop, producing negative capture ratios. That pattern is what an exit-timing problem looks
like, but three trades cannot establish it.

## Business questions

### Q1_ENTRY_HAS_EDGE = `INSUFFICIENT_SAMPLE`

`Q1_EVIDENCE`: only 9 entries, need 30.

Observed only, explicitly not a conclusion: mean MFE **3.20R** vs mean MAE **-0.82R**;
median MFE 3.77R, median MAE -0.62R; **9/9 entries had positive MFE**; 24h horizon.

Measured at entry level over one fixed horizon per entry, deliberately *not* from the 45
per-policy rows — those would count each entry five times and truncate each measurement at
the policy's own exit, mixing exit choice into a question about entry quality.

If this ratio survives to n>=30 it would indicate the entry signal is not the primary
problem. At n=9 it is a hypothesis worth collecting data for.

### Q2_EXIT_LEAKAGE = `INSUFFICIENT_SAMPLE`

`Q2_EVIDENCE`: only 9 CONTROL trades, need 30.

Observed only: CONTROL median capture 69.9%. Read carefully — this is capture measured
against MFE *truncated at CONTROL's own exit*, so it cannot by itself show leakage. The
suggestive number is elsewhere: mean MFE over a fixed 24h horizon was 3.20R while the fixed
target sits at 1.5R. That gap is where leakage would live, but the horizons are not
comparable, so it is a lead to test rather than a finding.

### Q3_POLICY_BY_REGIME = `INSUFFICIENT_SAMPLE`

`Q3_EVIDENCE`: need at least 2 regimes with 5+ trades each.

**Blocked by a missing component, not only by sample size.** This repository has no
validated point-in-time regime classifier available to P2-A. `classify_regime` returns
`UNKNOWN` for every entry by design, because the frozen scope forbids inventing a
classifier to make a slice look better. Consequently:

- Policy E (regime-aware) correctly fails closed onto CONTROL and is **byte-identical to A
  in every row of this run**. E contributes no information here; that is the specified
  fail-closed behaviour, not a defect.
- `BEST_TREND_POLICY` and `BEST_RANGE_POLICY` cannot be answered at all.

A point-in-time regime classifier is the single largest blocker to answering Q3.

## Method notes and honest limitations

**Intrabar ambiguity: 0 occurrences.** Replay used 1m bars, the highest resolution
available. No bar bracketed both stop and target, so `STOP_FIRST` never had to be invoked
and no `TARGET_FIRST` sensitivity was needed. The machinery is implemented and unit-tested;
it simply did not trigger on this sample. On coarser bars it would.

**Costs.** Entry fees are the actual exchange-charged fees. Exit fees are estimated at 5bps
taker plus 1bps slippage against the frozen Binance USDT-M assumption. Actual and estimated
components are tracked separately and never merged. All rankings above are after cost.

**Structural limitation of the method.** Replaying alternative exits over already-taken
entries cannot capture that a different exit would have changed position availability, and
therefore which later entries were reachable. P2-A ranks exit geometry; it does not
simulate an alternative history.

**Policy C is a proxy, not real structure recognition.** `C_STRUCTURE_INVALIDATION` uses an
ATR-multiple stand-in (2x ATR stop, 3x ATR target) because no structure analyzer exists.
That C leads the table on 9 trades therefore does **not** establish that structural exits
work — it establishes that a wider stop with a wider target did better on 9 trades. Real
structure recognition remains unbuilt.

**Two defects found and fixed during implementation**, both of which had produced
plausible-looking wrong numbers:

1. *Bar-window timestamp mismatch.* `ohlcv_bars.time` stores space-separated naive strings
   and the range filter is a string comparison, so passing `datetime.isoformat()` silently
   discarded every bar on the fill date (`'T'` sorts after `' '`). The first run began
   replaying ~14h late and reported a 976-minute median hold for a 0.35% stop. After the
   fix, policy A reproduces the real trade: 127 replayed minutes against ~128 real minutes
   to the actual exchange close. Pinned by `test_db_timestamp_format_matches_stored_convention`.
2. *Capture-ratio denominator.* Requiring only `MFE > 0` let a 0.0002% excursion produce
   `-107242%`. The floor is now the trade's own round-trip cost: if the favourable
   excursion never exceeded the cost of exiting at that peak, no profit was capturable and
   the ratio is `undefined` with a recorded reason. Genuinely negative ratios are retained.
   Pinned by two regression tests.

A third bug was caught by the mandated test rather than by inspection: the scale-out ladder
re-fired an already-consumed target on later bars, letting quantity fractions sum above 1.
Fixed with a consumed-leg guard.

## Compliance

| Requirement | Status |
|---|---|
| Real entries only, no synthetic in business results | Verified — 9 real Testnet fills with exchange order IDs |
| Entry held fixed; only exit varied | Verified — `test_real_entry_is_immutable` |
| Point-in-time initial geometry | Verified — `test_no_future_data_in_initial_geometry`; ATR computed only from bars closed at/before the decision bar |
| No execution mutation | Verified — `test_no_execution_mutation`; DB opened `mode=ro`; positions still 10 after the run |
| Research proposal geometry not reused | Verified — `test_research_proposal_not_improperly_reused` |
| Intrabar ambiguity handled | Implemented + tested; 0 occurrences at 1m |
| Costs never zero | Verified — `test_fees_included_in_net_pnl` |
| `MUST_NOT_CHANGE` untouched | Verified — only `services/research/` and `tests/services/research/` added |

## Recommended next step

Do not change any production parameter on 9 trades. The two things that would actually
unblock P2 are:

1. **Accumulate sample.** Every verdict here is sample-limited. n>=30 is the existing floor.
2. **Build a point-in-time regime classifier.** Without it, Q3 is unanswerable and policy E
   is inert, which removes the most interesting hypothesis — that trending and ranging
   markets want different exits — from testability.

Whether to proceed to P2-B depends on Q1, which is currently undetermined. The observed
9/9 positive MFE leans toward "entry is not the main problem", which would argue for
continuing on exits — but that lean is not evidence yet.
