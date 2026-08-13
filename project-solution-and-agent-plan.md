# Project Solution And Agent Plan

**Project:** Crypto Quant Automated Trading / V2 Production Strategy Finalization
**Status:** `BLOCKED_EVIDENCE`
**Committed base:** `backup/2026-08-10-wip@46cf24d2b36c66906ffda96097e021bc920862ed`
**Effective working baseline:** committed base plus the current user-owned uncommitted worktree overlay.
**Scope:** plan and contract only. No business-code implementation, test edits, database writes, exchange orders, commit, push, branch, or worktree.

## 1. Baseline Lock

Read-only baseline commands and facts:

| Check | Result |
|---|---|
| `git status --short` | Large pre-existing user worktree overlay, including V2 authority restore, Runtime Truth, strategy adapter, and tests |
| `git branch --show-current` | `backup/2026-08-10-wip` |
| `git rev-parse HEAD` | `46cf24d2b36c66906ffda96097e021bc920862ed` |
| `git diff --stat` / `git diff --name-only` | Captures the current unstaged part of the user overlay; `git status --short` also captures staged and untracked overlay paths |
| `git log origin/backup/2026-08-10-wip..HEAD --oneline` | Empty: no local commits beyond origin |

`committed_base` is only the named Git commit. The V2 authority restore and associated Runtime Truth/strategy changes are observed in the **uncommitted worktree overlay**; this plan makes no claim that they are part of `HEAD`. The effective baseline is the exact tuple `(branch, committed_base HEAD, combined staged/unstaged/untracked overlay identity and path scope, origin delta)`. `PRE-000` must re-run the baseline commands, including staged scope checks, before any future implementation. Any non-mechanical change in any element of that tuple yields `BLOCKED_BASELINE`; it must not silently adopt a different overlay as the same baseline.

## 2. Active V2 Chain

The path observed in the effective working baseline (not asserted to be committed in `HEAD`) is:

`services/execution/v2_scheduler_entry.py::execute_v2_automated_trading_cycles`  → operator profile and immutable ConfigSnapshot → `resolve_production_authorization` → `services/automated_trading/application/production_strategy.py::evaluate_authorized_production_strategy` → `cycle_service.py::run_automated_trading_cycle` → `entry_service.py::evaluate_entry` → `entry_service.py::execute_entry` → Binance acknowledgement/fills → V2 fact persistence/protection/reconciliation.

When authorization is not `APPROVED`, production entry terminates as `NO_AUTHORIZED_PRODUCTION_STRATEGY`; sampling is not production authority. Existing positions still reconcile, recover, protect, and exit.

## 3. P-001 Risk Authority Map

| Rule | Classification | Current V2 source/consumer and timing | Semantics | Evidence |
|---|---|---|---|---|
| `risk_per_trade` | `SIZING_CONSTRAINT` | `operator_profile.py::resolve_v2_execution_settings` → `CycleRequest`; `cycle_service.py::_calculate_quantity` before intent | Converts stop distance to risk notional; capped, never raises risk | `tests/services/test_automated_trading_cycle.py` sizing cases |
| `max_symbol_exposure` / `max_position_fraction` | `SIZING_CONSTRAINT` | operator profile/tier → `_calculate_quantity` exposure ceiling | Floors requested notional to `equity * max_position_fraction` | same cycle sizing tests; tier tests |
| `max_leverage` | `SIZING_CONSTRAINT` | operator profile/tier → quantity ceiling and `execute_entry(... leverage=...)` | Caps margin capacity and submitted leverage | cycle/entry tests |
| `max_portfolio_initial_risk_fraction` | `DEAD_OR_UNUSED` in current V2 | Present in legacy bootstrap/Gatekeeper contracts; absent from `CycleRequest`, `EntryRuntimeContext`, and `_calculate_quantity` | No proven V2 enforcement; must not reconnect legacy Gatekeeper wholesale | legacy `gatekeeper.py` only |
| daily loss / drawdown | `DEAD_OR_UNUSED` in current V2 | Enforced by legacy `Gatekeeper`, `tasks.py`, and `paper_cycle_orchestrator.py`; no Active V2 consumer found | No proven V2 entry stop or exit behavior | legacy tests only |
| existing-position block | `ENTRY_HARD_GATE` and `EXIT_EXEMPTION` | `cycle_service.py` builds `open_position_symbols`; `entry_service.py::evaluate_entry` | Blocks new entry with `POSITION_ALREADY_OPEN`; reduce-only exit uses separate exit service | `tests/services/test_automated_trading_entry.py` |
| cooldown | `ENTRY_HARD_GATE` | `CycleRequest.symbol_cooldown_active` → `evaluate_entry` | Blocks opening only | entry tests; sampling service supplies cooldown signal |
| reconciliation block | `ENTRY_HARD_GATE` and `EXIT_EXEMPTION` | `reconcile` status and `entry_blocked_symbols` enter `EntryRuntimeContext` | `UNAVAILABLE`, `RECOVERY_REQUIRED`, quarantined symbol block new exposure; exits/recovery remain live | reconciliation/recovery tests |
| kill switch | `ENTRY_HARD_GATE` and `EXIT_EXEMPTION` | runtime control plus `entry_kill_switch_active` in `evaluate_entry` | Stops new entries only; does not suppress reduce-only exits | entry and exchange-truth contract tests |
| `min_notional` | `ENTRY_HARD_GATE` | `execute_entry` after step-size flooring, before exchange submission | Rejects zero/under-minimum quantity; no local projection | `tests/services/test_automated_trading_entry.py` |

