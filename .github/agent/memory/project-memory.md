# Project Memory

## Runtime / Review scope closeout (2026-08-21)

- VERIFIED: the one-click wrapper intentionally starts `v2_active` with
  `TESTNET_CANARY`; direct `launch-paper-console.ps1` calls retain `v2_shadow`
  unless `-EnableNaturalTestnet` is passed. This is a contract clarification,
  not a trading-path change.
- VERIFIED: ReviewService now derives automatic performance scope from the
  canonical active manifest (`BTC/USDT`, `ETH/USDT`) and labels SOL/XRP/BNB as
  research-only, preventing out-of-scope Canary history from contaminating
  execution PnL or worst-performer ranking.
- VERIFIED: `scripts/audit_runtime_incidents.py` is read-only and classifies
  unresolved incidents by current managed exposure versus historical evidence;
  it never mutates the immutable incident status column. Current-intent
  detection reads only pre-fill intent states, so closed `FILLED` history does
  not appear as active exposure.

## P0 Runtime Reliability and V2 freeze (2026-08-19)

- VERIFIED: `df10d57d47228d673b8cf6c627819ce6633e95af` remains the local and remote
  `backup/2026-08-10-wip` baseline.
- VERIFIED: stale exchange account snapshots are still displayable in the admin
  console, but are explicitly marked stale and never grant new-entry authority.
- VERIFIED: no-trade summary no longer promotes a degraded reconciliation status
  without blockers into `RECONCILIATION_BLOCKED`; `NO_AUTHORIZED_PRODUCTION_STRATEGY`
  remains visible when reconciliation has no entry blockers.
- VERIFIED: one-click launcher produced `SUCCESS / STARTUP_READY`, started the
  isolated research worker, and preserved `ENTRY_PAUSED / NONE / PENDING`.
- VERIFIED: `/ops` browser evidence showed Runtime Truth, entry pause explanation,
  stale-data labeling, 30s polling, and no console errors.
- VERIFIED: the durable browser verifier now targets `/ops`, checks the actual
  `/api/v1/runtime/*` contract, and passes its full checklist including polling.
- VERIFIED: `contracts/v2_transaction_contract.json` and
  `scripts/verify_v2_transaction_contract.py` freeze the V2 transaction hot path;
  ordinary staged edits fail with `V2_HOTPATH_CHANGE_REQUIRES_EXPLICIT_APPROVAL`.
- BLOCKED_BY_ENV: real Testnet contract execution was refused because
  `V2_TESTNET_CONTRACT_ENABLED` and Binance credentials were not present; no real
  order was attempted.

## Autonomous recovery loop closeout boundary (2026-08-19)

- ACTIVE startup recovery is verified: launcher `STARTUP_READY`; scheduler is
  `ACTIVE/BINANCE_TESTNET`, `TESTNET_CANARY`, `testnet_sampling_v2`, five-symbol
  scope BTC/ETH/SOL/XRP/BNB, `entry_authorized=true`, and no startup contract
  errors.
- The authorized BTC manual baseline lifecycle was refreshed from
  `short 0.5346` to flat after confirming no unresolved V2 intents, managed
  positions, unknown orders, or unprojected fills. This is a symbol-scoped
  acknowledgement, not a global runtime failure.
- A bounded read-only natural observation ran for 60 minutes / 236 cycles and
  produced no natural entry. Evidence is
  `docs/evidence/automated_trading_v2/natural_cycle_20260818T190030Z.json` and
  `.local/final-auto-trading-loop-state.json` now records
  `ACTIVE_NATURAL_ENTRY_NOT_OBSERVED`.
- Therefore Runtime Deployment is operational, but the real
  `Intent -> Exchange Order -> Fill -> Protection -> Reduce-only Exit` proof is
  still pending. Do not treat this observation as a completed trading
  lifecycle and do not relax the sampling strategy to manufacture one.

## Natural Testnet final proof (2026-08-20)

- VERIFIED: V2 Active natural Testnet proof now reads the authoritative V2
  intent/order/fill tables and captured XRP/USDT entry order `3481239947`,
  trade `148671953`, quantity `10176.5`, average fill `0.9982`, with
  `acceptance_or_manual_order=false`.
- VERIFIED: the same natural lifecycle created protection orders
  `1000000172235239`/`1000000172235260`, then a reduce-only stop order
  `3481336578` / trade `148717959`; local position is `CLOSED` and latest
  reconciliation is `HEALTHY` with exchange/local open positions `0/0`.
- VERIFIED: ordinary active restart clears process Natural mode and restores
  `entry_enabled=true`, `entry_authority=NONE`, `entry_authorized=false`,
  `ENTRY_PAUSED`, and `Production=PENDING`.

## Testnet Canary runtime contract (2026-08-18)

- The authoritative Binance Testnet Canary sampling contract is now the exact
  five-symbol scope BTC/ETH/SOL/XRP/BNB with risk `0.10`, leverage `30x`, target
  and maximum margin `0.05`, per-symbol notional `1.50x`, maximum five positions,
  and aggregate notional `7.50x` equity.
- The contract is applied after authority resolution and the scheduler explicitly
  binds the `TESTNET_SAMPLING` lane. E-003, volatility shock, R2 and E-004 remain
  diagnostic only in this scope; exchange/data/reconciliation/runtime safety stays
  blocking. Existing positions are grandfathered and are never resized or closed
  by this configuration change.
- `AutoTradingSettings` defaults and validation, bootstrap/apply profile, frontend
  display, and contract tests are synchronized. Existing exchange mark notional is
  counted against the aggregate new-entry ceiling.

## Testnet Canary continuity and visible entry authority (2026-08-12)

- V2 now resolves exactly one entry authority per cycle: `PRODUCTION` for an
  approved immutable production authorization, otherwise `TESTNET_CANARY`
  only in `BINANCE_TESTNET` when the operator's existing sampling opt-in is
  enabled, otherwise `NONE`. Canary retains `SAMPLING/non_promotable` and
  cannot contribute to production authorization or promotion evidence.
- Scheduler Runtime Truth now carries `entry_authority`, `entry_authorized`,
  `entry_authority_reason`, production authorization state, active strategy,
  promotion eligibility, and `TRADING`/`ENTRY_PAUSED`; the transaction desk
  renders these separately from reconciliation/protection/reduce-only exits.
- Natural recovery evidence after official launcher: scheduler state reported
  `ACTIVE/BINANCE_TESTNET/TESTNET_CANARY`, `entry_authorized=true`,
  `production=PENDING`, `promotion_eligible=false`, and a natural Canary
  entry persisted V2 intent `ea23a85f-abb6-47b8-b17c-4d37178a9f54`, position
  `9ead2ee8-b88e-4dae-a654-c67082f53773`, Binance fill trade `313315568`.
  Do not classify this as production evidence or force its exit.

## V2 entry authority paused pending approved production evidence (2026-08-12)

- The one-click V2 ACTIVE lane remains the sole Testnet writer, but it no longer grants
  `testnet_sampling_v2` entry authority. Missing, stale, mismatched, or pending production
  authorization terminates new entries as `NO_AUTHORIZED_PRODUCTION_STRATEGY`; reconciliation,
  protection, recovery, and reduce-only exit processing continue.
- `docs/evidence/active-manifests/auto_paper_mature_templates.json` schema v3 now carries the
  required production authorization binding: candidate/version, rules hash, immutable active
  ConfigSnapshot hash, exact BTC/ETH scope, validation reference, approval identity/time and
  `APPROVED` state. The checked-in record is deliberately `PENDING`; `trend_momentum_v2_enriched`
  remains research, not an approved production strategy.
- `services/automated_trading/application/production_strategy.py` is a pure adapter from the
  existing 4h/1h/15m `DecisionPipeline` to a V2 `PRODUCTION`/`PRIMARY` candidate. It preserves
  source trace and ATR-derived configured 2R geometry; V2 still resolves absolute protection
  prices from Binance confirmed average fill. Research proposals and multi-leg exits remain
  shadow-only.
- Runtime Truth now reads V2 managed positions/orders/fills/protections and ignores legacy
  position/order projections for ACTIVE reconciliation. Closed and quarantined V2 positions are
  not compared as open positions.

## Geometry ownership frozen; P2 is strategy/exit evaluation (2026-08-11)

- Operator authorized a Layer-1 invariant change: **strategy owns geometry, execution owns
  safety**. See `AGENTS.md` §"Strategy Owns Geometry, Execution Owns Safety",
  `docs/adr/ADR-004-strategy-owns-geometry-execution-owns-safety.md`, and ADR-080 in
  decisions-log. Scope for the next phase:
  `docs/superpowers/plans/2026-08-11-p2-strategy-exit-policy-frozen-scope.md`.
- Verified facts behind the decision: the lane that actually submits is `testnet_sampling_v2`,
  whose **entry is genuinely quantitative** (EMA50 + MACD hist + RSI band + ATR14>0 on closed
  15m bars) but whose **exit is effectively hardcoded** — `stop = max(1.2*ATR14, price*0.0035)`,
  `TP = 1.5*stop`. When `1.2*ATR14` < 0.35% the floor wins and geometry degenerates to fixed
  SL 0.35% / TP 0.525% / RR 1.5 for every symbol, direction and regime. Both BTC and ETH
  entries on 2026-08-10 hit exactly that floor — the narrow band was the floor, not ATR.
- **Architectural gap that blocks laddered promotion:** `TradeCandidate` accepts one
  `stop_distance` + one `take_profit_distance`
  (`services/automated_trading/domain/candidates.py:81-82`), but `StrategyProposal` carries a
  `targets` tuple whose `quantity_fraction` must sum to 1
  (`services/strategy_library/proposals.py:44,56-58`), and protection submits a single
  reduce-only leg (`.../application/protection_service.py:401`). **No `StrategyProposal` →
  `TradeCandidate` adapter exists anywhere** in `services/automated_trading/` or
  `services/execution/`. A strictly-additive extension port is authorized; collapsing a ladder
  to one target at promotion time is explicitly not an acceptable workaround.
- Research candidate geometry, verified: `trend_pullback_v2` = structural stop
  (pullback extreme ∓ 0.25*ATR) with 1R/1.8R/2.5R at **35/40/25**;
  `range_sweep_reversion_v1` = sweep extreme ∓ 0.25*ATR with midpoint/opposite-boundary/runner
  at **40/40/20** (not 35/40/25 — that split belongs to trend_pullback).
- Protection already resolves from real `average_fill_price` post-fill
  (`candidates.py:145-149`) — this was already correct, no change needed.
- **P2-A tooling caveat:** existing `scripts/compare_exit_policies_cli.py` +
  `services/validation/technical_replay.py::compare_exit_policies` are bound to the legacy
  4h/1h/15m `AUTO_PAPER_TECHNICAL_RULES` pipeline and do **not** cover the
  `testnet_sampling_v2` lane that actually trades. MFE/MAE helpers exist in
  `technical_replay.py` and `scripts/export_trend_momentum_mae_mfe.py`; profit capture ratio
  (`realized profit / MFE`) does not exist yet.
- Anti-goal recorded: P2 must not end as "0.35% → 0.7%" or "1.5R → 2R". Parameter substitution
  is not a strategy improvement.

# QuantDinger isolated source runtime evidence (2026-08-05, refreshed)

- The Shadow bridge now has an explicit bounded child-process runtime and CLI
  artifact path. API/Scheduler processes never execute external source; the
  child receives historical bars and signal-capture APIs only, with no imports,
  credentials, exchange clients, databases, positions, or order writers.
- Real development-window evidence: BTC/USDT 15m Binance Vision history from
  2025-01-01 through 2025-01-08 (320 bars) generated artifact hash
  `41025a9fbeb963fec18229dabcbd5944267eae8574ce1f9864f5b5ce5456a169`, 1
  de-duplicated signal and 1 next-bar replay trade; 145 duplicate-target
  events were rejected. Artifact parsing passed and the replay exited by
  `takeprofit`. The refreshed differential report has 0 local trades and 1
  unmatched external trade because no authoritative local replay payload was
  preserved for this rerun; it is explicitly `NOT_PROMOTION_EVIDENCE`.
- This is a parity diagnostic only. The external source remains Shadow-only,
  active-manifest promotion is blocked, and no Binance/Testnet write occurred.

## Gate 16 preserves manual Testnet baseline (2026-08-04)

- `scripts/verify_automated_trading_testnet_contract.py` no longer requires an empty BTC account. It snapshots the operator-owned position and open orders, adds the contract increment in the same direction for Binance one-way mode, and restores the exact baseline instead of flattening the symbol.
- Real proof: baseline `BTC short 0.0105`; entry `27941708499` / trade `524311879` for `0.0008`; stop/target `1000000155928815` / `1000000155928828`; exit `27941708544` / trade `524311888`; final baseline `short 0.0105`, new open orders `0`.
- The active directional PaperRun now has `allow_entry_with_unmanaged_positions=true`. Manual/external positions remain unmanaged and excluded from strategy attribution; this flag does not adopt them.
- Evidence is `TESTNET_CONTRACT`, `natural_strategy=false`; it does not replace natural Scheduler strategy evidence.

## Strategy core refactor Gate 0/1 baseline (updated 2026-07-30)

- Current-folder audit is frozen at `docs/audits/2026-07-29-strategy-refactor-audit.md`: V2 actually runs `testnet_sampling_v2` on 15m and emits V2 `TradeCandidate`; the legacy active manifest still points to `trend_momentum_v1` on the 4h/1h/15m path.
- Golden Baseline generator is `scripts/generate_strategy_golden_baseline.py`; it hashes current paths+contents, current active rules, and BTC/ETH 1m/5m/15m/1h/4h data, then refuses overwrite.
- Independent review found the first tree-hash scope omitted `docs/**` and became stale after a memory commit. Commit `bf3b0b7` corrected the scope to all source/config/document files under the current folder, explicitly excluding generated artifacts, caches, dependencies, and mutable runtime logs; the frozen hash must equal a fresh recomputation before delivery.
- Historical backfill now covers BTC/ETH continuously from 2023-01-01 through the 2026-07-29 cutoff: per symbol 1m=1,879,200; 5m=375,840; 15m=125,280; 1h=31,320; 4h=7,830, with zero gaps/missing bars. Provenance is `artifacts/strategy_refactor/history-provenance-2026-07-29.json`.
- The corrected immutable baseline is `artifacts/strategy_refactor/baseline-20260729-0000Z-r4`. It freezes a 2026-01-29..2026-07-29 Final Holdout without reading it. The original directory is superseded because its pretty-printed `trades.jsonl` was not valid line-delimited JSON; `r1` captured a pre-sync source tree; `r2` was invalidated by concurrent research-candidate commits; `r3` preceded the generated pytest-state refresh. All remain immutable evidence.
- Data coverage is `SUFFICIENT`, but `trend_momentum_v1` is rejected: portfolio 1,126 trades, Sharpe -0.1096, PF 0.9882, MaxDD 77.14%, net expectancy -0.000202 and net return -22.71%. The 90% IID bootstrap intervals cross zero and are not promotion eligible.
- The next approved step is Phase 1 data/feature parity and next-bar semantics, followed by point-in-time costs and per-window walk-forward/dependent bootstrap. Do not tune MACD/RSI, risk, stops or promotion gates to rescue the failed candidate, and do not use the sealed Holdout for optimization.

## Testnet truth / P0.3 Entry-slot latency (2026-07-28)

- Armed Testnet Entry runs only in coordinated lease `paper_runtime_cycle` (`slot=tight_entry`). Observation/local auto-runs use separate lease `paper_observation_cycle` at `paper_observation_cycle_offset_seconds=90`.
- `paper_runtime_cycle_offset_seconds=5` so submit can finish inside `pretrade_max_decision_age_seconds=75` measured from closed 15m candle.
- Active ConfigSnapshot can still carry `tradable_status=unknown` after `exchange_info_refresh` updated the PaperRun row; cycle load merges run-row trading assets via `_merge_runtime_execution_profile`.
- P0.3 natural fill not yet observed: recent bars correctly end `SAMPLING_RULES_NOT_ALIGNED`. Do not relax RSI/EMA/MACD or net-edge to manufacture fills.

## Pre-commit unblock for scripts (2026-07-26)

- Commit `ca88e24` ("6") pushed after fixing mypy/ruff in `account_equity.py` / `tasks.py` / `cross_sectional_replay.py` and adding ADR-077 `scripts/*.py` per-file-ignores for style noise.
- If `ruff-format` fails a commit: it auto-fixed files — run `git add -A` then `git commit` again (do not `--no-verify`). Then `git push`.
- Accidental empty file `=` must not be committed (removed in this push).

## ULTRA-AGGRESSIVE Paper testing configuration v2 deployed (2026-07-15)

- **Context**: After initial 46% threshold optimization produced insufficient order flow, user explicitly requested further loosening: "风险可以在提升...日亏损到20%...以尽可能多地找到符合买卖点，拿到交易数据为主" (Risk can be increased further, daily loss to 20%, prioritize finding as many trade opportunities as possible and getting data). User made clear this is **Paper/Testnet simulation environment** where order generation and data collection are the core goals.
- **Final configuration (ADR-065 v2)**: (1) MetaLabel threshold: 42% (42% × 2R = 0.84 expectancy, below breakeven but acceptable for Paper sampling); (2) Single trade risk: 5% (was 2.5%); (3) Max symbol exposure: 35% (was 20%); (4) Max total exposure: 90% (was 60%); (5) Max leverage: 40x for directional / 30x for swing (was 25x/15x); (6) Daily loss limit: 20% (was 6%); (7) Max open positions: 10 (was 6); (8) Universe: reduced from Top20 to Top10 majors (BTC/ETH/SOL/XRP/BNB/DOGE/ADA/LINK/AVAX/TRX) for higher single-symbol concentration and clearer trends.
- **Expected outcome**: 5-20 orders/day (vs 0 orders/day at 50% threshold). Goal is to collect 100+ real trades within 7-14 days to measure observed vs predicted win rate, then recalibrate based on real data rather than historical proxies.
- **Critical safety boundary**: This configuration is **TESTNET-ONLY**, absolutely forbidden for live trading. All changes documented in `docs/optimization/2026-07-15-ultra-aggressive-final-config.md`.
- **Evaluation checkpoints**: (1) 2026-07-22 (7-day): assess actual win rate vs 42% prediction, review daily order flow and P&L; (2) 2026-07-29 (14-day): statistical significance assessment with 100+ trades, decision on whether to recalibrate MetaLabel threshold or investigate deeper structural issues.
- **Honest acknowledgment per ADR-063**: Ultra-aggressive settings maximize sampling speed but do NOT manufacture positive edge where none exists. If observed post-cost expectancy remains negative after 100+ real trades, indicates signal quality / cost model / market regime issues requiring fundamental strategy revision (volatility breakout, on-chain data, sentiment) rather than further threshold loosening.
- Next steps: (1) Run `一键启动.cmd` to start Paper testing with new config; (2) Monitor first order generation within 24 hours; (3) Daily check of rejection-reason distribution and order flow; (4) 7-day checkpoint on 2026-07-22.

