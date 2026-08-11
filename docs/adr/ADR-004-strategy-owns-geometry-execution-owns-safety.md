# ADR-004: Strategy Owns Geometry, Execution Owns Safety

- **Date:** 2026-08-11
- **Status:** Accepted
- **Authorized by:** Operator, explicitly, as a Layer-1 invariant change
- **Relates to:** [ADR-001 Single Writer](ADR-001-automated-trading-v2-single-writer.md), [ADR-002 Exchange-First Receipts](ADR-002-exchange-first-receipts.md), [ADR-003 Entry Exit Gate Separation](ADR-003-entry-exit-gate-separation.md)
- **Recorded in decisions-log.md as:** ADR-080 (that file uses a separate running series; this file continues the `docs/adr/` V2 series)

## Context

The V2 automatic execution chain is verified end-to-end on Binance USDT-M Testnet: a
closed 15m bar produces a candidate, entry gates authorize it, a real exchange order
fills, protection is submitted from the actual fill price, and a reduce-only exit
reconciles back into local state. That chain is no longer the bottleneck.

The bottleneck moved to **who decides the price geometry of a trade**. Today that
decision is split in a way nobody designed on purpose:

**Entry is already quantitative.** The lane that actually submits orders,
`testnet_sampling_v2`, evaluates closed 15m bars against EMA50, MACD histogram, an RSI
band, and a positive ATR14 — it does not fire at fixed price levels
([testnet_sampling_v2.py:8-9](../../services/strategy_library/candidates/testnet_sampling_v2.py#L8-L9)).

**Exit is effectively hardcoded.** The stop is
`max(1.2 * ATR14, reference_price * 0.0035)` and the take-profit is `1.5 * stop`
([decision_service.py:294-295](../../services/automated_trading/application/decision_service.py#L294-L295),
[decision_service.py:472-473](../../services/automated_trading/application/decision_service.py#L472-L473)).
The ATR term is adaptive in principle, but whenever `1.2 * ATR14` falls below 0.35% of
price the floor dominates and the strategy degenerates to a fixed
**SL 0.35% / TP 0.525% / RR 1.5** geometry regardless of symbol, direction, regime, or
volatility. Both BTC and ETH entries observed on 2026-08-10 landed exactly on that
floor, which is why the observed stop/target band looked implausibly narrow.

Three research candidates already generate genuinely structured geometry instead:

| Candidate | Stop basis | Targets |
|---|---|---|
| `trend_pullback_v2` | pullback extreme ∓ `0.25 * ATR` | 1R / 1.8R / 2.5R at 35% / 40% / 25% |
| `range_sweep_reversion_v1` | sweep extreme ∓ `0.25 * ATR` | range midpoint / opposite boundary / break runner at 40% / 40% / 20% |
| `failed_breakout_reversal_v1` | reclaim extreme ∓ ATR buffer | structured, same contract |

These are emitted as `StrategyProposal`, which carries an `EntryTrigger`, an
`InvalidationRule` (a structural failure point, not a percentage), and a
`targets` tuple whose `quantity_fraction` values must sum to exactly 1
([proposals.py:44](../../services/strategy_library/proposals.py#L44),
[proposals.py:56-58](../../services/strategy_library/proposals.py#L56-L58)).

Meanwhile the frozen execution entry point, `TradeCandidate`, accepts exactly **one**
`stop_distance` and **one** `take_profit_distance`
([candidates.py:81-82](../../services/automated_trading/domain/candidates.py#L81-L82)),
and protection submits a single reduce-only leg
([protection_service.py:401](../../services/automated_trading/application/protection_service.py#L401)).
No `StrategyProposal` → `TradeCandidate` adapter exists anywhere under
`services/automated_trading/` or `services/execution/`.

So the research layer can already express "take 1R, then 1.8R, then let 25% run with the
trend" and the execution layer structurally cannot execute it. Any attempt to promote a
research candidate without resolving this would silently collapse the ladder to a single
target and discard precisely the runner leg that motivated the work.

The failure mode this ADR exists to prevent is **parameter-tuning disguised as a fix**:
changing 0.35% to 0.7%, or 1.5R to 2R, produces a different hardcoded geometry, not a
strategy that adapts to the market. It also destroys attribution — after changing entry
and exit together, a change in PnL cannot be assigned to entry quality, stop placement,
target placement, or a regime shift.

## Decision

**Strategy owns geometry. Risk owns limits. Execution owns fidelity.**

Every price-level decision in a trade belongs to the strategy layer and must be produced
by a quantitative rule that can be backtested, replayed, and falsified. Every
account-survival boundary remains a hard configured limit that no strategy may raise.
Execution faithfully carries out authorized geometry and never invents it.

### Ownership boundary (normative)

| Decision | Owner | Rationale |
|---|---|---|
| Direction (LONG / SHORT) | Strategy | Falsifiable from data |
| Entry timing and confirmation | Strategy | Falsifiable from data |
| Stop price / invalidation level | Strategy | "Where is my thesis wrong" is a market-structure question |
| Target prices and partial fractions | Strategy | "Where is this worth banking" is a market-structure question |
| Whether to keep holding a runner | Strategy | Falsifiable from data |
| Max risk per trade | Hard limit | Account survival |
| Max position fraction / total exposure | Hard limit | Account survival |
| Max leverage | Hard limit | Account survival |
| Max entry price drift | Hard limit | Execution integrity |
| Stop must exist | Hard limit | Non-negotiable per Layer 1 |
| Duplicate-entry prohibition | Hard limit | Account survival |
| No entry while reconciliation is unhealthy | Hard limit | Ownership ambiguity |
| Order submission, protection, recovery, reconciliation | Frozen execution | Verified known-good |

A strategy proposing geometry that violates a hard limit is **rejected, never clamped
into compliance silently**. Rejection reasons must remain visible in the decision funnel.

### What this ADR does NOT authorize

- It does not change any currently running production parameter. `testnet_sampling_v2`
  keeps its exact entry rules, its `max(1.2 * ATR14, price * 0.0035)` stop, its `1.5R`
  target, and its current position/leverage/gate settings until evidence promotes a
  replacement.
- It does not grant any research candidate submit authority.
- It does not permit tuning the 0.35% floor, the 1.5R multiple, or any Validation Layer
  promotion threshold (`Sharpe > 1.0`, `PF > 1.3`, `MaxDD < 25%`, `Expectancy > 0`).
- It does not relax the Exchange-First invariant or the entry/exit gate separation.

### Controlled extension port for laddered exits

The single-target limitation is acknowledged as a **real architectural gap**, not a
constraint to design around. One bounded extension to the frozen execution layer is
authorized:

1. The extension is **strictly additive**. The existing single-stop / single-target path
   must remain byte-for-byte behaviorally identical, and that identity must be proven by
   regression tests, not asserted.
2. Laddered exits travel a **new** code path. Multi-leg reduce-only exits, partial-fill
   accounting, and per-leg reconciliation are new behavior requiring their own
   idempotency and reconciliation evidence.
3. The extension may be **built** on evidence of need but may only be **armed** for a
   candidate that has passed promotion. Building the port is not promotion.
4. Everything else in the chain stays frozen: the Binance adapter, execution intent,
   order/fill persistence, managed position, ownership status, recovery, emergency close,
   the launcher, the scheduler, and runtime state.
5. Protection prices continue to be resolved from the real `average_fill_price` after the
   exchange confirms the fill, never from a strategy reference price or an OHLCV trigger
   ([candidates.py:145-149](../../services/automated_trading/domain/candidates.py#L145-L149)).
   This holds per leg.

### Attribution discipline

Entry geometry and exit geometry must not change in the same evaluation step. Exit policy
is evaluated first, against entries already produced by the unchanged control lane, so
that any measured difference is attributable to the exit alone.

### Required evaluation metrics

Win rate alone is insufficient and has repeatedly been misleading in this repository.
Comparisons must report, per symbol, per direction, and per regime: trade count, net
expectancy, profit factor, average and median R, max drawdown, MFE, MAE, holding time,
fee drag, and **profit capture ratio** (`realized profit / MFE`), which distinguishes a
bad entry from a good entry exited too early. All comparisons are post-cost; nominal
RR is not evidence.

## Consequences

### Positive

- Exit geometry becomes falsifiable rather than a constant nobody can defend.
- Regime-appropriate exits become expressible: trend ladders for trends, boundary targets
  for ranges.
- Attribution survives, because entry and exit are evaluated independently.
- The frozen execution chain stops absorbing strategy-performance complaints. A losing
  strategy is a strategy problem, not a reason to touch verified plumbing.

### Negative

- The extension port adds real complexity to a currently-verified chain: partial fills,
  per-leg protection state, and multi-leg reconciliation are all new failure surfaces.
- Multi-leg exits increase order count and therefore fee drag, which must be measured
  post-cost rather than assumed negligible.
- Shadow evaluation of exits over already-taken entries cannot capture the fact that a
  different exit policy would have changed subsequent position availability. This is a
  known limitation of the method and must be stated in every report it produces.

### Mitigation

- The additive-only requirement is enforced by regression tests over the existing
  single-target path, not by reviewer diligence.
- Promotion continues to require the unchanged Validation Layer thresholds and adequate
  sample size, with explicit confidence intervals; intervals crossing zero are not
  promotion evidence.

## Verification

This ADR is a contract, not an implementation. Its verification obligations attach to the
work it authorizes:

- Any exit-policy comparison must state its sample size and whether confidence intervals
  cross zero.
- Any execution-layer extension must show the existing single-target regression suite
  passing unchanged, plus real Binance Testnet order IDs and fills for each new leg.
- Compliance with the ownership boundary is verified by tracing a real cycle and
  confirming that every price level originated in the strategy layer and every rejection
  originated in a hard limit.