**P-001 disposition:** `BLOCKED_EVIDENCE`, not yet `IMPLEMENT_REQUIRED`. The Active V2 path proves that these two portfolio stops have no current V2 consumer, but it does not identify a V2-native risk-contract owner for their values, accounting source, reset boundary, or hard-stop semantics. No implementation scope can therefore be frozen without inventing a second risk truth or reattaching legacy `Gatekeeper`, both of which are prohibited. The smallest missing evidence is a read-only mapping that identifies: (1) the immutable V2 ConfigSnapshot fields and source for `max_portfolio_initial_risk_fraction`, daily-loss, and drawdown values; (2) the V2 fact-table query that supplies portfolio initial risk, realized daily PnL, equity peak, and account equity; (3) the timezone/day-reset definition; and (4) the precise V2-native consumer symbol which produces the entry-only rejection while retaining reduce-only exit exemption. Frozen values remain `risk_per_trade=0.10`, `max_leverage=40`, `max_position_fraction=0.35`; this plan does not choose or modify any missing portfolio-stop value.

## 4. P-002 Runtime Truth Recovery

Current implementation is in `apps/api/routers/runtime.py`: `_exchange_cache`, `_exchange_inflight`, a single-worker executor, `_probe_exchange_truth`, `_ensure_exchange_probe`, and `_exchange_truth`.

Observed constants are `_EXCHANGE_CACHE_SECONDS=45`, `_EXCHANGE_BACKGROUND_REFRESH_SECONDS=20`, `_EXCHANGE_MAX_SERVE_SECONDS=90`, `_EXCHANGE_TRUTH_TIMEOUT_SECONDS=20`. These are not changed by this contract. They satisfy the required behavior in the current tests: successful cache reuse, stale fallback, explicit unavailable (never zero), one shared in-flight probe, 30-second polling-safe cache window, and post-timeout background recovery without cancellation.

**P-002 disposition:** `VERIFY_ONLY / ALREADY_SATISFIED`. E-002 adds regression protection only if independent review finds a missing edge. Any constant change requires measured latency evidence and a new operator-approved contract; do not mechanically apply 45→15 or 20→8.

## 5. P-003 B-Mode Competition

The live registry currently contains 10 IDs: `operator_heuristic_v1`, `trend_momentum_v1`, `trend_momentum_v2_enriched`, `trend_breakout_v1`, `pandas_ta_broad_screen_v1`, `operator_heuristic_v2_relaxed`, `trend_pullback_v1`, `failed_breakout_reversal_v1`, `trend_pullback_v2`, `range_sweep_reversion_v1`. `trend_pullback*`, `range*`, and `failed_breakout*` are `RESEARCH_ONLY`; `testnet_sampling_v2` is not in this registry and is permanently excluded from Production competition.

The repository supplies part, but not all, of a reproducible experiment contract. The verified common mechanics are: BTC/USDT and ETH/USDT only; 4h direction, 1h state, and 15m entry bars; closed-bar decision followed by next-bar-open fill; the candidate-registry 2R control; and the proposal walk-forward implementation's 12-month train, 3-month OOS, eight-window, 24-hour embargo configuration. Its current replay cost object defines taker fee 5 bps/side, spread 1 bps/side, latency slippage 1 bps/side, and partial-fill fraction 0.85. The cost object marks funding unavailable unless complete point-in-time funding observations are supplied, which blocks promotion rather than permitting a zero-funding assumption.

The remaining required input contract is **not frozen and is itself `BLOCKED_EVIDENCE`**. The current local data evidence records only about 12.7 months of BTC/ETH 15m/1h/4h history, whereas the existing eight-window implementation rejects an insufficient development range. The available P2-A artifact has only nine captured entries and cannot substitute for OOS candidate evidence. Before any Champion calculation, one immutable read-only evidence package must identify the exact market-data source and market type, bar/funding checksums, coverage start/end, train/OOS and holdout boundaries produced by the window builder, candidate rules hashes, fee/spread/latency/partial-fill/funding provenance, and the evaluation sizing convention. It must also provide candidate-by-symbol/window metric distributions for the incumbent and every eligible challenger, including natural signal/trade density.

Each symbol must independently pass net expectancy > 0, PF >= 1.30, Sharpe > 1.0, at least 30 OOS trades, MaxDD <= 20%, no critical data issue, and no single-window dependence. A challenger must improve at least two of expectancy, PF, Sharpe, and MaxDD. The requested numerical definition of “significant deterioration” cannot be frozen from the current repository: no comparable BTC/ETH OOS distribution exists. It is therefore a blocking output of the missing evidence package, not an invented 10%/0.10/2pp rule. The same applies to the quantitative minimum density ratio: it must be derived from the incumbent/challenger distribution and recorded with its rationale. No Champion replacement is permitted until these two derived values are evidence-bound.