## Auto open/close remediation plan closed (2026-07-14)

- P0: data_stale dedup confirmed; equity sync from Testnet snapshots; exposure rejections root-caused as uncapped leverage sizing (not ghost positions) and capped; Top20 completeness audit all 20 symbols ≥61 bars.
- P1: edge-stats ran (17 trades <30 → rejected, proxy remains); chan annotation template at `docs/templates/chan-annotation-template.csv` (operator input still required); 4h/15m signal subset split wired.
- P2: `compare_exit_policies` framework already present + verified.
- Honest limit: fixes restore correct automatic opens when gates pass; they do not invent positive edge. Restart Paper console after pull so cycles pick up equity sync + sizing cap.


## Stuck-loading repair + honest real-data edge audit closes with "no profitable shape found yet, don't force fills" (2026-07-14)

- User reported the desk stuck on "加载中/连接中" after a restart and zero Binance orders all day, then explicitly said: "只想要它能开上单并且盈利的概率会大一点...现在半天不开单，开单亏得比赚的多，肯定是不行的" — i.e. both extremes (never firing, and firing on bad signals) are unacceptable, and asked me to find or synthesize a real solution rather than just tune thresholds. See `[TASK-059]` in task-history.md and ADR-063 for full technical detail; this entry is the load-bearing summary.
- **Stuck-loading**: root cause was an orphan API process (started outside `launch-paper-console.ps1`/`一键启动.cmd`, e.g. a bare terminal `nohup`) that never got `POSTGRES_URL` pointed at local SQLite, so it silently used the docker-compose default `...@timescaledb:5432/...` which cannot resolve outside the docker network — every DB-touching endpoint 500'd while `/health` (DB-free) looked fine. Fixed by killing orphan/duplicate processes and re-running the standard launcher.
- **The substantive ask required real research, not code tweaking.** Before touching any threshold, pulled real data to check whether any of the three strategy shapes this repo has code for (directional 8-indicator ensemble, single-symbol funding carry, cross-sectional funding carry) actually has positive edge right now: (1) directional — already-existing 90-day Top20 replay shows OOS net expectancy -0.23%, confirmed still the honest state; (2) single-symbol carry — pulled REAL Binance mainnet (not the platform's own testnet-sourced persisted data, which was found to be flat/non-representative for 8/20 symbols) funding history, 60 days: real funding is 0.3-1bps/8h, nowhere near a 4-16bps round-trip cost; (3) cross-sectional carry (rank Top20 by funding, long/short baskets) — real mainnet data, 298 real 8h settlement windows, 17.4% win rate, -46.7% cumulative. **None of the three clear real transaction costs in the current regime.** This was reported to the user directly and honestly rather than manufacturing a fix; the user chose (via AskUserQuestion) to close out only the affirmatively-correct, already-real-data-validated items rather than force any threshold change or invest in a full cross-sectional backtest engine for a shape already shown edge-negative.
- **What was actually fixed**: (1) ExitLadder default reverted to fixed-2R in `AUTO_PAPER_TECHNICAL_RULES` — a prior session's own real replay (`docs/audits/2026-07-12-exitladder-replay-comparison.md`) had already proven ExitLadder strictly worse (net expectancy -0.087% vs +0.219%, worse PF, worse max DD) but the bootstrap default still shipped it; this was a pure regression fix. (2) The `net_edge_after_cost` gate's win_rate/avg-win/avg-loss inputs were a raw-bar-return noise proxy (`_meta_label_samples()`: last ~47 bars' close-to-close return in the fused direction, independent of whether the signal ever actually fired) dressed up as an edge measurement — added `services/execution/signal_edge_stats.py` + `scripts/compute_signal_edge_stats.py`, reusing the existing `TechnicalStrategyValidationService` replay engine to compute a REAL trade-conditioned edge estimate (gated on >=30 real trade samples), with `decision_pipeline.py::_edge_stats_for_gate()` preferring it when fresh and falling back to the proxy otherwise (same fail-closed pattern as the existing MetaLabel model loader). This does not change the sign of the current negative-edge finding by itself — it makes the gate's future judgments based on real historical outcomes instead of noise, once an operator runs the compute script against production history.
- **This is the second consecutive session (after ADR-060's MetaLabel training gate correctly rejecting on real OOS-AUC failure) where the honest, evidence-based answer was "this doesn't work yet, don't fake it."** Per AGENTS.md non-negotiables 1/2/6, user urgency to see fills is explicitly not evidence of positive edge; no strategy lane was force-armed or had its net-edge/cost thresholds relaxed to manufacture orders.
- Verification: full `pytest -q -m "not integration"` -> 353 passed, 2 deselected (5 new edge-stats-artifact tests + 3 new gate-fallback tests); ruff/mypy clean on all touched/new files; `git diff --check` clean. All real-data research (Binance mainnet funding-rate pulls, local SQLite funding-dispersion checks) used disposable scratch scripts deleted after extracting findings — no permanent code changes were made from assumption; every number reported to the user was independently reproduced from real API/DB queries in this session.

## Live paper-trading underperformance root-caused + Sections 一/三 implemented (2026-07-13)

- User reported two rounds of live auto open/close trading were "非常差" (very poor) and asked to diagnose/fix the live system while also implementing Sections 一 (cross-sectional funding-rate carry) and 三 (MetaLabel training upgrade) from the LLM-integration remediation proposal — explicitly deferring 二/四 (need new credentials) and 五 (needs more real failure samples). See `[TASK-058]` in task-history.md and `ADR-060/061/062` in decisions-log.md for full detail; this entry is the load-bearing summary.
- **Live diagnosis (not signal quality — only 6 filled trades existed across both lanes, too few to judge edge):** the API+scheduler processes were running since that morning, predating both today's ADR-059 LLM fix and a fresh `.env` OpenRouter key — restarted both. `net_edge_after_cost_negative` was rejecting nearly all candidates because `bootstrap.py`'s fee assumptions (10-18bps one-way) were 2-4x real Binance USDM rates (maker 2bps/taker 5bps) — recalibrated to 5bps. Found and fixed a **new** LLM-chain bug in the same class ADR-059 named: OpenRouter's free reasoning models can burn the whole `max_tokens` budget on hidden reasoning, then dump that incomplete trace into `content` on `finish_reason="length"` — fixed with `reasoning: {exclude: true}`, plus hardened the fallback chain to survive a malformed-JSON `ValueError` from one candidate instead of aborting entirely. Confirmed the TASK-053 reduceOnly `-2022` bug has had zero recurrences since its 2026-07-12 fix (no action needed). Updated `AGENTS.md` with an explicit "current Paper-validation-phase risk baseline" section per user instruction, documenting the 15% risk cap + recalibrated fees as *current* policy, not permanent.
- **Section 一 (cross-sectional funding-rate carry):** new `services/execution/cross_sectional.py` ranks the scanned universe by funding rate every cycle; wired into `paper_signal.py`/`paper_runtime.py` as a new `strategy_lane="cross_sectional_carry"` with `rank_dropout` closes. Registered as a **disabled research strategy** (`bootstrap_cross_sectional_carry_strategy()`, `paper_status=NOT_STARTED`) — per AGENTS.md 1/2/6, a brand-new strategy shape needs backtest/OOS evidence before auto-arming, and that dedicated cross-sectional replay engine is not yet built (tracked as a limit, not silently skipped).
- **Section 三 (MetaLabel training upgrade):** added `scikit-learn`/`joblib`; new `services/strategy_library/meta_label_model.py` (shared deterministic feature extraction for training+inference, fail-closed model loading); `create_meta_label()` now optionally substitutes a trained model's win-probability, rule-based path remains the permanent fallback; new `scripts/train_meta_label_model.py` reconstructs samples from persisted OHLCV with a strict time-ordered (non-shuffled) walk-forward split and a `0.55` OOS-AUC gate. **Ran it for real** against the live paper-console DB: both 15m (6956 samples, AUC 0.5435) and 4h (2145 samples, AUC 0.4720) timeframes correctly rejected — no model artifact exists, `create_meta_label` still uses the rule-based path. This is the honest expected outcome given ~1 week of live history and the already-documented weak backtest edge, not something to route around.
- Verification: full `pytest -q` → 345 passed, 2 skipped (321 baseline + 24 new); full `mypy` clean (126 files); `ruff check .` clean on all touched files; live-verified via real restart + 3 genuine end-to-end OpenRouter veto successes post-fix, remaining failures are free-tier `429`s and the already-known GitHub Models token-scope gap, not new defects.

## Desk ↔ Binance Testnet sync clarified and hardened (2026-07-13)

- User reported "orders/positions not synced with Binance" while comparing the Paper console (BTC selected) against a Futures UI showing ETH chart + BTC/SOL/LINK fills. Live probe proved the platform **was already connected** to Binance Futures **Testnet** (`execution_ready=true`, `LIVE_TRADING_ENABLED=false`): open position `ETH long 0.055 @ ~1804.27`, and the same BTC/SOL/LINK fills the operator saw on the exchange UI. Apparent "not synced" was UX/misdirection, not a dead gateway.
- Fixes: (1) account probe now returns the correct `web_ui_url` for the active backend (`testnet.binancefuture.com` when mode/backend is testnet), with an explicit **mainnet never syncs** warning; (2) recent-order scan always includes BTC/ETH/SOL/LINK majors so the Orders tab matches multi-symbol Testnet history; (3) console banner/hero copy no longer points operators at `demo.binance.com` when API is on Testnet; (4) `ExchangePositionHint` jumps to the open exchange symbol when the chart is on another pair; (5) local `.env` `BINANCE_TRADING_MODE=testnet` so CN demo-fapi 451 fallback is not the default path.
- Ops note: API on `:8016` must be launched with launcher `POSTGRES_URL=sqlite:///.local_paper_console.db` — a bare restart against default Postgres makes `/binance-testnet-account` 500 while `/health` still looks fine.
- Verification: live account probe connected; desk expected positions=1 / orders=10; frontend `TradingConsolePanels` + `PaperConsole.deskSync` 17 passed; `ruff` + `tests/services/test_binance_gateway.py` (+ network/risk) 17 passed. Mainnet remains hard-disabled.

## Section 0 closure: LLM decision-veto chain silent failure diagnosed and fixed (2026-07-13)

- Independent task line from the Phase 0-5 remediation series below — user scoped this strictly to "Section 0" (diagnose why the LLM-assisted decision-veto chain `decision_veto_agent`/`pre_execution_veto_llm` was silently never invoked, then validate the fix with free-tier credentials via the SQLite test harness). Sections 一~五 (cross-sectional strategy, adversarial review gateway, MetaLabel training upgrade, RAG vectorization, Review Agent LLM attribution) remain out of scope until explicitly requested.
- Root causes found and fixed: (1) `shared/config.py::claude_api_key` only read `CLAUDE_API_KEY`, not the `ANTHROPIC_API_KEY` the user actually had configured — fixed with `AliasChoices`. (2) `services/agents/llm_factory.py::build_configured_llm_runtime()` fell back to `UnavailableLLMRuntime` with zero logging when no provider was configured — added structured logs for both the no-provider and configured-runtime paths. (3) `services/agents/service.py::_execute_llm_veto` used one blanket `except Exception`, collapsing timeout/provider-unavailable/unknown failures into an indistinguishable generic error — split into `TimeoutError`/`RuntimeError` (incl. `LLMProviderUnavailable`)/`Exception` branches, each recording a distinct failure reason. (4) Added `GET /agents/llm-status` (`apps/api/routers/agents.py`) so the effective provider chain state is queryable without log spelunking. (5) First-failure notification confirmed already correctly in place from a prior session (re-verified via Read, unchanged).
- Approved side-fix: `services/execution/bootstrap.py::_ensure_auto_paper_run`'s `preserved_keys` tuple was missing `"llm_veto_enabled"`, so a manually-disabled veto silently flipped back to the hardcoded `True` on every bootstrap restart. Fixed by adding it to `preserved_keys`.
- New bug found during end-to-end verification (not part of the original 5-point plan, but the same class of "silent failure" symptom): `_OPENROUTER_SEED_MODELS` hardcoded two OpenRouter free-tier model IDs that had been retired by the provider (404 on every call). Replaced with two verified-live free models (`nvidia/nemotron-3-super-120b-a12b:free`, `nvidia/nemotron-3-nano-30b-a3b:free`) after querying the live catalog and confirming clean JSON output across 3 consecutive runs.
- GitHub Models validation blocked by a token permission-scope issue (401/403 on chat-completion endpoints, but 200 on the catalog-listing endpoint — the PAT itself lacks the "Models" read scope, not a code defect). Raised to the user via AskUserQuestion; user chose "先跳过,只用OpenRouter验证 (推荐)" (skip for now, validate with OpenRouter only) — this satisfies the section's free-tier-validation requirement via OpenRouter alone.
- End-to-end validation: new opt-in integration test `tests/integration/test_llm_decision_veto_live.py` (follows the existing `@pytest.mark.integration` + `RUN_<NAME>_INTEGRATION` env-gated convention from `tests/integration/test_binance_public_smoke.py`), exercising the real `AgentTaskService.submit_task()` → `_execute_llm_veto()` → `AgentTaskRepository` persistence path against a real OpenRouter free model, using the SQLite harness in `tests/conftest.py::db_session`. `RUN_LLM_VETO_INTEGRATION=1 pytest tests/integration/test_llm_decision_veto_live.py -v` → PASSED (14.60s).
- Verification: full `pytest -q` → 321 passed, 2 skipped; `ruff check` and `mypy` clean on all touched files (also fixed an unrelated `ruff I001` import-order error in `shared/models/__init__.py` introduced by a prior session's edit). All touched code Read-reverified before declaring complete, per the mandatory self-verification rule.

## Deep audit remediation Phase 4/5 closure: order-provenance isolation, ExitLadder E2E test, correlation risk audit (2026-07-13)

- Phase 4 (see `[TASK-054]` in task-history.md): confirmed testnet-acceptance probe orders and funding-carry legs are both already isolated from real strategy performance stats — carry legs never call `ExecutionRepository.create_order()` at all (architectural isolation), acceptance probes use explicit `BINANCE_DEMO_AUDIT_KEY` tagging. No code change; added the first HTTP-level `/auto-cycle` end-to-end test for ExitLadder partial closes (`tests/api/test_paper_runtime_api.py::test_paper_runtime_auto_cycle_partial_closes_via_exit_ladder`), using deterministic `stoploss_rules={"fixed_bps": 200}` to make the L1 trigger price computable in test code without reading back API responses.
- Phase 5 (see `[TASK-055]`): this closes the user's original five-phase remediation report. Cross-symbol correlation-based portfolio risk control was **already fully implemented** before this remediation round (traced to `[TASK-048]`): `services/execution/portfolio_risk.py` (fail-closed Pearson correlation over 60-bar windows), `paper_signal.py::_build_risk_state()` (per-candidate correlation vs. every held position, risk-budget discount), `gatekeeper.py::_evaluate_numeric_risk()` (four rejection codes: `portfolio_correlation_unavailable`, `correlated_exposure_limit_exceeded`, `correlated_cluster_exposure_exceeded`, `net_directional_exposure_exceeded`), all covered by existing tests in `test_execution_gatekeeper.py`. No new code was needed — this was a verification-only task. **All five phases from the user's original report are now closed**: Thread A (leverage/sizing aggressiveness), Thread B (indicator-driven entries: MACD/RSI/MA/FVG/ADX/VWAP/Bollinger), Phase 2/3 (execution defaults + majority-vote ensemble), Phase 4 (order isolation + ExitLadder E2E test), Phase 5 (correlation risk — pre-existing, verified).
- Also corrected a documentation typo introduced while closing Phase 4: `task_plan.md`/`progress.md`/`task-history.md` had recorded the scoped `pytest tests/api/test_paper_runtime_api.py -q` run as "8 passed" when the real output was "7 passed, 1 warning" (6 pre-existing + 1 new test). Fixed in all three files; does not affect the full-suite "321 passed, 1 skipped" figure, which was already correct.

## Deep audit remediation Phase 2/3 closure: default order type, opposite-signal priority, ADR-023 fix, majority-vote ensemble (2026-07-13)

- Continuing the same standing user instruction from the Phase 1 entry below ("不用中断，下一个phase以上一个phase的结果制定方案，自动执行"): investigated Phase 2's remaining checklist items and found two were already implemented by prior sessions, not still pending as `task_plan.md` claimed.
  - "默认改为限价单（保留可配置的市价单兜底）": confirmed `shared/config.py:58` already sets `execution_default_order_type = "limit"`, and both `paper_signal.py:82` and `gateway.py:339` read `strategy.rules.entry_rules.get("order_type", settings.execution_default_order_type)` — i.e. limit is the default with a per-strategy market-order override still available (used deliberately for `submit_acceptance_order`/`submit_carry_order`, which are infrastructure probes, not directional entries). No code change needed; `task_plan.md` corrected to reflect this as already-satisfied rather than pending.
  - "收紧反向信号平仓逻辑": confirmed via `paper_runtime.py:606-830` (and locked in by this session's new regression test `test_runtime_stoploss_wins_over_opposite_signal_hit_on_same_bar`) that protective-trigger handling (`_check_protective_trigger`) already executes and `continue`s strictly before the `close_on_opposite_signal` branch is ever reached on the same bar — stoploss/takeprofit always wins over an opposite-signal close, which is the correct tightened priority. No further change required; this was Phase 2-2's actual deliverable (a test, not a behavior change, since the behavior was already correct).
- **Phase 2-3/2-4 (ADR-023 fix):** deleted the misplaced/duplicate ADR-023 entry (colliding with the unrelated 2026-07-03 grounding ADR) and renumbered/relocated it as **ADR-058**, correcting its stale "5% aggregate initial risk" text to the actual `max_portfolio_initial_risk_fraction=0.15` in `services/execution/bootstrap.py`'s `AUTO_PAPER_TECHNICAL_RULES`.
- **Phase 3 (multi-timeframe AND-gate → majority vote), re-confirmed as complete:** `services/strategy_library/ensemble.py`'s `layered_regime_entry` fusion now resolves direction by majority vote across direction-source signals (`technical_dow_trend`, `technical_ema_trend`, `technical_adx`, `technical_mtf_ma`, `technical_macd`, etc.) with `MIN_DIRECTION_SOURCE_QUORUM=3`, failing closed only on exact ties or below-quorum counts — not on any single dissenting vote as the old all-agree AND-gate did. Re-verified by reading `tests/services/test_signal_ensemble.py` in full this session (majority-vote-not-unanimity, exact-tie fail-closed, below-quorum fail-closed, counter-trend-vote-exclusion cases all present and passing).
- **Thread B (multi-indicator entry signals beyond time-based bars), re-confirmed as complete:** `services/strategy_library/technical.py` has `generate_macd_signal` (golden/death cross), `generate_rsi_signal` (overbought/oversold), `generate_ema_trend_signal`/`generate_dow_trend_signal`/`generate_multi_timeframe_ma_signal` (multi-timeframe MA alignment), `generate_fvg_signal` (fair-value-gap fill/reclaim), `generate_adx_trend_signal`, `generate_false_breakout_signal`, `generate_vwap_reclaim_signal`, `generate_bollinger_reversion_signal` — all covered by `tests/services/test_technical_signals.py`, re-read in full this session with no discrepancies found.
- Verification this session: `tests/services/test_paper_runtime.py` 17/17 passed (new stoploss-priority test included); full `pytest -q` → 320 passed, 1 skipped; `ruff check .` and `mypy` clean on all touched files.
- This entry's stale predecessor text ("5% aggregate initial stop risk") also existed in the "Directional Paper Runtime (2026-07-12)" entry further down this file — corrected there too, see that entry's note.
- Remaining open items per `task_plan.md`: **Phase 4** (separate `TestnetAcceptanceService` probe orders from real strategy orders; ExitLadder E2E tests) and **Phase 5** (cross-symbol correlation portfolio risk control) — proceeding to these next per the standing "自动执行" instruction.

## Deep audit remediation Phase 1: leverage-slider break-chain + remainder-trail dedup (2026-07-13)

- User delivered a fresh multi-part diagnostic report on the automatic open/close and quant-strategy system, with a Phase 0–5 remediation plan and four ready-to-use prompts (A/B/C/D). Standing instruction: analyze first, then work module-by-module in the report's order, ask before any uncertain call, and update all project tracking/memory files after every completed task. Phase 1's exact implementation approach was explicitly delegated to me ("这部分你定吧").
- **Leverage slider fix (see ADR-056):** confirmed the actual root cause is `AutoTradingSettings.asset_risk_tiers`'s Pydantic `default_factory` making the field permanently truthy, so `PaperSignalGenerator._requested_leverage`/`_requested_notional` always preferred it over `position_rules.max_leverage`; the admin UI edits only the latter (dead) field and echoes back the former unchanged. User chose "方案 B" via AskUserQuestion: keep the tier structure, rescale it against the sliders. Implemented `scale_asset_risk_tiers()` in `services/execution/risk_tiers.py`, wired into `update_paper_run_auto_settings`, and fixed the frontend's disconnected defaults/fake static preview in `RuntimePanels.jsx`.
- **Remainder-trail dedup (see ADR-057):** confirmed `paper_runtime.py::_apply_trailing_ratchet` and `technical_replay.py::_advance_open_position`'s hand-rolled ladder-remainder block computed identical LONG/SHORT ratchet-to-breakeven math against two different data shapes. Extracted `next_trailed_stop_price()` into `services/execution/exit_ladder.py`; both call sites now delegate to it.
- Verification: `tests/services/test_asset_risk_tiers.py` (+3), `tests/api/test_paper_runtime_api.py` (+tier-scaling assertions), `tests/services/test_exit_ladder.py` (+5 for the extracted function) — 37/37 relevant tests passed; ruff/mypy clean on all touched files. Both edited call sites were re-Read after editing per the delivery self-check rule.
- **Phase 0 correction:** cross-checking the user's report against actual code found Phase 0 (ExitLadder replay comparison) was already delivered in the prior session (TASK-052, 2026-07-12) via `scripts/run_exitladder_replay_comparison.py` and `TechnicalStrategyValidationService(exit_mode=...)` — not via a `--exit-model` flag on `run_top20_technical_validation.py` as the report's wording implied. `docs/audits/2026-07-12-exitladder-replay-comparison.md` already shows ExitLadder underperforms fixed 2R (net expectancy `-0.000866` vs `0.002185`, PF `0.8817` vs `1.1308`) — no promotion, no auto-lane change. `task_plan.md` corrected accordingly rather than redoing this work.
- Remaining Phase 2/3/4/5 items from the user's report are still pending (see `task_plan.md`): limit-order default + opposite-signal close tightening + ADR-023 duplicate numbering/5%-vs-10% mismatch, multi-timeframe AND-gate → majority-vote redesign, Testnet-probe/strategy-order separation + ExitLadder E2E tests, cross-symbol correlation portfolio risk.

## Auto open/close gap closure Phases A–D + ops unblock (TASK-052/053, 2026-07-12)

- Directional mechanical path unblocked: ghost Demo injection + ReduceOnly `-2022` loop fixed; mature directional `457c6ecd` is the sole running directional run (`binance_simulation_first`, cost gate verified); duplicate `c2b5a1fa` paused.
- Live mirror proof: manual open+reduceOnly close on Testnet for `457c6ecd` → LINK `gateway_order_id` open `813722666` / close `813722823` (`docs/audits/_directional_manual_mirror_proof.json`, verdict `directional_mirror_ok`).
- Auto cycle still often skips (`multi_timeframe_disagreement` / `ensemble_discarded` / `skip_duplicate_cycle`) — expected filter behavior, not a gateway crash. Paper engine API `http://127.0.0.1:8016` + `.local_paper_console.db`.
- ExitLadder offline comparison worse than fixed 2R on net expectancy — no auto promotion. Vol tiers ADR-055 remain mechanical only. Top20 OOS still failed — do not enable strategies/mainnet.

## Strategy Playbook, ecosystem research, and current-state audit (TASK-046, 2026-07-12)

- Completed a code-and-test-backed current-state review at `docs/audits/2026-07-12-current-state-review.md`. The review maps all eight technical signals, multi-timeframe confirmation, correlation discounting, MetaLabel, LLM veto, Gatekeeper, exits, and position sizing to file/line evidence. It distinguishes the scoped `0.50`/`0.45` MetaLabel values and `3x`/`5x`/`10x` leverage contexts; no core trading/data blocker was found.
- Added a code-backed Strategy Library Playbook API plus persisted roadmap status: `GET /api/v1/strategy-library/playbook` and `PATCH /api/v1/strategy-library/roadmap-items/{item_id}`. Roadmap changes retain operator, note, timestamp, and audit history; migration `0007` upgrades clean SQLite from `0001` through the new table.
- Reworked the Strategy Library into the retained Strategy Assets workflow plus seven explanatory tabs: overview, entry, exit, position sizing, LLM/RAG boundaries, external sources, and optimization roadmap. Explanatory content comes from the API, displays its source/verification commit, and has explicit loading/error/retry behavior.
- GitHub research was frozen at the 2026-07-12 snapshot. Superalgos was corrected to Apache-2.0; Jesse/NautilusTrader/Qlib/vectorbt/OpenBB local ingestion evidence was indexed; five new candidates (Lumen, HydraQuant, basis-funding-arbitrage-bot, hedge-fund-committee, RiverFlow-Apex) were added with license policies and roadmap mappings. Detailed evidence is in `策略库/06_GitHub生态补充调研.md`.
- Fresh verification: backend `257 passed, 1 skipped, 2 warnings`; frontend `11 files / 30 tests`; Ruff passed; mypy passed for 119 source files; production build and `git diff --check` passed. Clean SQLite migration reached `0007`. Chrome headless fallback screenshots at `1440x1000` and `390x844` were visually inspected after Playwright was unavailable; the content rendered without incoherent overlap, and a real PATCH/GET round trip persisted roadmap state with one audit record.
- Known debt remains explicit: full-repository `ruff format --check .` had 65 historical drift files at the pre-change audit, so only files touched in this task were formatted and checked. Vite still reports a >500 kB bundle warning; Python reports the existing Starlette TestClient and Alembic config deprecations.

## Risk-gate hardening and real Top20 Testnet acceptance (TASK-045, 2026-07-12)

- MetaLabel now requires at least 20 historical samples before `bet_taken`; short positive histories fail closed. Explicit `4h_direction_15m_entry` confirmation also fails closed when 4h data or confirmation signals are unavailable, while strategies without an explicit timeframe model no longer inherit a hidden confirmation timeframe.
- Automatic Paper orchestration defaults to the fixed operator Top20 and `fixed_operator_top20`; explicit manual/research scopes remain authoritative.
- Futures Testnet acceptance has a 120 USDT per-symbol cap and per-symbol stage/order/protection/compensation/failure evidence. A real run completed all 20 symbols with 40 fills and ended with zero positions and zero open orders.
- Read-only `scripts/testnet_preflight.py` proves the Mainnet boundary and reports only sanitized readiness. Futures Testnet is connected; Spot Testnet credentials remain missing, so no real dual-leg carry completion is claimed.
- Celery uses late acknowledgement, worker-loss requeue, task-start tracking, and bounded result retention. Runtime status exposes per-task run/failure counts, last success/failure timestamps, and Top20 heartbeat coverage.
- Temporal remains a future migration option only; `docs/architecture/temporal-migration-adr.md` defines deterministic workflow/activity boundaries without adding a second production runtime.

## Binance Testnet acceptance, dual-leg carry, and trading workbench (TASK-043, 2026-07-11)

- Automatic execution settings now resolve asset tiers instead of applying one leverage/position cap to every symbol: BTC/ETH/SOL use `10x` with a `15%` equity-based notional cap; the other fixed Top20 symbols use `5x` with a `6%` cap. Leverage affects margin only and does not multiply the configured notional exposure.
- Global execution constraints remain fail-closed: at most 5 positions, 50% aggregate exposure, 1% risk per trade, 4% daily loss, and 15% hard drawdown. Gatekeeper/protection checks, LLM veto, Market Intelligence, and the no-Martingale rule remain in force.
- A persisted Futures Testnet acceptance service/API now performs preflight, clean-account checks, per-symbol leverage and precision sizing, protected market entry, reduce-only close, protection cleanup, compensation, idempotency, Binance-order evidence, and final zero-position/zero-order reconciliation for the fixed Top20.
- Funding carry is now a separate dual-leg execution workflow: independent Binance Spot Testnet credentials, Spot long plus equal-notional Futures short, explicit leg/state evidence, second-leg compensation, two-leg close, and final net-exposure checks. It does not reuse or relabel the previous single-leg funding signal as arbitrage execution.
- The Trading console is now a workbench: order book REST/WebSocket paths were removed, chart and order ticket remain primary, and a fixed-height internally scrollable record workspace exposes positions, orders, Binance account, automation, carry, decision, and risk/data evidence. Table wrapping and raw CCXT connection-error presentation were corrected.
- Fresh verification: backend `232 passed, 1 skipped, 1 warning`; admin Vitest `9 files / 20 tests`; production build passed; mypy passed for 114 source files; directed Ruff passed; `git diff --check` passed. Chrome screenshots at desktop and `390x844` show the workbench without incoherent overlap.
- External acceptance remains incomplete by design: `127.0.0.1:7890` is not listening and Spot Testnet credentials are absent. No 20-symbol/40-fill run, dual-leg BTC carry round trip, or official Binance history screenshots may be claimed until those prerequisites are supplied and the resulting account is reconciled flat.

## Binance Mock Top20 runtime remediation (TASK-042, 2026-07-11)

- The July 9 API process was stale relative to the July 10 frontend/code. Local startup now migrates legacy SQLite safely, uses the global `AGENT_PYTHON`, validates the current API contract/build identifier, and no longer overrides the operator's `.env` execution choice.
- The operator has explicitly armed local Binance Mock/Testnet execution with `BINANCE_AUTO_EXECUTE=true`, `BINANCE_USE_TESTNET=true`, and `LIVE_TRADING_ENABLED=false`. Effective execution still requires credentials, a Testnet boundary, and per-run `mirror_to_gateway=true`; mainnet remains hard-disabled.
- Automatic bootstrap maintains exactly two active runs (`auto_paper_btc_funding` and `auto_paper_mature_templates`) over the fixed 20-symbol universe with `max_symbols=20` and `binance_simulation_first`. The duplicate legacy technical run is paused without deleting its orders or audit history.
- Fixed Top20 is served immediately from the canonical server list. Binance `exchangeInfo` enriches tradability/precision/min-notional asynchronously; `PEPE` maps to `1000PEPEUSDT`. Exchange submission performs its own availability validation even while UI cache status is pending.
- The Trading console no longer substitutes three fake fallback symbols. It distinguishes monitoring, armed execution, no qualifying signal, risk rejection, and exchange-unavailable states. Order reconciliation covers all Top20 symbols and exposes matched, unmatched, rejected, and protection-order evidence.
- Logging is INFO by default with 10 MB / five-file rotation. CCXT, urllib3, websockets, httpx/httpcore wire output and Uvicorn query-bearing access logs are suppressed. Approximately 3.74 GB of authorized obsolete logs were deleted; current credentials were retained as requested.
- Third-party websocket transport tracebacks emitted through the `asyncio` logger are filtered only when their traceback originates inside `site-packages/websockets`; application asyncio exceptions remain visible. Optional news/macro/social/notification failures remain in task-level failure/result state and no longer falsely mark the core scheduler unhealthy. Celery's lazy task API is preloaded before in-process runner threads start, preventing concurrent first-import failures in core review tasks.
- Fresh verification: backend `217 passed, 1 deselected, 1 warning`; full-repository Ruff and mypy passed; admin Vitest `8 files / 19 tests` and production build passed; `git diff --check` passed. Runtime status was `scheduler running`, `auto armed`, `live/mainnet false`; both active runs scanned 20 symbols and Top20 reconciliation returned 20 summaries. No order was forced because current signals did not pass the existing strategy/validation/risk gates.

## Adversarial audit remediation baseline (TASK-041, 2026-07-10)

- Local SQLite startup is a two-part contract: Alembic owns relational tables, while `create_local_runtime_schema()` creates the separately owned time-series/event tables needed outside Docker. `run-api-local.ps1` must perform both after setting `POSTGRES_URL`; production TimescaleDB remains owned by `infra/timescale/init.sql`.
- Execution gateway startup fails closed for configured keys that expose `canWithdraw`; the credential-less placeholder gateway remains visible for operator capability status but cannot trade. Opening requests below exchange `min_notional` are rejected, and exchange submissions use a stable, length-bounded client order ID derived from the logical idempotency key.
- The frontend must treat network failures and proxy 5xx as the same service-unavailable state. Once that state is set, `useConsoleData` stops refresh, account polling, and WebSocket reconnection. Auto-settings must normalize nullable API fields before binding controlled inputs.
- Latest verification: Python `205 passed, 1 skipped`; Ruff and frontend test/build pass. Mypy is currently a separate failing quality gate (`68` errors in `23` files) and must not be reported as clean.

## Fixed Top20 Binance simulation-first auto trading lane (TASK-039, 2026-07-10)

- The automatic trading candidate universe is now the operator-defined fixed Top20 list, not a dynamic quote-volume replacement: BTC, ETH, SOL, XRP, BNB, HYPE, SUI, LINK, TRX, AVAX, TON, DOGE, ADA, HBAR, ONDO, ENA, TAO, FET/ASI, RENDER, and PEPE. PEPE maps to Binance USD-M `1000PEPEUSDT` while the UI displays `PEPE (1000PEPE contract)`.
- `/api/v1/market/universe?mode=fixed_top20` returns `UniverseAsset` status, exchange symbol mapping, precision, min notional, and skip reasons. Automatic runtime skips non-TRADING symbols with visible review context instead of silently trying to trade them.
- Data heartbeat now maintains required automatic-cycle timeframes (`1m`, `15m`, `4h`) for all 20 fixed candidates. Runtime scanning respects configurable `max_symbols`, `max_open_positions`, leverage, exposure, and kill-switch boundaries.
- Default automatic strategy bootstrap moved away from the operator-experience `4h_direction_15m_entry` rule. The new default key is `auto_paper_mature_templates`; `operator_experience_4h_15m_v1` is kept disabled for research/backtest only. Bootstrap no longer fabricates validation metrics.
- Binance simulation-first execution mode records local `OrderExecution`, `PositionSnapshot`, gateway order ids, and protection refs only after Testnet/Demo gateway success. Gateway failure fails closed with a local rejection rather than a fake fill. Mainnet/live trading remains disabled.
- Trading console now exposes editable automatic order settings, fixed Top20 monitoring, message-source rows, and order-sync state. Settings updates write through typed auto-settings into the current PaperRun execution profile and bound RiskProfile.
- Verification evidence: backend non-integration pytest passed (`196 passed, 1 deselected, 1 warning`), Ruff passed, mypy passed, admin Vitest passed (`12 passed`), admin build passed, `git diff --check` passed, and Playwright smoke confirmed the trading page renders the new panels without JS runtime errors when only Vite is running.

## Auto Paper/Testnet safety rollback after far protection orders (TASK-038, 2026-07-09)

- Root cause: local Paper bootstrap and startup defaults treated Binance credentials as consent to exchange mirroring. `mirror_to_gateway` was auto-enabled, `BINANCE_AUTO_EXECUTE` defaulted true, and local startup scripts forced auto execution on. Automatic Paper cycles could therefore submit Testnet market entries plus stop/takeprofit conditional protection orders.
- Price root cause: Paper order generation used the latest repository K-line close as `reference_price`; the Binance gateway submitted already-computed stoploss/takeprofit triggers and did not revalidate protection distances against a current execution reference before placing the entry.
- Safety fix: automatic Paper/Testnet mirroring is now operator opt-in. `BINANCE_AUTO_EXECUTE` defaults false in settings, scripts, `.env.example`, and local `.env`; `PAPER_RUNTIME_RELAXED_SIGNALS` is false in local `.env` / `.env.example`; bootstrap no longer flips running PaperRuns to `mirror_to_gateway=true`.
- Gateway guard: Binance gateway now rejects invalid or far protection prices before entry submission, using `GATEWAY_PROTECTION_MAX_DISTANCE_BPS` (default 800 bps). Long/short stoploss and takeprofit side checks are enforced before any exchange order is created.
- Operational response: the already-running local FastAPI process on port 8000 was stopped so old in-memory settings cannot continue scheduler cycles. Restarting the console will use the safer Paper-only defaults.
- Verification: targeted execution/API tests passed (`28 passed, 1 warning`), changed-file Ruff passed, and `git diff --check` passed.

## Market Intelligence capped factor vote (TASK-037, 2026-07-09)

- Market Intelligence is implemented as an in-architecture factor, not a new layer: `MarketEvent`, `MarketIntelligenceFeatureSnapshot`, provider status, and provider adapters belong to Data Layer; `MarketIntelligenceSignal` becomes a capped Strategy Layer vote; execution authority remains with SignalEnsemble, MetaLabel, Decision Veto, Gatekeeper, and Risk Engine.
- First version supports Binance-derived `market_extras`, news/macro/risk evidence, and adapter-first provider status for CoinGlass, CryptoQuant, and DeFiLlama. CoinGlass/CryptoQuant missing API keys return `missing_credentials` and do not fail runtime.
- Directional Paper decisions now add `market_intelligence` to the ensemble only after at least one deterministic technical signal exists. `vote_weight` is schema-capped at `0.30`; high/critical active risk events set cooldown and disable the vote.
- New API surface: `/api/v1/market-intelligence/events`, `/features`, `/signals`, and `/refresh`. Trading console shows the intelligence panel; Ops shows provider status; Review shows current intelligence factor state.
- Verification evidence: targeted Market Intelligence/DecisionPipeline tests passed (`23 passed`); changed Python Ruff passed; mypy passed; admin Vitest passed; admin build passed.

## Binance Testnet real open/close smoke + quality baseline closure (TASK-036, 2026-07-09)

- A real Binance Futures Testnet BTCUSDT open/close smoke was executed through `BinanceUsdtPerpetualGateway` with `LIVE_TRADING_ENABLED=false`, `BINANCE_USE_TESTNET=true`, API base `https://testnet.binancefuture.com/fapi/v1`, and quantity `0.001`.
- Exchange records: open BUY market order `20356862614` filled for `0.0010` BTC at avg `62874.700000`; close SELL reduce-only market order `20356874963` filled for `0.0010` BTC at avg `62864.500000`; cleanup SELL reduce-only order `20356888777` filled for a pre-existing `0.0001` BTC residual position at avg `62874.200000`.
- Final Testnet probe confirmed `open_position_count=0`, `positions=[]`, wallet balance `5250.75171046`, and available balance `5250.75171046`. Evidence is stored in `scripts/_testnet_open_close_report.json` and contains no API secrets.
- Runtime hardening discovered during the real smoke: Binance private calls needed ccxt `adjustForTimeDifference` plus `load_time_difference()` after demo/testnet URL selection; close-only gateway orders now invert the current position side (`long` -> SELL reduce-only, `short` -> BUY reduce-only).
- Quality baseline after the real smoke: full repo Ruff passed, mypy passed, backend non-integration tests passed (`185 passed, 1 deselected, 1 warning`), admin Vitest passed (`12 passed`), admin build passed, npm audit found 0 vulnerabilities, project `pip-audit .` found no known vulnerabilities, and `git diff --check` passed. Compose smoke remains host-blocked because Docker is not on PATH.

## Technical strategy hardening + Strategy Library RAG (TASK-035, 2026-07-09)

- Technical directional lane now defaults to explicit, rule-based signals only: MACD, Dow trend, price action false breakout/breakdown, RSI, EMA trend, ADX, VWAP reclaim, and Bollinger reversion. The unsafe "last two candles" fallback was removed; no qualifying signal means no trade.
- The default automatic technical strategy is configured as `4h_direction_15m_entry`, with 4h direction confirmation and 15m entry signals. Default technical-lane risk is conservative: `risk_per_trade=1%`, `max_leverage=5`, and `max_position_fraction=5%`.
- Binance/Testnet auto execution now follows the accepted "exchange first, local record after success" semantics. If the gateway submit fails, the local order is marked rejected with `binance_auto_execute_failed`, rather than filled locally.
- RAG retrieval now prioritizes the local `策略库/*.md` operator-editable strategy documents, then falls back to distilled open-source assets under `research_source/open_source_strategy_library/assets/**/*.md`. Chinese trading keywords and the newly supported indicator families are searchable.
- ABU was corrected to GPL-3.0 and kept as distilled research-only material. `策略库/05_ABU策略组件索引.md` records strategy component taxonomy without copying runtime source.
- Verification evidence: `python -m pytest -q -m "not integration"` -> 184 passed, 1 deselected, 1 warning; changed-file Ruff passed; `python -m mypy` passed; admin Vitest passed (12 tests); admin build passed; `git diff --check` passed. Full repo Ruff still has pre-existing unrelated style issues in older files and was not batch-fixed.

## Report gap closure — frontend depth, observability, compose smoke (TASK-033, 2026-07-08)

- Pytest SQLite databases now live under `.local/test-runtime/` (see `tests/conftest.py`); root-level `.pytest_ai_quant*.db` clutter was migrated/cleaned. `scripts/clean_test_artifacts.py` removes stale files after 7 days.
- Frontend §5 depth: nested routes for backtest/optimization/strategy/research-source details; ValidationCenter supports backtest submission; ResearchDesk supports refresh-assets/extract-ideas/promote-to-draft; StrategyLibrary supports detail drill-down, version history, materialize draft, status update; RiskConsole supports RiskProfile create + edit via `POST/PUT /api/v1/risk/profiles`.
- Backend additions: `RiskProfileUpdate` + `PUT /risk/profiles/{id}`; `GET /optimizations/{id}`; `GET /strategies/versions?strategy_id=`.
- Observability: `GET /metrics` exposes scheduler + live feed gauges; `infra/prometheus/prometheus.yml` scrapes `api:8000`. ADR-041 documents unauthenticated internal scrape policy.
- Compose runtime smoke: `scripts/compose_smoke.py` brings up timescaledb/redis/api, checks `/health` and `/system/health/dependencies`, then tears down. Optional CI workflow `.github/workflows/compose-smoke.yml` runs on `workflow_dispatch`, weekly schedule, or PR label `run-compose-smoke`.
- Signal engine: MACD histogram and Dow continuous trend paths emit confidence-scaled signals; `_signal_weight` multiplies base weight by `signal.confidence`.
- Verified: `py -3 -m pytest -q -m "not integration"` -> 167 passed; admin Vitest 12 passed; admin build passed. Local compose smoke skipped (Docker not on PATH).

## Protective exits, free LLM fallback, and Binance Testnet mirror (TASK-032, 2026-07-08)

- `PaperRuntimeService` now checks protective stoploss/takeprofit levels before no-trade/opposite-signal handling. It resolves the latest filled non-close entry order, uses Kline high/low crossing rather than close-only checks, fills at the protective trigger price, prioritizes stoploss when both protective levels are touched in the same bar, and records stoploss outcomes through Review `FailureRecord`.
- `takeprofit_rules.trail_after_r` is now consumed for Paper runtime positions. When floating profit reaches the configured R multiple, the runtime ratchets stoploss to entry in `PaperRun.paper_metrics_summary.protective_trailing`; the stop only tightens and never loosens.
- Paper automatic cycles can optionally mirror local fills to the configured Binance USDT perpetual gateway when `PaperRun.execution_profile.mirror_to_gateway=true`. Mirroring is explicit per PaperRun, local Paper fill/position updates happen first, and gateway failures write `gateway_mirror_failed` failure records without rolling back local state.
- The admin trading console exposes a Testnet mirror toggle backed by `PATCH /api/v1/execution/paper-runs/{id}/execution-profile`. Default behavior remains local Paper-only until the operator explicitly enables mirroring.
- Agent LLM runtime now supports Anthropic first, then OpenRouter free models, then GitHub Models free models through an OpenAI-compatible structured JSON runtime and `FallbackChainStructuredLLMRuntime`. `GITHUB_MODELS_TOKEN` is independent from `GITHUB_TOKEN`; model overrides are optional comma-separated settings, otherwise runtime catalog discovery with short cache and static seed fallback is used.
- Verified: targeted Paper/LLM/API tests passed; full `py -3 -m pytest -q` passed (`162 passed, 1 skipped`); Ruff, Ruff format check on changed Python files, mypy, frontend Vitest, frontend build, npm audit, pip-audit, and `git diff --check` passed. Compose validation remained skipped locally because Docker is not on PATH.

## Real-time trading console + multi-screen split (TASK-031, 2026-07-07)

- The Paper trading console's "not real-time" complaint had a concrete root cause chain, not a missing feature: `binance_live_universe_enabled` / `binance_live_market_enabled` / `binance_live_ws_enabled` in `apps/api/config.py` defaulted to `False`, so `/market/exchange-stream` was silently degrading to 2s REST polling dressed up as WS frames. All three now default `True`; `tests/conftest.py` gained an `autouse` fixture that forces them back to `False` during tests so the suite never makes real outbound Binance calls.
- `frontend/admin/src/hooks/useConsoleData.js` no longer bumps `candleSnapshotVersion` (full chart rebuild) on every 8s poll tick while the WS stream is `live` — only when genuinely falling back to REST. Live klines now flow purely through the WS `kline` event's incremental `update()`. The WS connection also now reconnects with exponential backoff (capped at 15s) on `onclose`/`onerror` instead of going permanently idle until the user changes symbol/timeframe.
- Trading page layout (`PaperConsole.jsx` + `styles.css`): the order ticket (`.ticket-rail`) is no longer buried at the bottom of a single-column stack below 1280px — CSS `order` now puts chart → ticket → market list → order book, so the buy/sell form is reachable within one screen at common widths.
- Non-core-trading content was moved out of the trading page into the routes already scaffolded for it: `RiskEventFeed` → `/risk` (`RiskConsole.jsx`, replacing its hand-written table, now with a real resolve/acknowledge action wired to `PATCH /risk/events/{id}/resolution`), news/macro/notifications → `/ops` (`OpsConsole.jsx`, previously a placeholder), review reports → `/review` (`ReviewCenter.jsx`, previously a placeholder). Both new pages use `@tanstack/react-query` `useQuery`, matching `RiskConsole.jsx`'s existing pattern rather than `useConsoleData.js`'s hand-rolled fetch/poll style, since they have no legacy coupling to that hook.
- Deliberate deviation from the literal plan text: `RuntimeControlPanel` and `DecisionDebugPanel` stayed on `/trading` (inside a new always-visible `execution-grid`, no longer hidden behind an accordion) instead of moving to `/ops`, because both are scoped to the currently-selected symbol/timeframe, not global ops state — moving them would have separated a trader's control from the chart they're commenting on.
- `OpsPanels.jsx`'s `FeedPanel` is now exported and reused directly by `OpsConsole.jsx`/`ReviewCenter.jsx`; the old `OpsReviewPanel` wrapper (and the now-dead `newsItems`/`macroEvents`/`reviews`/`notifications` fetches + state fields in `useConsoleData.js`) were deleted once Grep confirmed zero remaining callers, removing four redundant REST calls from the trading page's 8-second poll cycle.
- Verification: `py -3 -m pytest tests/ -q` -> 151 passed, 1 skipped; `npm --workspace frontend/admin run test -- --run` -> 8 passed; `npm --workspace frontend/admin run build` passed. Manual browser smoke (symbol/timeframe switching, WS reconnect, responsive breakpoints) was not performed this session — only automated tests and build were run.

## One-click startup dependency self-heal (TASK-030 startup, 2026-07-07)

- The local Paper console startup failure was caused by `frontend/admin/src/router.jsx` importing `@tanstack/react-query` while the workspace `node_modules` did not contain the package, even though `frontend/admin/package.json` and `package-lock.json` already declared it.
- `scripts/start_paper_console.ps1` now checks for key frontend workspace modules (`@tanstack/react-query`, `lightweight-charts`, `react-router-dom`, `vite`, `vitest`) instead of only checking whether `node_modules` exists. If any are missing, one-click startup runs `npm install` before launching FastAPI and Vite.
- This is an Ops/startup-path fix for the local Paper/Testnet console. It does not change Strategy, Validation, Execution, Risk, or Review logic.
- Verification evidence: `npm --workspace frontend/admin run build` passed; `npm --workspace frontend/admin ls @tanstack/react-query` resolved `@tanstack/react-query@5.101.2`; `.\一键启动.bat` started API `http://127.0.0.1:8000` and frontend `http://127.0.0.1:5173`; API `/health` returned ok and frontend `/` returned 200.
- GitHub branch check: local `main`, `origin/main`, and `origin/HEAD` all resolve to `9237b0647174156511ddb138fe76d6fad194d1bb`; the extra remote branches are Dependabot dependency-update branches, not the active platform trunk.

## TASK-030 security closure, scheduler/feed fixes, and third-party-backed frontend pages (2026-07-07)

- Python and frontend dependency audit baselines were tightened. Dev dependencies now require pytest 9+, pytest-asyncio 1.4+, pytest-cov 7.1+, and runtime constraints include current safe lower bounds for FastAPI/Starlette, aiohttp, cryptography, pydantic-settings, PyJWT, and python-multipart. Frontend admin now uses Vite 8.1.3 and Vitest 4.1.10 with `npm audit --audit-level=high` as a hard CI gate.
- Docker paper/live overlays now explicitly set `RUNTIME_SCHEDULER_MODE=celery` on `api`, `celery_worker`, and `celery_beat`; `scripts/compose_validate.py` rejects missing paper/live scheduler overrides to prevent duplicate in-process + Celery Beat runtime cycles.
- Binance WS collector restart exceptions now call a reconnect error handler wired by `RuntimeScheduler` into `LiveFeedBus.set_error(...)`, so operator surfaces can see `reconnecting` and `last_error` instead of silent Kline stalls.
- The remaining placeholder frontend entries were replaced with data-backed pages: Validation reads backtests/optimizations/hypotheses plus Binance funding signal; Review reads reviews/failures/decision memory/news inputs; Research reads GitHub/open-source research sources, strategy ideas, news, and macro events; Ops reads dependency health, trading scheduler status, Agent tasks, notification outbox, and exchange capabilities.
- `/api/v1/market/news` and `/api/v1/market/macro-events` now support `refresh=true` read-through ingestion using the existing RSS/SEC/news and ForexFactory macro services. Fetch failures fail soft through `refresh_error` while persisted data remains available.
- Security scan artifacts were written to `docs/security/task-030-security-scan.md` and `.html`. Project-level `pip_audit .` and `npm audit` are clean; whole-machine pip-audit still reports non-project global packages (`litellm`, `nltk`, `torch`) and is intentionally not treated as this repo's dependency graph.
- Current verification baseline: `py -3 -m pytest -q` -> 149 passed / 1 skipped; Ruff lint passed; mypy passed; changed-file Ruff format passed; admin Vitest passed; admin Vite 8 build passed; npm audit passed; project pip-audit passed. Full repo format check still has historical drift outside this change set; compose validation remains skipped locally because Docker is not on PATH.

## Trading core scheduler + Binance WS feed bus (TASK-029, 2026-07-07)

- Local Paper operation now has an in-process scheduler at `services/execution/scheduler.py`. FastAPI lifespan starts it when `RUNTIME_SCHEDULER_MODE=inprocess` and autostart is enabled, while Celery remains the production/multi-process path.
- `/api/v1/execution/trading-status` now exposes scheduler mode/running state, last auto-cycle time, next ETA, scheduler error, and live feed status without returning secrets.
- Binance live Kline collection now publishes persisted closed candles through `services/data/live_feed_bus.py`; `/api/v1/market/ohlcv/stream` sends one persisted snapshot and then subscribes to the shared bus instead of polling REST per websocket client.
- Timescale/Postgres OHLCV and market extras writes now use batch `ON CONFLICT DO UPDATE`, with SQLite fallback preserved for tests and local smoke runs.
- The trading console now shows an auto-engine status badge, limit/market order controls with GTC audit metadata, stoploss/takeprofit chart price lines, expanded order columns, and clearer Gatekeeper/LLM rejection reasons.
- Frontend IA has first platform routing: Trading, Risk, Strategy, Validation, Review, Research, and Ops top-level entries. RiskConsole and StrategyLibrary read real existing APIs; other entries are explicit placeholders, not fake data.
- Strategy execution gained optional multi-timeframe confirmation when confirmation bars exist, and Paper notional sizing now uses stop-distance risk budgeting capped by `max_position_fraction` (default 5%) before Gatekeeper.
- Engineering cleanup: CI now runs frontend tests, Python dependency audit, and npm audit reporting; Dependabot is configured; mypy uses explicit package bases; docs/config now reflect Binance-only and single-tenant Bearer decisions.
- Current verification baseline: `py -3 -m pytest -q` -> 146 passed / 1 skipped; Ruff passed; mypy passed; admin Vitest passed; admin build passed; `py -3 scripts/compose_validate.py` skipped because Docker is not on PATH.

## Binance public REST realtime Paper console closure (TASK-028, 2026-07-07)

- Paper/Testnet console now uses Binance public REST as the live market-data path for USD-M Top20 universe, OHLCV, order book, recent trades, and premium-index/funding inputs. If `ccxt` is unavailable, `BinancePublicRestExchange` falls back to standard-library HTTP calls rather than returning fake/static market data.
- `/api/v1/market/ohlcv`, `/snapshot`, `/order-book`, `/trades`, `/universe`, and `/funding-arbitrage-signal` now expose live source evidence such as `binance_public_rest` or `binance_usdm_24h_ticker`, while still writing OHLCV/funding data back into `ohlcv_bars` / `market_extras` for the Validation -> Paper chain.
- Manual Paper/Testnet trading now rejects blank `strategy_id` / `validation_backtest_run_id` at request validation, and Gatekeeper rejection writeback no longer turns blank strategy evidence into a `FailureRecord` model error. Frontend open buttons are disabled until Strategy ID, Backtest ID, and stoploss are present.
- The admin console order book and recent trades panels no longer synthesize local rows from the last price. They render backend order-book/trade payloads, show explicit empty states, and the key trading-console Chinese copy has been repaired to UTF-8 text.
- `scripts/start_paper_console.ps1` and `start-paper-console.bat` provide the local one-click startup path with live public market data enabled, safe Paper/Testnet flags, SQLite schema initialization, FastAPI + Vite startup, and system-browser opening. Browser validation avoids Codex Browser/IAB and uses system Chrome / HTTP smoke only.
- Fresh smoke evidence: local API returned OHLCV/order book/trades from `binance_public_rest`, Top20 from `binance_usdm_24h_ticker`, then a Paper manual long order filled through `paper_manual` and a close request filled with `close_only=true`.
- Current verification baseline: `py -3 -m pytest -q` -> 142 passed / 1 skipped; Ruff passed; mypy passed; admin Vitest passed; admin build passed.

## Open-source RAG assetization closure (TASK-027, 2026-07-06)

- `research_source/open_source_strategy_library` no longer stops at manifest registration. `ResearchSourceAsset` now tracks URL, commit/ref, license, local path, sha256, byte size, status, extraction tags, and summary for local RAG assets.
- `fetch_remote=true` now uses a GitHub allowlist from `seed_sources.json`, writes distilled Markdown assets under `assets/<source_id>/`, and records `asset_manifest.json`; failed remote paths are preserved as `failed_assets` rather than hidden.
- Seed sources now include allowlists, denylist patterns, license policies, and extraction targets, with added RD-Agent, vectorbt, and OpenBB references alongside the earlier Freqtrade/Jesse/Hummingbot/ABU/Lean/vn.py/TradingAgents/Qbot/Superalgos/Nautilus/Qlib/FinRL set.
- Extracted `StrategyIdea` records are now asset-driven: `intake_metadata.asset_refs` is populated from local assets, and unknown-license / metadata-only sources stay `research_note_only` so Strategy Agent cannot materialize them into drafts.
- New research-source APIs expose local assets: `GET /api/v1/research-sources/{source_id}/assets` and `POST /api/v1/research-sources/{source_id}/refresh-assets`.
- First local asset ingestion evidence exists for Freqtrade, Jesse, Hummingbot, ABU, NautilusTrader, Qlib, vectorbt, and OpenBB. This is still a local Markdown/manifest RAG substrate, not a vector DB or full external repository mirror.
- Current verification baseline: `py -3 -m pytest -q` -> 124 passed / 1 skipped; Ruff passed; mypy passed; admin build passed.

## 7x24 Paper decision pipeline automation (TASK-026, 2026-07-06)

- Celery Beat is now configured with real schedules for all-running Paper cycles, market-data heartbeat, risk-profile sweep, daily review generation, notification dispatch, C-level news polling, B-level macro polling, and D-level Twitter watchlist polling. This upgrades the previous worker entrypoint from a manual primitive into an always-on Paper scheduler seam.
- Non-arbitrage Paper order generation now flows through `DecisionPipeline`: persisted MACD/Dow/price-action signals -> `SignalEnsemble` -> `MetaLabel` -> optional `decision_veto_agent.pre_execution_veto_llm` -> `ExecutionOrderRequest`. Funding-threshold arbitrage remains deterministic and bypasses the technical ensemble by design.
- Paper runtime cycles are idempotent per `paper_run_id + symbol + timeframe + latest_bar_time`, and each action can expose a decision trace for frontend/debug/review usage.
- Stoploss/takeprofit generation now prioritizes strategy rules and falls back to ATR/risk-reward distances rather than fixed 2%/3% percentages; Gatekeeper remains the final stoploss/veto/risk hard gate.
- Data Layer now has first C/B/D source seams: `news_items`, macro event storage, RSS/SEC polling, ForexFactory-style macro polling, Twitter watchlist polling, and stale market-data RiskEvents. Missing Twitter credentials produce explicit disabled summaries rather than false success.
- Admin frontend has been split from a single `main.jsx` into API/hooks/pages/components and now includes a Decision Pipeline debug panel plus news, macro, review, and notification visibility. Vitest is now installed for frontend component coverage.
- Current verification baseline: `py -3 -m pytest -q` -> 120 passed / 1 skipped; Ruff passed; mypy passed; admin Vitest passed; admin build passed; compose validation skipped locally because Docker is not on PATH.

## P0 repository hygiene and runtime configuration guardrails (TASK-025, 2026-07-05)

- Runtime database artifacts are no longer allowed in source control. `.dev_ai_quant.db` was removed from Git tracking, and `.gitignore` now covers `.dev_ai_quant.db`, per-process pytest SQLite databases, and SQLite runtime artifacts.
- Docker Compose runtime services now read `.env`; `.env.example` is a template only. CI copies `.env.example` to a temporary `.env` before compose validation, and `scripts/compose_validate.py` rejects runtime compose files that reference `.env.example` as `env_file`.
- Admin API auth remains single-tenant Bearer token only, but the comparison is now constant-time and non-local environments reject the default `dev-admin-token` with `auth_misconfigured`.
- Research Agent local alpha scanning no longer falls back to a workstation-specific desktop path. `scan_local_alpha` requires either `input_payload.alpha_root` or `WORLDQUANT_ALPHA_LOCAL_PATH`.
- User-facing Markdown links are now repository-relative instead of tied to this Windows desktop path. Status docs now describe the repo as `Phase 0 完成 + 第一批 P1 落地`, with the next P1 order fixed as Celery Beat/7x24 scheduling, frontend admin coverage, then B/C/D data sources.

## Autonomous paper runtime over Binance Top20 candidates (TASK-024, 2026-07-04)

- The Execution Layer now has a first autonomous paper-runtime seam: `PaperRuntimeService` plus `/api/v1/execution/paper-runs/{id}/auto-cycle` and `/runtime-status`.
- Paper runtime still respects the existing admission chain. Only validation-admitted `PaperRun` objects can be cycled, and every auto-generated open/close order still flows through `ExecutionGatekeeperService`.
- Default paper monitoring is no longer effectively BTC/ETH-only. `PaperOrchestrationService` now seeds `candidate_symbols` from the in-repo Binance Top20 fallback universe while keeping `BTC/USDT` and `ETH/USDT` pinned first.
- Current runtime behavior is intentionally conservative: opposite signals close existing paper positions before later re-entry, filled paper orders are persisted through `OrderExecution` lifecycle history, and latest open-position state is derived from each symbol's newest `PositionSnapshot`.
- A worker-side entrypoint now exists at `services.execution.tasks.run_paper_runtime_cycle`, but this is still a cycle primitive rather than a proven always-on 7x24 scheduler/daemon.

## Binance testnet-first hardening (TASK-023, 2026-07-04)

- Binance 接入方式已明确收口为官方 API Key / Secret，而不是交易所登录密码。
- `Settings` 新增 `BINANCE_USE_TESTNET` 与 `LIVE_TRADING_ENABLED`；当前默认是 `BINANCE_USE_TESTNET=true`、`LIVE_TRADING_ENABLED=false`，优先测试网 / 模拟盘，不默认放开真实实盘。
- `BinanceUsdtPerpetualGateway` 现在会在可用时对底层 CCXT client 调用 `set_sandbox_mode(True/False)`，让测试网切换成为明确运行时行为，而不是靠人工记忆。
- 运维文档 `docs/ops/environment-and-config.md` 已补成可执行说明，明确要求用户自己在交易所创建测试网或最小权限 API Key，并强调 2FA、IP 白名单、关闭提现权限。

## Tranche 2/3/4 closure slice: validation evidence, live runtime, and online agent boundary (TASK-022, 2026-07-04)

- Validation Layer promotion is now strict across both Paper and Live admission. `HypothesisRecord`, benchmark/control results, OOS windows, and pod-risk evidence are persisted and checked through `ValidationAdmissionService`; legacy backtests without complete evidence no longer promote just because raw backtest eligibility passed.
- Validation report API is hypothesis-aware: `/api/v1/validation/reports/{backtest_run_id}` now resolves the linked hypothesis and returns an accurate `promotion_gate` instead of always showing `missing_hypothesis`.
- Execution Layer now exposes first-class live runtime APIs in the existing `/api/v1/execution/*` cluster: `gateway-capabilities`, `account-snapshots`, `live-runs/{id}/sync-account`, `live-runs/{id}/orders`, `live-runs/{id}/orders/{order_execution_id}/cancel`, `reconciliations`, and `live-runs/{id}/reconcile`.
- The self-owned gateway seam is now materially real rather than placeholder-only: `BinanceUsdtPerpetualGateway` maps account sync, submit, cancel, and reconciliation over a CCXT-style client for `Binance USDT perpetual`, while `NullExchangeGateway` remains the safe no-credentials fallback.
- Agent Layer now has a real structured online boundary instead of only `UnavailableLLMRuntime`: `AnthropicStructuredLLMRuntime` and `ConfiguredStructuredLLMRuntime` call the Anthropic Messages API, enforce JSON-only structured outputs, and allow per-agent provider/model mapping through `AGENT_LLM_PROVIDER_MAP` / `AGENT_LLM_MODEL_MAP`.
- Alembic `0006_validation_memory_and_gateway_runtime.py` now covers the new hypotheses, decision memory, gateway/account snapshot, reconciliation, agent-task metadata, and live/order runtime persistence. The migration was verified against SQLite with a documented SQLite-safe branch for the added live-run foreign key.

## Tranche 1 security + notification dispatch baseline (TASK-020, 2026-07-04)

- `/api/v1/*` now enforces single-tenant Bearer-token auth through `apps/api/auth.py`; `/health` and `/api/v1/health` remain public.
- The admin token is configured by `ADMIN_API_TOKEN`, defaults to `dev-admin-token` for local single-user development, and the Paper admin frontend now sends the same token through `VITE_ADMIN_API_TOKEN` fallback logic.
- `NotificationOutboxItem` is no longer an audit-only seam: it now persists `delivery_channels`, `next_attempt_at`, `last_attempt_at`, and `attempt_history`, and `NotificationDispatcherService` can deliver due items through first-batch `telegram` and `webhook` adapters with persisted retry/backoff state.
- `/api/v1/notifications/outbox/dispatch` can dispatch due notifications or replay one explicit `notification_id`; the same logic is also exposed as Celery task `services.notifications_tasks.dispatch_notification_outbox` on `ops_queue`.
- Frontend build is green again in this workspace after restoring the missing npm workspace dependency install path; CI now includes `npm ci` + `npm run admin:build`.
- `scripts/compose_validate.py` is now the standard compose validation entrypoint. On machines without Docker it exits with a documented `skipped` status locally; CI calls the same script with `--require-docker`.

## Persistent notification outbox (TASK-019, 2026-07-04)

- `NotificationOutboxItem` now persists through `notification_outbox` ORM/migration and `NotificationRepository` instead of being derived only from active `RiskEvent` rows at read time.
- High/critical `RiskEvent` creation automatically enqueues an idempotent pending notification intent with ID `risk:{risk_event_id}`; low/mid events do not auto-enqueue.
- `/api/v1/notifications/outbox` now supports persisted list/filter, manual creation, and delivery-result writeback through `delivery_status`, `delivery_attempts`, `last_error`, and `delivered_at`.
- This is still an Ops / Review / Risk visibility seam only: no Telegram, email, webhook, or credentialed external adapter was added in this tranche.

## Research-side rejection memory writeback (TASK-018, 2026-07-04)

- `FailureRecord` can now attach to either `strategy_id` or `idea_id`, while still rejecting records with neither subject.
- `StrategyIdea.intake_metadata` is now persisted through the shared contract, ORM, repository, and migration, so local alpha intake evidence is structured rather than only embedded in rationale text.
- Research Agent `scan_local_alpha` now writes persisted `subjective_to_drop` / evaluator-rejected alpha ideas into the Review Layer as `alpha_evaluator_reject` failure records.
- `/api/v1/failures` now supports filtering by `strategy_id`, `idea_id`, and `failure_type`, allowing Review/Research workflows to retrieve reusable failure evidence for clustering or manual porting.

## Phase 1 Risk Engine Hardening + WorldQuant Adapter Repair (TASK-017, 2026-07-03)

- Execution Layer now uses a typed `ExecutionRiskState` at order-admission time and persists both `rejection_codes` and `evaluated_risk_state` into every `OrderExecution`.
- `RiskProfile` defaults are now aligned across shared contracts, ORM, migration, and docs: `max_symbol_exposure=0.10`, `max_total_exposure=0.50`, `consecutive_loss_limit=4`, `api_failure_limit=3`, `api_failure_window_minutes=10`.
- `ExecutionGatekeeperService` still enforces stoploss, validation, freshness, veto, and blocking risk events, and now also enforces numeric exposure, leverage, loss, drawdown, consecutive-loss, and API-failure pauses.
- Paper stepping now synthesizes `ExecutionRiskState` from `PaperRun` metrics plus `PositionSnapshot`, while direct execution requests must provide a complete `risk_state` or be rejected.
- Gatekeeper rejections now write structured failure evidence into the existing Review Layer writeback loop through `FailureRecord -> Strategy.failure_reasons + iteration_history`.
- `research_source/worldquant_adapter` is now a real executable research seam instead of a placeholder: `ts_rank`, `ts_zscore`, and `group_neutralize` are implemented; `expression_evaluator.py` executes the supported operator subset over crypto-native inputs and fails loudly on unsupported stock fields/operators.
- Crypto group migration is explicit in v1: `industry -> volatility_regime`, `sector -> funding_regime`, `subindustry -> liquidity_regime`, `market -> market`.
- Local alpha intake now preserves raw expression metadata, windows, operator lists, mapped group aliases, behavior signatures, and explicit unsupported evidence; unsupported expressions are tagged `subjective_to_drop` instead of silently falling through.

## Open-source Strategy Library Intake (TASK-016, 2026-07-03)

- Added `StrategySourceManifest` plus import/extraction request/result contracts for E-level open-source research sources.
- Added `research_source/open_source_strategy_library` with first-batch source manifests for Freqtrade, Jesse, Hummingbot, Lean, vn.py, ABU, Superalgos, Qbot, Vibe-Trading, TradingAgents, TradingAgents-CN, daily_stock_analysis, plus NautilusTrader/OctoBot/QLib/FinRL candidates.
- The module generates local RAG metadata/assets and conservative `StrategyIdea` seeds only; external code is not imported into runtime execution.
- Added `/api/v1/research-sources`, `/api/v1/research-sources/import`, `/api/v1/research-sources/{source_id}`, and `/api/v1/research-sources/{source_id}/extract-ideas`.
- Added Agent tasks: `research_agent.import_open_source_sources`, `research_agent.extract_open_source_strategy_ideas`, and `strategy_agent.materialize_seed_strategy_drafts`.
- Added `PaperSignalGenerator` and `/api/v1/execution/paper-runs/{paper_run_id}/step`; generated paper orders still go through the existing gatekeeper checks for stoploss, validation, freshness, risk events, and veto.
- Verified locally: targeted open-source intake/Paper step tests passed; full `py -3 -m pytest -q` (`52 passed, 1 skipped`); `py -3 -m ruff check .` passed; `py -3 -m mypy` passed; `npm --workspace frontend/admin run build` passed.
- Still out of scope: live execution integration with Freqtrade/Jesse/Hummingbot/Lean/vn.py, full remote repository cloning, vector database indexing, and live grid/market-making.

## Remediation Plan First Pass (TASK-015, 2026-07-03)

- Engineering baseline repaired without adding a new architecture layer: `apps/__init__.py` fixes the package boundary, FastAPI `Depends/Query` Ruff B008 is scoped to router files, and `apps/api/config.py` uses the declared `pydantic-settings` dependency directly.
- Validation Layer now has a carry-lane walk-forward/OOS/stress diagnostic slice in `services/validation/{walk_forward,report,stress_scenarios}.py`; stress results can reject a gate decision and cannot bypass Paper admission.
- Added `/api/v1` endpoints for carry walk-forward, validation reports, system dependency health, exchange capabilities, and notification outbox. Public API prefix remains `/api/v1`.
- `BacktestReport` now carries validation windows, stress results, and lookahead diagnostics; `IngestionJob` carries data quality summary; `ExchangeCapability` and `NotificationOutboxItem` are shared contracts.
- Makefile data/backtest targets now call real script entrypoints or fail explicitly with guidance. Unsupported batch scan/backtest targets no longer pretend success.
- Agent executors are stricter: unknown executor tasks fail, deterministic Decision Veto and Review executor slices exist, and Agent Layer still does not generate orders.
- Documentation synchronized: implementation matrix, technical architecture plan, validation methodology, risk safeguards plan, and ensemble README no longer claim missing modules that now exist.
- Verified locally: `py -3 -m pytest -q` (`45 passed, 1 skipped`), `py -3 -m ruff check .`, `py -3 -m ruff format --check .`, `py -3 -m mypy`, and `npm --workspace frontend/admin run build`.
- Not locally verified: Docker compose config/runtime, because `docker` is not available on PATH. GitHub push remains dependent on network/auth availability.

## Binance Data Layer First Tranche (TASK-014, 2026-07-03)

- Data Layer now has real Binance public-market ingestion seams for first-tranche BTC/USDT use: idempotent `ohlcv_bars` / `market_extras` writes, CCXT-based OHLCV and funding backfill services, and WS payload handlers that persist only closed Kline candles.
- `binance_ohlcv_backfill`, `binance_funding_backfill`, and `binance_live_market_collector` are recognized ingestion job types. `enqueue_binance_ingestion` is registered as a Celery task; backfill jobs write persisted data, while the live collector is a long-lived worker seam and is not a frontend push channel.
- `frontend/admin` now has a Vite dev proxy for `/api -> http://127.0.0.1:8000`, while `VITE_API_BASE_URL` remains an explicit override.
- Timescale init now includes unique indexes for market data idempotency and aligns `risk_events.resolution_status` with the repository.
- Verified locally: `py -3 -m pip install -e ".[dev]"`, targeted Data Layer tests (`11 passed`), changed-file Ruff check, full `py -3 -m pytest -q` (`41 passed`), and `npm --workspace frontend/admin run build`.
- Still not implemented: real order placement/cancel, account balance/position sync, order book persistence, notifications/alerts, news/social ingestion, LLM veto, and frontend WebSocket/SSE push.

## Phase 1a/1b/1d/1e Grounding Update (TASK-012, 2026-07-03)

- `services/data/` has been restored with repository, Binance helpers, application service, and task entrypoints; `.gitignore` now anchors root `/data/` and ignores `.pytest_ai_quant.db` / `*.egg-info/`.
- Dev install now excludes LLM libraries from the default `dev` extra; LLM deps remain optional under `llm`.
- Carry validation no longer uses hardcoded Sharpe/max drawdown/cost constants. It calculates net returns, PnL, Sharpe, max drawdown, profit factor, expectancy, win rate, cost breakdown, and a conservative DSR-style penalty from trade data.
- Negative net expectancy carry samples are rejected; this intentionally changed older tests that expected `conditional` despite failing real net metrics.
- SignalEnsemble / MetaLabel now has a deterministic service and API slice; MACD and Dow swing trend technical signals are implemented. Chan theory remains not implemented.
- WorldQuant alpha semantic evaluator is deferred per latest user instruction; keep only scan/intake seam in scope.
- Verified locally: `py -3 -m pip install -e ".[dev]"`, `py -3 -c "import services.data; import apps.api.main"`, targeted Phase 1 tests (`14 passed`), and full `py -3 -m pytest -q` (`31 passed`).
- Not locally verified: Docker Compose config, because `docker` is unavailable on PATH. Also this directory is not a Git repository, so commits / `git rm --cached` could not be performed here.

## Paper Trading Console Update (TASK-013, 2026-07-03)

- Added `MarketSnapshot`, `OhlcvSeriesResponse`, and `ConsoleOverview` read contracts.
- Added market and console read APIs for the Paper dashboard: `/api/v1/market/snapshot`, `/api/v1/market/ohlcv`, `/api/v1/console/overview`.
- Added Paper status and RiskEvent acknowledgement APIs for first manual controls.
- Rebuilt `frontend/admin` from a static shell into a Paper-first trading console with Binance symbol inputs, Kline chart, carry panel, orders, positions, risk events, and paper/manual controls.
- Added `lightweight-charts` as the frontend chart dependency.
- Verified locally: targeted API tests, full `py -3 -m pytest -q` (`35 passed`), `npm --workspace frontend/admin run build`, and Playwright desktop/mobile smoke. Real backend was not started during browser smoke, so the UI displayed its explicit API failure state as designed.
- Still not implemented: real Binance WebSocket collector, exchange account sync, real order placement/cancel, live trading operations, notifications, and LLM veto execution.

## Identity

- 项目名：AI Quant Research Platform
- 目标：实现研究报告定义的完整量化研究平台，而不是想法体检器或单纯回测脚本
- 主市场：第一阶段以 `BTC/USDT 永续` 为主
- 主语言：Python
- 管理后台：React + Tailwind

## Stable Constraints

- 报告是主架构真源
- 六层架构不可删层
- 风控系统必须是执行层核心，而不是后补
- Review Layer 必须每日回写策略库
- WorldQuant 只作为 E级研究数据与辅助来源

## Current Phase

- Phase 0：平台骨架、统一模型与设计冻结
- 现实进度：已进入“Phase 0 完成 + 第一批 P1 落地”状态，主链已具备可审计的研究闭环骨架

## Active Design Sources

- 第一层真源：研究报告
- 第二层真源：`AGENTS.md`
- 第二层实施母文档：`docs/architecture/platform-master-design.md`
- 开发入口索引：`docs/architecture/design-source-index.md`
- 工程落地细化：`AI_Quant_v2_集成方案开发任务书.pdf` + 对账文档 `docs/architecture/v2-integration-reconciliation.md`（docx 真源优先，冲突以 docx 为准）
- 已完成子设计：`docs/architecture/domain-and-interfaces-design.md`
- 已完成子设计：`docs/architecture/data-and-ingestion-design.md`
- 已完成子设计：`docs/architecture/agent-and-orchestration-design.md`
- 已完成子设计：`docs/architecture/execution-risk-review-design.md`
- 已完成子设计：`docs/architecture/validation-methodology.md`（Deflated Sharpe / walk-forward / 压力测试场景库 / 成本建模）
- 设计附录：
  - `appendix-a-repository-structure.md`
  - `appendix-b-feature-phasing.md`
  - `appendix-c-principles-and-non-goals.md`

## Additional Design Assets

- 上层产品定位：`docs/product/product-spec.md`
- 上层功能总表：`docs/product/feature-catalog.md`
- 开发验收真源：`docs/product/prd.md`
- 模块字段级承接：`docs/product/module-feature-catalog.md`
- 路线图：`docs/roadmap/phase-roadmap.md`
- 配置规范：`docs/ops/environment-and-config.md`
- 准备清单：`docs/ops/delivery-checklist.md`

## Planned Repository Structure

- `apps/api`（已接 strategies CRUD seam + config + celery_app）
- `frontend/admin`
- `shared/models`（统一 Pydantic 数据契约，跨层唯一真源）
- `services/data`
- `services/strategy_library`（已有 Strategy 18 字段 ORM）
- `services/agents`
- `services/validation`
- `services/execution`
- `services/review`
- `research_source/worldquant_adapter`（方法论移植接缝）
- `infra`（timescale/freqtrade/jesse/grafana）
- `migrations`（Alembic，关系表）
- `tests`（contracts + api）

## Phase-0 Scaffolding Status (TASK-005, 2026-06-29)

- 已落地：shared 契约、infra、docker-compose v2（8 服务）、TimescaleDB、Alembic+Strategy ORM、strategies CRUD seam、Makefile、pre-commit、CI、tests。
- 待运行环境验证：`docker compose up`、`alembic upgrade head`、`uv lock`（本地无 docker/make/uv）。
- 下一步：P0-03 `ohlcv_downloader.py`、P0-12 strategies repository（替换内存 seam）。

## Phase-0 收尾架构补强 (TASK-006, 2026-07-02)

用户基于外部量化建议反馈四点，逐一确认后落地为设计文档 + 契约字段（仍不写业务逻辑代码）：

1. 信号融合 + meta-labeling 归属 Strategy Library 子模块（`SignalEnsemble`/`MetaLabel`），不新增独立第7层。见 [domain-and-interfaces-design.md](C:\Users\Windows11\Desktop\量化项目\docs\architecture\domain-and-interfaces-design.md) §3.5a/§3.5b，契约在 `shared/models/signal.py`。
2. P1 优先级明确：资金费率/基差套利（波动率无关底仓策略）先于技术策略框架化落地，用于跑通整条 Validation Layer 流水线。见 [appendix-b-feature-phasing.md](C:\Users\Windows11\Desktop\量化项目\docs\architecture\appendix-b-feature-phasing.md)。
3. 新闻过滤/LLM 一票否决的具体触发规则已写入 [execution-risk-review-design.md §2.2a/§03a](C:\Users\Windows11\Desktop\量化项目\docs\architecture\execution-risk-review-design.md) 与 [agent-and-orchestration-design.md](C:\Users\Windows11\Desktop\量化项目\docs\architecture\agent-and-orchestration-design.md)（新增 Decision Veto Agent）。
4. 验证方法论（walk-forward/Deflated Sharpe/压力测试场景库）与成本建模（手续费/滑点/资金费率净收支）新增子设计文档 [validation-methodology.md](C:\Users\Windows11\Desktop\量化项目\docs\architecture\validation-methodology.md)，`BacktestRun`/`BacktestReport` 契约已补字段。
5. 对应 ADR：见 decisions-log.md ADR-011~014。

## Primary Deliverables For This Phase

1. 全局项目配置
2. 项目记忆体系
3. 统一领域模型
4. 后端主干
5. 初始前端骨架
6. 平台总设计包母文档
7. 领域与接口设计包
8. 数据与接入设计包
9. Agent 与任务编排设计包
10. 执行 / 风控 / 复盘设计包
11. 产品与路线规格包

## Current Executable Status (TASK-011, 2026-07-02)

- API 已统一到 `/api/v1`，列表接口使用 `items + total`，错误返回统一 `error_code/message/detail`
- 已落地真实持久化对象：`OptimizationRun`、`RiskProfile`、`ReviewReport`、`FailureRecord`、`AgentTask`、`LiveRun`、`OrderExecution`、`PositionSnapshot`
- `risk_events` 已从内存假实现切换为 Timescale-owned 持久化事件流
- 执行前 gatekeeper 已拒绝：无止损、validation 未通过、数据不新鲜、`veto=true`、高严重度风险事件
- Review Layer 已支持 `FailureRecord -> Strategy.failure_reasons + iteration_history` 回写
- `research_source/worldquant_adapter` 已具备本地 `alpha` 扫描器，可把研究源转成结构化 `StrategyIdea`
- `frontend/admin` 已不再是占位页，现为 React + Tailwind 管理台壳；本地 build 已通过
- `docker-compose.test.yml`、`docker-compose.paper.yml`、`docker-compose.live.yml` 已入仓，Prometheus/Grafana dashboard 资产已有首版骨架

## Local Console Reliability (2026-07-12)

- 本地交易台 API 固定使用 `127.0.0.1:8016`；`8000` 在当前 Windows 环境会出现可监听但不返回 HTTP 响应的异常，启动器不得再使用它作为默认端口。
- API 使用 `--local-console` 模式，只读本地 SQLite 的已落库行情、订单、仓位与账户快照；不得在页面读取请求中同步调用 Binance。
- 独立 `scripts/run-local-paper-scheduler.py` 持续执行行情心跳和自动 Paper 周期，写回同一 SQLite。状态 API 用 `logs/scheduler.pid` 显示该外部调度是否存活。
- 这属于 Data -> Execution -> Review 闭环：接口读取不中断、自动周期持续写入、币安验收成交与外部账户快照继续作为独立审计记录留存。

## Directional Paper Runtime (2026-07-12)

- Directional auto execution is Paper-only and scans the fixed Binance Top20. Its mandatory timeline is `4h trend -> 1h state -> 15m closed-bar entry`; `1m` is reserved for position protection and does not create entries.
- Existing positions must always be protected from a fresh 1m bar, even if entry-frame data is stale or the 15m entry bar was already processed. Missing multi-timeframe data blocks only new entries.
- Risk defaults for this lane: BTC/ETH/SOL max 20x; remaining Top20 max 10x; 2% stop-risk per trade; 15% aggregate initial stop risk (corrected 2026-07-13 per ADR-058 — `max_portfolio_initial_risk_fraction=0.15` in `services/execution/bootstrap.py`'s `AUTO_PAPER_TECHNICAL_RULES`, not the previously-recorded stale 5%); 5% daily loss blocks new entries; 20% peak drawdown force-closes and locks the PaperRun pending manual recovery.
- Profit exits: break-even at +1R, close 50% at +2R, retain the balance under a ratcheting stop, and close after 24 hours if favorable movement is below +0.5R. Costs are 10bps/side for core symbols and 18bps/side otherwise; stress validation still requires 1.5x cost evidence before Testnet promotion.
- LLM/RAG may enrich decision evidence and review data but cannot set direction, price, leverage, sizing, or stops. Only persisted high/critical `RiskEvent`s veto an entry; LLM unavailability is auditable but cannot bypass deterministic risk gates or itself halt a valid rule entry.

## Phase-0 开发前完整方案包 (TASK-007, 2026-07-02)

用户要求做第二轮全局查缺补漏，并交付一整套开发前方案文档（技术架构/PRD/模块功能清单/
策略库机制/LLM接入/24小时运行/外部数据源/风控），全部写入 `docs/` 作为正式仓库文档，
分批交付。8 份文档已全部完成：

1. [technical-architecture-plan.md](C:\Users\Windows11\Desktop\量化项目\docs\architecture\technical-architecture-plan.md) — 六层到物理部署的映射、四环境 compose 拓扑规划、API/队列/存储/配置技术规则、§12 已知技术缺口追踪表（当前仓库骨架与目标态之间的具体缺口清单）。
2. [prd.md](C:\Users\Windows11\Desktop\量化项目\docs\product\prd.md) — 产品愿景与反成功标准、两角色、六个人工决策点作为产品硬约束、七模块用户故事+验收标准。
3. [module-feature-catalog.md](C:\Users\Windows11\Desktop\量化项目\docs\product\module-feature-catalog.md) — 逐模块功能点表，精确交叉引用 domain-and-interfaces-design.md 的对象/接口簇字段名。
4. [strategy-library-collection-and-scoring.md](C:\Users\Windows11\Desktop\量化项目\docs\architecture\strategy-library-collection-and-scoring.md) — 六类来源分类、单策略/组合两级评分机制、六类淘汰触发信号，只设计机制不写具体参数。
5. [llm-integration-plan.md](C:\Users\Windows11\Desktop\量化项目\docs\architecture\llm-integration-plan.md) — 逐 Agent LLM 使用边界与禁止清单、四段式 Prompt 结构、成本控制、LangChain/LlamaIndex 仅服务 Research Agent 的 RAG 边界，只做方案不写代码。
6. [24x7-operations-plan.md](C:\Users\Windows11\Desktop\量化项目\docs\architecture\24x7-operations-plan.md) — 进程/连接/数据三层监督模型、心跳与指数退避重连、五级降级状态机、通知告警分级规则。
7. [external-data-source-integration-plan.md](C:\Users\Windows11\Desktop\量化项目\docs\architecture\external-data-source-integration-plan.md) — 五级数据源的具体 API/SDK/拉取频率/存储路径，补全 data-and-ingestion-design.md 有意留白的具体参数。
8. [risk-control-and-safeguards-plan.md](C:\Users\Windows11\Desktop\量化项目\docs\architecture\risk-control-and-safeguards-plan.md) — 裁决此前四处留白：LLM 否决超时选"超时即否决"、Key 权限自检强制拒绝启动规则、`RiskProfile` 具体阈值默认值、熔断触发规则具体化。

对应 ADR：见 decisions-log.md ADR-015~018。

## Design Convergence Blueprint (TASK-008, 2026-07-02)

- 新增 `docs/architecture/design-source-index.md` 作为开发入口索引，固定真源层级、Phase 语义、文档职责边界。
- 明确 `Phase 0` = 平台骨架 + 统一模型 + 设计冻结；`appendix-b-feature-phasing.md` 中的 `P0/P1/P2` 仅是实现 tranche 标签。
- README / `report-alignment.md` / 产品上层文档已同步说明：`product-spec.md` + `feature-catalog.md` 负责上层定位，`prd.md` + `module-feature-catalog.md` 负责开发验收。
- `策略库/笔记.docx` 被正式定义为研究素材池，只能通过 `StrategyIdea -> StrategyDraft -> StrategyContract` 流程进入主链路。
- 代码层补齐：`RiskProfile` 契约、工作流生命周期对象契约、六大接口簇 API skeleton、全量 `Settings` 环境变量入口、`services/data` 包骨架。
## First Persisted Vertical Slice (TASK-009, 2026-07-02)

- The repository now has persisted SQLAlchemy-backed repositories for `StrategyIdea`, `StrategyDraft`, `Strategy`, `StrategyVersion`, `BacktestRun`, `IngestionJob`, and `PaperRun`.
- `apps/api/routers/{strategies,backtests,ingestion,runs}.py` now use real repositories via `get_db_session` instead of in-memory dictionaries.
- Binance A-level helpers now exist in `services/data/binance.py` for `Top20` universe selection, OHLCV normalization, and funding-rate normalization.
- `services/data/service.py` defines the current fallback Binance `Top20` universe list for ingestion jobs until live exchange ranking is wired in.
- `services/validation/carry.py` contains the first carry backtest service with settlement-window handling, spot/perp/funding cost reconciliation, and `conditional` gate behavior when deflated Sharpe is absent.
- `services/execution/paper.py` makes `BTC/USDT` and `ETH/USDT` the default first simulated symbols for paper preparation.
- Celery now has first real task entrypoints for ingestion, backtests, and paper runs.
- Alembic `0001` now manages the relational tables for this slice instead of only the original `strategies` table.
- Current verified path: `StrategyIdea -> StrategyDraft -> Strategy -> StrategyVersion -> BacktestRun -> GateDecision -> PaperRun`.

## Persisted Carry Application Flow (TASK-010, 2026-07-02)

- `services/data/repository.py` now provides the first real timeseries repository for `ohlcv_bars` and `market_extras`, including store/list helpers plus gap and freshness checks for Phase-1 carry validation.
- `shared.models.CarryBacktestRequest` is now the application-layer submission contract for the persisted carry lane.
- `services/validation/application.py` now defines `CarryBacktestApplicationService`, which loads persisted spot/perp/funding data, applies settlement-window data-quality checks, runs the carry backtest, and persists the resulting `BacktestRun`.
- `apps/api/routers/backtests.py` now exposes `/backtests/carry` as the first API path that executes a backtest from persisted market data instead of accepting only a pre-built `BacktestRun` payload.
- `services.validation.tasks.enqueue_carry_backtest` is wired as the queue-side entrypoint for the same application flow, but local import smoke for Celery remains pending until the environment installs `celery`.

## Binance Simulation Runtime Status (2026-07-16)

- The local scheduler on port `8016` is running against the SQLite console database. The carry, directional, and swing automatic runs scan the fixed Top10 and are armed as `binance_simulation_first`; deterministic validation, stop-loss, risk, and cost gates still decide whether any individual signal can submit.
- Binance Mock Trading is the execution proof target. The configured `demo` mode can fall back to `https://testnet.binancefuture.com` when `demo-fapi` is unreachable; this fallback is the same simulation account whose order ids appear in the Binance Demo Trading UI. Mainnet remains disabled.
- External verification uses the isolated `link_verification` run only. It is excluded from strategy-performance evidence, must be paused after an E2E test, and its account must be flattened. `scripts/verify_config.py` now requires a persisted automatic `gateway_order_id`, successful Binance simulated-account reconciliation, and `LIVE_TRADING_ENABLED=false` before returning GREEN.

## Exchange-First Runtime Truth Baseline (2026-07-27, refreshed 2026-07-28)

- Runtime execution mode is explicit: `local_paper` or `binance_testnet`. Legacy Paper names are compatibility labels, not proof of exchange execution.
- A Binance Testnet managed position requires an exchange order, immutable fill receipt, trade IDs, position group, and confirmed fill quantity/average price. Local Paper positions are kept out of Binance reconciliation comparisons.
- Entry reconciliation is typed and fail-closed. Its three-failure kill switch persists in `PaperRun.paper_metrics_summary`; a healthy snapshot cannot clear it while any exchange order remains `EXCHANGE_UNKNOWN`.
- Current local schema head is Alembic `0015`. Runtime Truth is available under `/api/v1/runtime/*` with authenticated WebSocket events and explicit source/time/freshness/unavailable states.
- Cycle private-API load was reduced on 2026-07-28: `sync_paper_account_equity` no longer repeats `account_equity()` after a successful `sync_account`; `BinanceUsdtPerpetualGateway.reconcile` defaults to BTC/ETH open-order scans and filters positions/algo orders to that universe; hard-drawdown locking reuses the cycle reconcile snapshot.
- 2026-07-28 authorized Testnet flatten closed unmanaged ETH short (`gateway_order_id=14774000974`); exchange BTC/ETH open positions and open orders are again `0`. Local unmanaged/ghost rows remain quarantined as `RECONCILED_GHOST` where applicable.
- Observed after the latency fix: ordinary Scheduler evaluated closed 15m bar `2026-07-28T02:15:00Z` by `02:17:17Z` without `PRETRADE_DECISION_STALE` (prior failure was `age=706s`). Terminal reasons were `TECHNICAL_SIGNALS_INSUFFICIENT` / Sampling `SAMPLING_RULES_NOT_ALIGNED` — signal silence, not execution-path timeout.
- **P0.3 remains incomplete** until a natural receipt-backed Entry → SL/TP exchange IDs → normal Reduce-risk Exit ends at local `0` == exchange `0`. P1/P2 promotion and P3 stay locked.

## Automatic Trading V2 Natural Lifecycle Closure (2026-07-30)

- P0.3 is now verified complete on Binance USDT-M Testnet through the normal Scheduler path. Three natural managed positions produced exchange Entry fills, protection algo orders, protection-triggered reduce-only Exit fills, local `CLOSED`, and `PROTECTION_FILLED`.
- Evidence: ETH `14979020372 -> 14984144295`, BTC `24887097073 -> 24906752342`, ETH `14985524359 -> 14988841518`. These are Testnet order IDs and are not profitability evidence.
- The production fix set recovers delayed ACK/fills, resolves Binance protection `algoId -> actualOrderId`, projects quarantined positions closed only from matching persisted reduce-only fills, and resolves only ghost/external mismatch incidents after a fresh `HEALTHY` reconciliation.
- Runtime truth at final acceptance: Binance open positions `0`, Binance open orders `0`, local V2 open positions `0`, reconciliation `HEALTHY`; the API, RuntimeScheduler, frontend, and `scripts/watch_v2_runtime.ps1` watcher remain online.
- Frontend `/trading` renders V2 Engine Status, Why No Trade, and Exchange vs Local from the real Runtime API. Cold-load sampling showed two React development initialization requests followed by stable `~10s` polling; console had `0` errors and two non-blocking framework/chart warnings.
- LLM runtime truth exposes `called/provider/model/tokens/latency_ms/result`; a no-candidate cycle records `called=false, result=skipped, skip_reason=NO_CANDIDATE`, while provider failures are retained as attempted calls rather than disguised skips.
- Final deterministic verification: Ruff clean; mypy clean for 206 source files; pytest `1181 passed, 16 skipped`; frontend Vitest `16 files / 65 tests`; Vite production build passed.
- Residual risk: one historical `PROTECTION_RECOVERY_FAILED` incident remains `OPEN` for manual review by design. Strategy optimization is a separate next phase and must preserve the current 5%/40x Testnet-only sampling authorization and validation gates.

## Strategy Readiness Gate (2026-07-30)

- Current strategy verdict is `DATA_READY / ACTIVE_STRATEGY_REJECTED / OPTIMIZATION_NOT_READY`, documented in `docs/audit/strategy-readiness-2026-07-30.md`.
- The corrected pre-parity artifact covers BTC/ETH continuously across 1m/5m/15m/1h/4h through the 2026-07-29 cutoff; its Final Holdout remains sealed and unaccessed. The artifact is historical after the Phase 1 replay semantics change.
- Legacy technical replay still does not model funding, latency, spread, or partial fills, and the existing IID percentile bootstrap is not a valid final promotion CI. Do not optimize or change thresholds until those gates are repaired.

## Final V2 Drain Re-verification And Runtime Restore (2026-07-30)

- Normal RuntimeScheduler protection handling closed the latest BTC/ETH managed shorts without a manual close or direct cycle invocation. BTC reduce-only stop actual order `24933450773`, trade `522947770`, filled `0.0049 @ 64190.0`; ETH reduce-only stop actual order `15006608596`, trade `309176628`, filled `0.165 @ 1911.67`.
- BTC position `953a6487-3310-4c44-a7c8-916ebf032c8c` and ETH position `217bc8f7-9195-426d-914a-b5542e492558` are `CLOSED`; both protection records are `PROTECTION_FILLED`.
- Independent Binance Testnet probe returned positions/orders `0/0`; Runtime local open positions `0`; reconciliation `HEALTHY` with zero mismatches.
- Entries were restored through `POST /api/v2/automated-trading/controls/entry-enable`, reason `phase2_final_acceptance_complete`; live Runtime verified `entry_enabled=true`. No risk, leverage, stop/take-profit, net-edge, or strategy threshold changed.

## Strategy Phase 1 next-bar parity (2026-07-31)

- The first methodology slice is implemented in `services/validation/technical_replay.py`: replay evaluates a signal on a closed bar and fills at the following bar's open/timestamp; a final-bar signal without a following bar is not fabricated into an end-of-window trade.
- Regression evidence: focused replay tests `14 passed`; related validation tests `21 passed`; touched Ruff and production mypy passed.
- The corrected `baseline-20260729-0000Z-r4` remains a historical pre-parity artifact and must not be treated as source-hash-current after this code change. No new baseline or Final Holdout result was generated.
- Remaining Phase 1 work: shared point-in-time MarketSnapshot/features, live/replay input-hash parity, funding/spread/latency/partial-fill costs, per-window walk-forward ledgers, and dependent/block bootstrap. Do not tune thresholds or read Holdout results.

## Strategy Phase 1 proposal closure and Gate 16 contract (2026-07-31)

- Runtime V2 Shadow and replay share one canonical proposal pipeline, selector semantics, canonical hash and `2/2/80/80/80` context windows. Shadow persists only `research_shadow`; it creates no execution intent/order/position.
- The isolated Binance Vision research database contains BTC/ETH 5m/15m/1h/4h through the frozen cutoff with verified checksums and 3,918 funding events per symbol. Provenance: `artifacts/strategy_refactor/history/phase1-history-20260729-provenance.json`.
- Eight independent three-month OOS windows use a preceding twelve-month train boundary, 80-bar purge and 24-hour embargo. The append-only ledger records real candidate/window/symbol metrics; no parameter tuning, Final Holdout, DSR/PBO, bootstrap promotion test or promotion is run.
- Funding uses signed settlement events; fee provenance is existing runtime config. Spread, latency and partial fill remain `ASSUMED`, therefore all Phase 1 results remain not promotion eligible and the final verdict is `NO_ACTIVE_STRATEGY`.
- Immutable artifact: `artifacts/strategy_refactor/phase1-20260731-0000Z-r1`; it binds source/data/config/pipeline hashes and records `holdout_results_accessed=false`.
- Gate 16 Testnet contract passed with entry `25631813075` / trade `523375938`, reduce-only exit `25631829915` / trade `523375957`, and final BTC positions/orders `0/0`. This is `TESTNET_CONTRACT`, `natural_strategy=false`, not strategy evidence. Gate 17 was not run; launcher stays `v2_shadow`.

## Gate 17 sampling execution boundary and preserved external baseline (2026-08-06)

- V2 sampling candidates remain `SAMPLING`/non-promotable, but the active
  Binance Testnet lane now permits their real Exchange-First submission only
  when facts are persisted, execution mode is `BINANCE_TESTNET`, and activation
  is `ACTIVE`. Shadow, local Paper, and unit runs remain trace-only.
- When `V2_ALLOW_UNMANAGED_EXTERNAL_POSITIONS=true`, a captured baseline is
  preserved and only a same-direction V2 increment is eligible. Opposite-side
  candidates are rejected with `UNMANAGED_EXTERNAL_POSITION` because the
  account is treated as one-way; no code path may reduce or reverse the
  operator's baseline to create a Gate 17 sample.
- Official natural observer evidence at
  `docs/evidence/automated_trading_v2/natural_cycle_20260805T172437Z.json`
  ran for 60 minutes with `ACTIVE`/Testnet/healthy reconciliation but timed
  out without a same-direction natural Entry. Baseline remained
  `BTC/USDT:short=0.5302` and `ETH/USDT:short=6.814`; Gate 17 is still open.

## Gate 17 runtime authorization continuation (2026-08-06)

- Launcher `-EnableNaturalTestnet` now propagates the explicit observer-only
  `V2_NATURAL_E2E_ENABLED` flag; default Shadow launch removes it.
- After restart, the live API and isolated Scheduler are healthy in
  `ACTIVE/BINANCE_TESTNET` mode with `entry_enabled=true`, and the official
  observer is running read-only. Real 15m cycles at 17:45 and 18:00 UTC
  reached Sampling candidates but were LONG while the preserved Binance
  one-way baseline is SHORT, so they were fail-closed as
  `UNMANAGED_EXTERNAL_POSITION` with zero exchange writes.
- Gate 17 remains OPEN until a natural same-direction short entry completes
  the full receipt/protection/reduce-only-exit/baseline-restore chain.

## P1 Same-Cycle Research Shadow (2026-08-10)

- **Root cause fixed**: the real ACTIVE V2 scheduler now appends same-cycle,
  same-symbol, same-closed-15m-bar observations for
  `trend_pullback_v2`, `range_sweep_reversion_v1`, and
  `failed_breakout_reversal_v1` to the existing V2 decision payload.
  `testnet_sampling_v2` remains the sole ACTIVE submit authority.
- **Isolation**: ACTIVE symbol cycles finalize before the observer runs; the
  V2 writer/slot leases are released before evidence evaluation/appending.
  The observer receives no adapter, intent, order, position, or protection
  writer. Research candidate errors become `SHADOW_STRATEGY_ERROR` evidence.
- **Runtime evidence**: final cutover `2026-08-10T13:15:03.869648Z`; 192 new
  observations, 192 same-cycle matches, 0 unmatched, and all six research
  mutation ledger counters 0. BTC naturally reached
  `POSITION_ALREADY_OPEN`; ETH shared the same closed 15m bar with an
  independent ACTIVE terminal result. `trend_momentum_v2_enriched` remains
  out of scope.
- **Account safety**: the launcher was used twice with ACTIVE/Testnet and
  preserved external baseline. During the final observation an existing ETH
  protection exit naturally reduced Binance/local positions from 2 to 1;
  no new Shadow order/intent/position/protection lineage was found and
  reconciliation remained `HEALTHY`.
- **Verifier hardening**: top-level ACTIVE strategy identity and
  `v2_execution_cycles.execution_mode == BINANCE_TESTNET` are now required
  for accepted examples and same-cycle matches.
- **Verification**: P1 focused `30 passed`; P0/U1 `79 passed, 1 skipped`;
  full `pytest -q` `1439 passed, 7 skipped, 2 known candidate-registry
  failures`; touched Ruff clean; mypy `225` source files clean. Full Ruff
  retains 3 unrelated pre-existing script findings. Final status is
  `IMPLEMENTATION_COMPLETE_PENDING_REVIEW`; stop before P2.

## 2026-08-12 — Canary continuity review closure

- The Canary direction guard applies before entry gating for every authority:
  a candidate opposite to an explicitly captured unmanaged one-way Testnet
  baseline fails closed as `UNMANAGED_EXTERNAL_POSITION`; it must never reduce
  or flip operator exposure.
- Runtime Truth REST and WebSocket decisions now read only persisted V2
  decision facts. The normalized record includes last decision time, strategy,
  authority, signal, gate stage, exchange submission, and terminal reason; do
  not reintroduce the legacy `DecisionFunnelRepository` to this screen.
- The scheduler publishes the authority resolved by the completed V2 cycle,
  rather than preserving startup state. A Production approval or Canary toggle
  therefore changes Runtime Truth on the next cycle.
- Canary persistence remains `candidate_type=SAMPLING` and
  `promotion_eligible=false`. The live API after the official launcher reported
  `TESTNET_CANARY`, `PENDING`, `TRADING`, and the BTC terminal
  `DUPLICATE_DECISION`; Production authorization was not altered.
- Verification: targeted V2/API/scheduler tests `72 passed`; frontend full
  Vitest `105 passed`; frontend build and full mypy `236 source files` passed;
  independent review PASS. Full pytest was `1539 passed, 16 skipped, 2 failed`
  only on pre-existing candidate-registry expectations of 9 candidates (actual
  10). Full Ruff retains the documented three unrelated script findings.

## Testnet 50x / 5% margin sizing contract (2026-08-13)

- The operator-approved Testnet entry semantics are **50x leverage plus a
  per-entry margin ceiling of 5% of current equity**, not 5% notional. V2 caps
  new-entry notional at `equity * max_margin_fraction * max_leverage`; at
  7,349 USDT that is 367.45 USDT margin and 18,372.50 USDT notional per symbol.
  The separate `risk_per_trade=0.10` stop-loss budget remains unchanged and can
  only make an entry smaller.
- Active PaperRun `35298c65-cdbe-4bee-bee3-b7ded07c3204` resolves BTC/ETH as
  `max_leverage=50`, `max_margin_fraction=0.05`,
  `max_position_fraction=2.5`, `max_total_exposure=5.0`; this persisted profile
  remained unchanged after the official `v2_active` Testnet launcher restart.
- Existing ETH short and its two reduce-only protections are untouched by the
  sizing cap. The sizing code affects new entries only; no forced Testnet order
  was submitted, so a natural 50x/5%-margin Binance receipt remains pending.
- Regression protection: the sizing apply script now refuses to overwrite any
  pre-existing pending ConfigSnapshot, and bootstrap preserves an existing
  operator-owned medium risk profile instead of restoring defaults on restart.

## 2026-08-13 — Frozen P0/P1 cloud lift-and-shift and no-trade observability

- P0 deployment layer reuses the existing API and `v2_active` RuntimeScheduler
  entrypoints with systemd restart/boot recovery, persistent SQLite/runtime
  state, and Testnet-only fail-closed preflight. No V2 trading business files
  were changed.
- P1 added deterministic `/api/v1/runtime/no-trade-summary`; duplicate
  decisions are excluded from effective counts, and only non-reduce-only fills
  inside the query window count as recent entries.
- PaperConsole now shows account/position/PnL/recent activity plus Chinese
  no-trade monitoring; full Runtime Truth remains on OpsConsole. Shared
  formatter owns no-trade labels.
- Verification: frontend `112 passed`, build passed; mypy `236` source files
  passed; focused API/deploy suites passed. Full pytest remains `1571 passed,
  16 skipped, 2 failed` only on pre-existing candidate-registry count assertions
  (expected 9, actual 10). Full Ruff retains three unrelated script findings.
- Boundary: no strategy/risk/Binance semantics, second scheduler, second DB,
  Celery writer, or external Linux VM validation was performed.

## 2026-08-13 — Aggressive Multi-Regime V1 implementation pending OOS

- Strategy-layer implementation is present but not production-authorized: `execution-manifest.yaml` remains `IMPLEMENTATION_COMPLETE_PENDING_OOS`, `final_status=BLOCKED_PENDING_OOS_AND_TESTNET`, and `production_authorization=PENDING`.
- Added higher-timeframe regime weights (4h/1h/15m = 0.45/0.35/0.20), Breakout Continuation research candidate, selector conflict/score gates (0.08/0.58), deterministic V2 single-target projection, score-tier sizing capped by operator risk, and replay `v2_single_target` mode.
- Research runner now evaluates four proposal candidates with single-target geometry. Aggressive registry candidate remains `RESEARCH_ONLY` and `execution_eligible=false`; authorization fails closed until approved evidence and operator authorization exist.
- Full repository verification on 2026-08-13: `1584 passed, 16 skipped, 2 warnings`. No cycle/entry/exit/protection/reconciliation/recovery/Binance adapter files were changed.
- This is not `RESULT_ACCEPTED`: no eight-window OOS promotion evidence or seven-day natural Binance Testnet forward evidence exists yet.

## 2026-08-14 — Binance Testnet history audit and loss-aware strategy loop

- Read-only audit completed from the first available exchange trade
  `2026-07-07T16:52:55.936Z` through `2026-08-14T07:30:35.847Z` for BTC/USDT and
  ETH/USDT. Exchange `userTrades` plus income are authoritative for fills, fees,
  realised PnL and funding; orders/algo-orders are best-effort because Binance
  retention can omit older unfilled/cancelled history.
- Canonical artifacts are under `artifacts/trading_audit/`: raw exchange/local
  facts, `exchange_fills.csv`, `trade_episodes.csv`, `strategy_decisions.csv`,
  `unmatched_records.csv`, completeness, live evaluation, loss attribution,
  cost-stress and optimization reports.
- Completeness: 449 exchange trades, 282 orders, 575 income records, 204 algo
  orders, 141 local V2 fills, V2 fill match rate `1.0`, 308 unmatched records;
  status `PASS`.
- V2 entry-matched live result: 25 closed episodes, net PnL `-454.11402372` USDT,
  commission `172.83684269`, funding `-88.23480101`, PF `0.3370`, expectancy
  `-18.16456095`, win rate `48%`, max drawdown `509.94780122` USDT. Top loss cause
  is `STOP` (12 episodes, `-675.23621504` USDT); regime/score buckets were not
  persisted for those matched decisions. Reconciliation defects: 1773; open
  incidents: 12. Slippage and latency remain explicitly unmeasured.
- Generation N+1 research candidate `loss_aware_trend_pullback_v1` was added to
  the pure proposal pipeline only. It failed OOS (1,099 trades, PF `0.7081`,
  expectancy `-0.001229`). Best nonzero candidate was `trend_pullback_v2`
  (405 trades, PF `0.8210`, expectancy `-0.0007721`), also rejected; cost stress
  stayed negative from 0 to 20 bps/side. No live counterfactual was measured.
- Final status: `AUDIT_PASS / STRATEGY_NOT_ACCEPTED / EXECUTION_FROZEN`.
  No Binance adapter, scheduler, intent/order writer, protection, reconciliation,
  recovery, risk, or execution semantics were changed by this audit loop.

## 2026-08-14 — Edge-First Event method also rejected

- Replaced strategy-family guessing with a read-only Event -> Outcome -> Quality
  Gate experiment. The dataset contains `HTF_STRUCTURE_BREAK` and
  `HTF_BREAK_RETEST` events only; entries use next 15m open, fixed 1R stop / 1.5R
  target, and equal-risk outcomes.
- Eight 12-month-train / 3-month-OOS windows learned a finite gate from training
  only. Development ended at the sealed boundary `2026-01-29T00:00:00Z` and the
  report records `holdout_accessed=false`.
- Preliminary (superseded) aggregate frozen-gate OOS: 172 trades, win rate `44.19%`, Net-R payoff `1.2089`,
  PF `0.9570`, expectancy `-0.02698R`, max drawdown `23.08R`, positive windows
  `3/8`. Status: `STRATEGY_EDGE_NOT_FOUND`.
- No candidate was registered or armed; execution semantics, risk ceilings,
  protection geometry, scheduler, and Binance adapter were untouched.

### Correction after independent review

- The first Event-First report was not used as final evidence. Independent review
  found entry-bar omission, overlapping events, non-chronological drawdown, and a
  gate selector that could retain negative training expectancy.
- Those defects were fixed. The corrected replay uses the entry 15m bar, one-symbol
  occupancy, chronological metrics, nested train/validation gate selection, LCB95,
  and cost-stress PF checks. Corrected result: 1,258 events, 23 selected OOS trades,
  43.48% win rate, 1.178 payoff, PF 0.9065, expectancy -0.06098R, LCB95 -0.5826R,
  positive windows 0/8, `STRATEGY_EDGE_NOT_FOUND`.

## 2026-08-14 — Market Alpha + Meta-Label source exhausted

- Replaced overlapping OHLCV strategy tuning with public Binance spot/perpetual 1h
  data, taker-buy pressure, spot/perp basis, point-in-time funding, BTC/ETH lead-lag,
  equal-risk outcomes, and nested Logistic/GBM probability gating.
- Corrected the first replay's two methodological defects: train/validation/OOS rows
  are now time-ordered rather than symbol-block ordered, and validation threshold
  selection no longer requires final promotion metrics (only expected Net-R after cost
  plus a 15-trade minimum). Final gates remain locked and are evaluated separately.
- Development artifact: 37,856 events, 40 arms, 8 windows, `holdout_accessed=false`.
  Only 6 arms configured any windows and 3 produced OOS trades; the best non-zero arm
  had 4 trades, 50% win rate, payoff 1.23, PF 1.23, expectancy +0.14R, LCB95 -1.43R,
  1/8 positive windows, and 1.5x-cost expectancy +0.02R with stress PF 1.04.
  `accepted_arms=0`.
- Final status: `CURRENT_MARKET_ALPHA_SOURCE_EXHAUSTED`. No candidate registration,
  promotion, Testnet order, execution-plane change, or holdout read occurred.

## 2026-08-14 — Generation Next closed with no historical edge

- R-normalized payoff audit completed for all 25 V2 episodes: winner average net
  `+0.7809R`, loser average net `-1.3537R`, loser/winner initial-risk ratio
  `1.7716x`, total cost drag `261.07164370` USDT, stop average MFE `0.4553R`,
  average giveback `0.9982R`.
- Five research generations were run on the existing eight temporal walk-forward
  windows. HTF continuation, breakout-retest, volatility expansion, Donchian
  breakout-retest, and momentum continuation all failed standalone OOS gates;
  Generation 5 was `2109` trades, PF `0.82594`, expectancy `-0.0013823`, LCB
  `-0.0020221`, max DD `292.66%`, positive windows `1/8`.
- Final artifact `artifacts/trading_audit/reports/optimization_result.md` is
  `STRATEGY_EDGE_NOT_FOUND`. No candidate was armed, no live counterfactual was
  claimed, and old STOP avoidance was not estimated from a rejected strategy.
- Execution regression suite passed (`1596 passed, 7 skipped`); targeted V2
  execution suite passed (`199 passed`). Execution research changes remain
  strategy-only; reconciliation defects `1773` and open incidents `12` remain a
  separate audit requirement.

## 2026-08-14 — Forward Baseline snapshot contract added

- Added `v2_decision_snapshots`, `v2_shadow_records`, and
  `v2_shadow_outcomes` as hash-sealed append-only V2 evidence tables
  (migration `0021`). Repository writes reject divergent rewrites and ORM
  updates are fail-closed.
- Each completed V2 cycle now captures exact closed bars, strategy commit/version,
  config hash, decision/funnel/features, candidate payload, risk inputs, and
  cost inputs. Opportunity cycles append `ACTUAL`, `R1_SHADOW`, `R2_SHADOW`,
  and `R3_SHADOW`; no Shadow row has an execution side effect.
- `scripts/verify_forward_baseline.py` replays snapshots deterministically and
  reports the required 100-cycle gate with first divergence points. Runtime
  schema was upgraded `0020 -> 0021`, but no post-change natural cycles have
  been captured, so status remains `FORWARD_BASELINE_NOT_REPRODUCIBLE`.
- No strategy rule, threshold, leverage, stop/target, exchange adapter, or
  order semantics were changed by this baseline work.
- Confirmed reduce-only close persistence now appends ACTUAL/R1/R2/R3 outcome
  payloads (`gross_R`, `net_R`, `commission_R`, `funding_R`, `MFE_R`, `MAE_R`)
  from the confirmed exchange fill; no local synthetic fill is used.

## P2-A 退出策略影子评测（2026-08-16）

- [事实] 现成 `services.research.exit_policy_shadow.cli` 已对 30 笔真实
  `testnet_sampling_v2` CLOSED 入场完成 1m replay；A 当前控制策略仍为观察期
  最佳，但整体 PF `1.11`、净期望 `-7.85 USDT`，Q1/Q2 均未达到支持性结论，Q3
  因 regime slice 样本不足而 `INSUFFICIENT_SLICE_SAMPLE`。
- [事实] 同一 30 笔 V2 真实账本的 R 分量为：毛 `+0.07374R`、已记录进出场成本
  `0.22834R`、净 `-0.15460R`；名义 1.5R/-1R 几何按 15/15 胜负的成本后理想
  `+0.02166R`，实际毛结果另有 `-0.17626R` 几何/成交短缺。成本是最大单项拖累，
  但不能据此直接改几何，仍需更大样本和真实 funding。
- [事实] 30 笔中 11 笔跨过至少一个 funding settlement boundary；完成的只读
  Binance income 审计显示 32 个 V2 闭合 episode 的 commission 为
  `271.53670323 USDT`、funding 为 `-134.96894383 USDT`、净 PnL 为
  `-565.22143738 USDT`、PF `0.39659635`。本地 V2 成本字段仍没有逐笔 funding
  字段，`calculate_cost_gate` 默认 `funding_bps=0` 且调用点未传值；因此 funding
  已确认是实质成本，但暂不直接改 gate。点时窗口匹配已尝试，因 account-level
  income 无法唯一归因到策略 position，当前结论为 `INSUFFICIENT_DATA`。报告：
  `docs/audits/2026-08-16-testnet-history/reports/live_strategy_evaluation.json`。
- [事实] P2-B 三个候选在旧 `v2_shadow_records` 表中均为 0 条，但同周期 shadow
  实际嵌在 `v2_execution_decisions.payload.research_shadow`，不能把旧表查询结果
  当成“未接线”。
- [事实] `127.0.0.1:8016/health` 当前 HTTP 200；历史 unavailable 记录为陈旧或
  认证路径状态，不再阻塞离线研究。证据产物：
  `docs/audits/2026-08-11-p2a-exit-policy-shadow-results.json`、
  `docs/audits/2026-08-16-p2a-actual-decomposition.json`、
  `docs/audits/2026-08-16-p2a-component-summary.json`。

## 2026-08-16 — PF cohort lock and funding attribution boundary

- [事实] P2-A Policy A replay PF `1.11229903`、同 30 笔 exchange-fill 对齐的
  pre-funding PF `0.50914947`、32 episode account audit PF `0.39659635` 是
  三个不同口径；不得把 `0.3966` 直接拿来解释 30 笔 replay。
- [事实] 当前 30 笔 decomposition 的 normalized-R PF 是 `0.75334826`。
  旧 handoff 中的 `0.48688` 在当前仓库/产物中不存在，来源未恢复前标记
  `UNSUPPORTED_LEGACY_VALUE`。
- [事实] funding 只完成了按 entry/close 时间窗的 account-income 匹配：3 个
  event、合计 `+1.33323099 USDT` 的朴素窗口和；快照上下文均陈旧或存在
  外部/矛盾持仓，无法形成策略 position 唯一归因。状态为
  `FUNDING_WINDOW_MATCHED_ACCOUNT_LEVEL_AMBIGUOUS`，不得据此跑 funding-only
  实验。证据：`docs/audits/2026-08-16-p2a-funding-attribution.json`。
- [事实] P2-B 三个候选实际写入
  `v2_execution_decisions.payload.research_shadow.observations`；cutover 后
  5,607 个 unique observation，不是 0 条。trend/range 各 1,869 条且全为
  `SHADOW_NO_SIGNAL`；failed-breakout 有 16 条 `SHADOW_SIGNAL_READY`、5 条
  `SHADOW_STRATEGY_REJECTED`。证据：
  `docs/audits/2026-08-16-p2b-embedded-shadow.md`。

## Runtime-Parity + P1 动态保护审计（2026-08-16）

- [事实] 同一 30 笔 cohort 的 R0 现有 Policy A replay 精确复现
  `PF(gross)=1.11229903`；用真实 protection record 的 exchange-rounded stop/target
  截至真实 close 做 R1，均值 `0.291423R` gross；加入保守 next-bar P1 动态保护做
  R2，均值 `0.249455R` gross；真实 exchange R3 为 `0.073741R` gross、
  `-0.154600R` commission-net。
- [事实] P1 的 R1->R2 影响为 `-0.041968R`，而 R2->R3 仍为 `-0.404055R`。
  因此 P1 不是当前 replay->真实成交大缺口的主解释；下一步先做 exit
  order/fill 几何与时序 attribution，不先改 funding 或 direction signal。
- [事实] 模拟 P1 在 21/30 笔触发（12 笔未达到静态 target），但历史 protection
  event 中 `ProfitProtectionStopTightened=0`；`policy=P1` 标签不能替代 replacement
  事件证据。报告：`docs/audits/2026-08-16-runtime-p1-parity.json`。

## Exit order/fill lineage and current P1 receipt (2026-08-16)

- `scripts/build_exit_order_fill_lineage.py` joins the 30-row R0-R3 parity cohort
  to durable V2 intent/order/fill/protection/event/incident records using a read-only
  SQLite connection. The resulting canonical evidence is
  `docs/audits/2026-08-16-exit-order-fill-lineage.{json,md}`.
- The closed cohort contains 15 `STOP` and 15 `TARGET` exits, with no abnormal exits
  and no quantity-mismatched partial exits. Measured totals are exchange
  trigger-to-fill timing `-5.23940860R`, commission `-6.85480521R`, and an explicitly
  unallocated P1 counterfactual `-1.29198910R` because 21 simulated triggers had no
  historical `ProfitProtectionStopTightened` receipt. Do not call those multiple fills
  “partial fills.”
- Every one of the 30 historical reduce-only Binance fill order IDs exactly matches a
  `ProtectionTriggered.event_payload.exchange_order_id`; the report now emits this as
  `exit.order_link.status=MATCHED` and treats missing/mismatched order identity as an
  explicit episode failure rather than continuing the aggregate audit.
- The historical absence is not current behavior: active BTC/USDT position
  `0e3814c7-d23a-48a8-bef6-d240fc809b66` has a real P1 receipt at
  `2026-08-16T02:41:05Z`, moving the stop from `62744.5` to `62976.0229` under Binance
  order `1000000168673444`; TP `1000000167954361` remains live. Runtime is ACTIVE,
  API `8016` health returns 200, and reconciliation is HEALTHY. Natural reduce-only
  exit evidence is still pending.
## Execution cost optimization loop (2026-08-16)

- Natural exit waiting is not a task blocker. Current BTC/USDT remains observation-only.
- 30 closed Testnet episodes show exact exit lineage, observed commission `4bps/side`, mean commission drag `0.22834078R/trade`, mean trigger-to-fill `-0.17464695R/trade`, and floor-bound share `23/30`.
- The research-only `ATR_NATIVE_ONLY_FILTER` challenger failed BTC/ETH hard gates after 1m fidelity and 1.5x stress; no active manifest or runtime rule changed.
- Maker/limit replay is currently blocked because the historical database has no order-book/depth/quote observations. Existing P2 Policy A-E evidence remains a 9-trade proxy and cannot promote.

## Microstructure pipeline (2026-08-16)

- Added independent `services/microstructure` collector for Binance USDT-M Testnet BTC/USDT and ETH/USDT. It persists top-20 order-book snapshots, best bid/ask, spread, timestamps, last/mark price, latency and clock skew into `microstructure_snapshots`.
- Added advisory health and crash-recovery tables (`microstructure_health`, `microstructure_checkpoints`), 30-day retention, readiness gate, and one-command future replay entry point. The collector runs as a separate process and cannot block Scheduler, entries, exits, protection, or reconciliation.
- Launcher wiring starts/restarts `scripts/run_microstructure_collector.py` with `logs/microstructure.pid`; runtime scheduler remained ACTIVE/HEALTHY while real Testnet rows accumulated (BTC 3, ETH 3 at checkpoint).
- Readiness scope audit (2026-08-16): the former 0% was caused by counting pre-collector historical candidates in the denominator. Readiness now uses only candidate windows whose ±5m window overlaps the first persisted order-book snapshot. Current post-fix sample is 2 BTC / 0 ETH, coverage 100%; remaining gate is natural post-start candidate accumulation to 100 total with BTC/ETH each >=40.
## Directional sizing target corrected (2026-08-16)

- Operator target is `50x leverage + 5% equity margin`: `max_leverage=50`,
  `max_margin_fraction=0.05`, and the resulting `max_position_fraction=2.50`, with
  `risk_per_trade=0.01`.
- Static tiers, volatility tiers, slider scaling, API persistence, V2 operator-profile
  resolution, legacy PaperSignal sizing, bootstrap, and frontend auto-settings all
  use the same target.
- Active run snapshot `ff2ebdc1-ee1d-4ec3-a137-167112cb36a7` is live. Existing ETH/XRP
  positions remain untouched and protected; no exchange `setLeverage` call was made
  for them.

## Final runtime closeout (2026-08-19)

- Runtime endpoints share one reconciliation projection. The current live
  state is `ACTIVE / BINANCE_TESTNET`, Scheduler is running, reconciliation is
  consistent, and there are no exchange or locally open managed positions.
- Strategy research reached `NO_VALIDATED_EDGE`: the manifest is `PENDING`,
  eligible execution symbols are empty, and new-entry authority is
  `NONE / ENTRY_PAUSED / NO_AUTHORIZED_PRODUCTION_STRATEGY`. This is an
  intentional strategy-authorization pause, not an execution outage.
- The normal desktop launcher does not enable a Testnet Canary. BTC/ETH are
  displayed as `执行范围 / 新开仓暂停`; research-only symbols stay `研究 / 观察`.
  Existing managed exposure remains eligible for reconciliation, protection,
  and reduce-only exit if it appears.

## Runtime ownership follow-up (2026-08-18)

- Runtime Truth now consumes the persisted external baseline before comparing
  exchange and V2 positions. Manual baseline BTC is projected as
  `EXTERNAL_MANUAL` and excluded from managed protection P0; missing ownership
  remains `UNKNOWN` and fail-closed.
- Snapshot, positions, reconciliation, and no-trade summary expose the same
  ownership-aware reconciliation projection. A regression fixture covers manual
  BTC plus managed/protected ETH coexistence.
- Opposite-direction same-symbol candidates now use the explicit
  `MANUAL_POSITION_DIRECTION_CONFLICT` reason and are rejected per candidate;
  other symbols remain eligible when their own runtime facts are healthy.

## Runtime projection frame verification (2026-08-18)

- `projection_id` now hashes current exchange positions/open orders, relevant
  scheduler facts, external baseline, and open V2 positions; observation and
  heartbeat timestamps are excluded so cache refreshes do not create false
  cross-endpoint drift. A regression test proves timestamp-only changes are
  stable while fact changes produce a new id.
- Fresh verification: focused backend `68 passed`; frontend Vitest `114 passed`;
  frontend production build passed; full Ruff passed; mypy passed for 258 files;
  full pytest `1702 passed, 16 skipped, 1 failed` with only the pre-existing
  `test_daily_review_keeps_all_terminal_reasons_for_same_symbol` failure.
- Browser verification remains blocked by the installed Chrome plugin missing
  its internal `browser-service.mjs`; HTTP `/trading` and `/health` both return
  200, but this is not rendered-browser evidence.
- Active restart was fail-closed by `EXTERNAL_BASELINE_MISMATCH` (persisted BTC
  short `0.5346`, current exchange snapshot empty). The persistent baseline was
  not overwritten; local services were restored in Shadow mode. Runtime Closeout
  and strategy optimization remain BLOCKED pending operator baseline resolution.
# Runtime Truth + trade lifecycle closeout (2026-08-19)

- Manual baseline now has explicit lifecycle/acknowledgement states and symbol-scoped drift blocking. No rebaseline or live order was performed; current persisted BTC short `0.5346` remains intentionally unchanged while Binance is flat.
- Runtime `/snapshot`, `/positions`, `/reconciliation`, and `/no-trade-summary` share one ownership-aware projection ID. Canonical reconciliation is fail-closed for missing V2 order/fill identity; projection fingerprints include order/fill/protection identities and scheduler publishes baseline lifecycle/drift metadata.
- Read-only lifecycle forensics report: `.local/trade-lifecycle-forensics.json` / `.local/trade-lifecycle-forensics.md`; current 30-row cohort does not contain the requested ETH/SOL exact episodes.
- Gates: Runtime Deployment BLOCKED; Strategy Analysis READY; Strategy Deployment BLOCKED. No stop, sizing, leverage, fee, or promotion threshold changed.
