# P2 — Strategy & Exit Policy Evaluation (Frozen Scope)

- **Date:** 2026-08-11
- **Status:** Scope frozen. No implementation authorized by this document alone.
- **Governing contract:** [ADR-004](../../adr/ADR-004-strategy-owns-geometry-execution-owns-safety.md)
- **Predecessors, all `FROZEN_KNOWN_GOOD`:** V2 execution chain; P0-B active entry chain
  (commit `482c26c`); P1 same-cycle research shadow (commit `1a299ac`)

## Phase state

| Component | State |
|---|---|
| `AUTO_EXECUTION_CHAIN` | `FROZEN_KNOWN_GOOD` |
| `CURRENT_CONTROL` = `testnet_sampling_v2` | `KEEP_RUNNING_UNCHANGED` |
| `P1_RESEARCH_SHADOW` | `FROZEN_KNOWN_GOOD` |
| `NEXT_PHASE` | P2 — strategy & exit policy evaluation |

The question P2 answers is **not** "is 0.35% / 1.5R too tight". It is **"which exit
geometry, chosen by rule from market state, actually earns more after costs than the
current sampling geometry — and is the sample large enough to believe it."**

## P2-A — Exit policy shadow evaluation

**Goal.** Hold entries fixed, vary only the exit, and measure the difference.

**Method.** For every entry the unchanged control lane already produced, replay the
subsequent bars under each candidate exit policy. Real fills are never touched; nothing is
submitted to any exchange.

Policies to compare:

| ID | Exit policy |
|---|---|
| EXIT-A | Current control: `SL = max(1.2*ATR14, price*0.0035)`, `TP = 1.5R` (baseline) |
| EXIT-B | ATR-scaled stop and target, no percentage floor |
| EXIT-C | Structural stop (recent swing extreme ∓ ATR buffer) with structural target |
| EXIT-D | Trend ladder: 1R / 1.8R / runner, per `trend_pullback_v2` geometry |
| EXIT-E | Regime-aware: trend → EXIT-D, range → boundary targets |

**Required output**, split by symbol (BTC / ETH), direction (LONG / SHORT), and regime
(TREND / RANGE / EXPANSION): trade count, net expectancy, profit factor, win rate, average
R, median R, max drawdown, MFE, MAE, **profit capture ratio** (`realized profit / MFE`),
fee drag, holding time.

MFE/MAE precedent already exists in `services/validation/technical_replay.py` and
`scripts/export_trend_momentum_mae_mfe.py`; profit capture ratio is new.

**Implementation note.** The existing `scripts/compare_exit_policies_cli.py` and
`services/validation/technical_replay.py::compare_exit_policies` are bound to the legacy
4h/1h/15m technical pipeline and its `AUTO_PAPER_TECHNICAL_RULES` entry side. They do not
cover the `testnet_sampling_v2` lane that actually trades. P2-A therefore needs either a
V2-side evaluator or an explicit extension of the existing one — decide with evidence,
and do not "sync" the frozen `FROZEN_2026_07_12_TECHNICAL_RULES` block, whose purpose is
audit reproducibility.

**Stated limitation, mandatory in every report.** Replaying alternative exits over
already-taken entries cannot capture the fact that a different exit would have changed
position availability, and therefore which later entries were reachable. P2-A ranks exit
geometry; it does not simulate a full alternative history.

**Sample-size discipline.** State the sample size and the confidence interval. An interval
crossing zero is not promotion evidence. 35–65 trades is not sufficient to conclude a
strategy is effective.

## P2-B — Entry strategy comparison

Uses the P1 same-cycle shadow already in production: control and research candidates are
evaluated on the same bar, from the same market context, with only the control submitting
orders.

- `testnet_sampling_v2` (control, submits)
- `trend_pullback_v2` (shadow)
- `range_sweep_reversion_v1` (shadow)
- `failed_breakout_reversal_v1` (shadow)

Comparisons must be same-bar. Comparing across different time windows is invalid.

## P2-C — Promotion

Reached only when a research candidate beats the control on post-cost metrics with an
adequate sample and intervals that do not cross zero, under the unchanged Validation Layer
thresholds.

**Blocked by a known architectural gap.** `TradeCandidate` carries one `stop_distance` and
one `take_profit_distance`
([candidates.py:81-82](../../../services/automated_trading/domain/candidates.py#L81-L82));
`StrategyProposal` carries a multi-leg `targets` tuple summing to 1
([proposals.py:44](../../../services/strategy_library/proposals.py#L44)); protection submits
a single reduce-only leg
([protection_service.py:401](../../../services/automated_trading/application/protection_service.py#L401));
and no `StrategyProposal` → `TradeCandidate` adapter exists. Promoting a laddered candidate
requires the ADR-004 clause-9 extension port first. Collapsing a ladder to one target at
promotion time is explicitly not an acceptable workaround — it discards the runner leg that
motivates the work.

**Do not mistake `services/strategy_library/adapters/quantdinger.py:243` for that adapter.**
It converts an external QuantDinger *signal* — which itself carries only a single
`stop_loss_pct` / `take_profit_pct` — into a `SHADOW` / `RESEARCH` non-promotable candidate
per ADR-079. It never sees a `StrategyProposal` and never carries a `targets` tuple, so it is
further confirmation that `TradeCandidate` is structurally single-target, not a counterexample.

## Freeze lists

### MUST_NOT_CHANGE

- `services/automated_trading/infrastructure/binance_adapter.py`
- Execution intent, order persistence, fill handling, managed position, ownership status
- Protection submission and the fill-price-derived protection resolution
- Reconciliation, recovery, emergency close, ghost/duplicate guards
- Launcher, scheduler, `services/execution/runtime_state.py`, entry authority
- P1 same-cycle shadow plumbing
- `testnet_sampling_v2` entry rules, stop formula, target multiple, and current
  position/leverage/gate settings
- Validation Layer promotion thresholds
- The frozen legacy audit-reproduction rules block in `compare_exit_policies_cli.py`
- Frozen files listed under "Legacy Pipeline Freeze" in `AGENTS.md`

### MAY_CHANGE

- New read-only evaluation/replay code and its tests
- New evidence artifacts and audit reports
- Metric computation helpers (MFE / MAE / profit capture ratio)
- Documentation and memory files

### MUST_CHANGE

- Nothing in production strategy or execution code in P2-A.

P2-A is read / replay / analyze / evidence only. Code implementation and real external
acceptance stay separate steps: the ADR-004 clause-9 extension port is authorized to be
built when evidence justifies it, and is a distinct task from evaluation, with its own
regression proof and real Binance Testnet evidence per leg.

### Out of scope for this phase

Adding symbols beyond BTC/ETH, mainnet, changing risk/leverage/exposure limits, and
relaxing any gate.

## Anti-goal

P2 must not conclude with "0.35% became 0.7%" or "1.5R became 2R". That is parameter
substitution — a different constant, not a strategy. The deliverable is evidence about
**which rule should own the geometry**, and rules that lose should be recorded as losing.