The incumbent remains `trend_momentum_v2_enriched` unless these rules produce a clear Pareto challenger. Current evidence cannot do so: P2-A has only 9 real entries (BTC 5, ETH 4), and every business verdict is `INSUFFICIENT_SAMPLE`. Exit comparison therefore remains blocked. Once an entry champion is proven, fix entry timestamps/fills and compare only existing exit policies: ATR stop + fixed 2R control, ATR adaptive, structure invalidation, scale-out runner, and regime-aware. `C_STRUCTURE_INVALIDATION` is currently an ATR proxy, not validated structure recognition. Exit replacement requires improved net expectancy, no material PF/MaxDD deterioration, no unacceptable stuck/timeouts, no lookahead, and unchanged fill-based protection semantics. Otherwise fixed 2R remains.

**P-003 disposition:** `BLOCKED_EVIDENCE`. Read-only replay/walk-forward work is the only permitted next action. No production code task is created solely to manufacture a diff, and no authorization recommendation exists until the missing input, distribution, entry, and exit evidence is hash-bound.

## 6. Tasks And Gates

### PRE-000

Read-only re-lock of baseline and Active chain. Output `BLOCKED_BASELINE` on ambiguity.

### E-001 — V2 risk authority

`BLOCKED_EVIDENCE`. The two unproven V2 portfolio stops require the P-001 missing-evidence package before any file/symbol can be named. `services/automated_trading/application/cycle_service.py` and `entry_service.py` are Active-chain evidence locations, not an approved implementation allowlist. Legacy Gatekeeper remains untouched.

### E-002 — Runtime Truth recovery

`VERIFY_ONLY / ALREADY_SATISFIED`; maximum files are `apps/api/routers/runtime.py` and `tests/api/test_runtime_truth_api.py` only if a regression gap is found. No UI changes.

### E-003 — Production strategy and exit integration

`BLOCKED_EVIDENCE` pending the hash-bound competition input package and distribution-derived replacement/density rules. If a validated champion is later selected and the existing adapter cannot express it, a new Prompt 2 must create a separate 1–3 file implementation task naming exact symbols. Otherwise close as `VERIFY_ONLY` with `AUTHORIZATION_RECOMMENDED` only; manifest remains PENDING and operator approval is never synthesized.

### Independent Review

Read-only review after implementation tasks and before either external acceptance. It checks authority mapping, no legacy reattachment, evidence hashes, frozen values, and diff scope.

### EV-001 — Browser acceptance (independent)

After implementation and review only; code/test/validation diff must be empty during acceptance. Check seven pages at 900p/1080p, first fold, loading/empty/stale/unavailable/current, console, network frequency, probe recovery, and BTC/ETH side/quantity/status consistency. Any new UI bug becomes a new Prompt 1/2 issue; do not patch during acceptance.

### EV-002 — Natural Binance Testnet acceptance (independent)

Requires completed candidate evidence, independent review, and explicit operator approval. Accept only natural production signal → V2 gate → real order/fill → fill-based protection and correct reduce-only side → natural/SL/TP exit → V2 CLOSED → retired protection → exactly-once realized PnL → HEALTHY reconciliation. Sampling, forced, acceptance-only, threshold-lowered, or risk-disabled orders are invalid.

## 7. Contract Verdict

`BLOCKED_EVIDENCE`: P-001 lacks a V2-native ownership/accounting/reset contract for portfolio initial-risk and daily loss/drawdown enforcement. P-003 lacks a hash-bound BTC/ETH data/period/cost/sizing input package, the associated OOS and distribution evidence needed to derive replacement and density tolerances, and a promotable exit result. Browser and natural Testnet acceptance are intentionally separate future gates, not mixed into implementation.

## 8. Verification Record

- `pytest -q tests/api/test_runtime_truth_api.py tests/services/test_automated_trading_entry.py tests/services/test_automated_trading_cycle.py` → `69 passed, 2 warnings`.
- `pytest -q tests/test_candidate_registry.py tests/test_candidate_leaderboard.py` → `2 failed, 17 passed, 1 skipped`; both failures are pre-existing hard-coded registry count `9` versus actual `10`.
- `git diff --check` → PASS.
- `ruff check .` → FAIL, limited to three pre-existing unrelated script findings: `scripts/run_proposal_research_replay.py:326` (`B023` twice) and `scripts/verify_gate17_e2e.py:77` (`C416`); no change made.
- `mypy` → `Success: no issues found in 236 source files` (two informational `annotation-unchecked` notes).
- `pytest -q` → `1541 passed, 7 skipped, 2 failed`; both failures are the pre-existing hard-coded registry count assertions listed above. No test was edited.
- Browser/Testnet acceptance → NOT_RUN by plan-only scope and separate-gate contract.
