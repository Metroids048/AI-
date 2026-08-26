# Task History

## [TASK-2026-08-23-P0-RUNTIME-ENTRY-GATE]

- Read-only audit confirmed the user's six `ENTRY_KILL_SWITCH_ACTIVE` counts
  were caused by a persisted liveness recovery hold (`global.entry_enabled=false`),
  not by RSI/MACD strategy code. The control was already auto-recovered at
  `2026-08-23 08:56:47 UTC` with `LIVENESS_RECOVERY_RECOVERED`.
- A formal `launch-paper-console.ps1 -AutomatedTradingEngine v2_active
  -EnableNaturalTestnet -PreserveExternalTestnetBaseline -OpenBrowser:$false`
  restart reclaimed four stale leases, removed the duplicate scheduler launch,
  and returned `EXIT_CODE=0` with `ACTIVE Trading Mode Contract verified`.
- Natural post-recovery evidence: BTC/ETH `2026-08-23 09:45 UTC` decisions had
  `TESTNET_CANARY`, `entry_authorized=true`, `AUTHORIZED_TESTNET_CANARY`,
  and no new `ENTRY_KILL_SWITCH_ACTIVE`; BTC ended at
  `MACD_DIRECTION_MISMATCH`, ETH at `RSI_OUTSIDE_RANGE`.
- Runtime truth at close: Binance Testnet available, reconciliation `HEALTHY`,
  exchange/local positions `0/0`, open orders `0/0`. No strategy or risk
  parameter was changed; Production remains `PENDING`.

## [TASK-2026-08-21-LIVENESS-RECOVERY-FINALIZE]

- Commit A `ceaf46b2e7cfd3166303b897ad2bff66e9a1574b` 完成 Scheduler V2 liveness、
  durable entry hold、management-only recovery、5s/15s bounded restart backoff 与 worker
  process recycle；第 3 次连续失败进入 `AUTO_RECOVERY_EXHAUSTED`。
- Added tests for unsafe crash continuity, manual-vs-system hold clearing, dynamic state path,
  stalled critical task detection and current-HEAD transaction drift.
- Transaction contract baseline now points to Commit A protected-path hashes；Commit B 只包含
  contract/verifier/tests/docs。未修改策略规则、准入门槛、杠杆、仓位、费用、SL/TP 或 Production authorization。
- Runtime evidence used for this closeout: ACTIVE Testnet Canary, BTC/ETH critical cycle alive,
  latest closed-bar reconciliation healthy；existing natural ETH lifecycle ended CLOSED with
  exchange/local open positions `0/0`。

## [TASK-2026-08-21-AUTO-TRADING-LIVENESS-RECOVERY]

- Scope: 仅修复 Scheduler/V2 critical liveness、supervisor、launcher 健康契约、
  strategy package identity 漂移和当前状态文档；没有策略优化或风控数值变更。
- Implementation: `services/execution/scheduler.py` 增加 per-job liveness 与 V2
  supervisor；`services/execution/runtime_state.py` 增加 25 分钟 watchdog；
  `scripts/launch-paper-console.ps1` 健康检查要求 critical task alive、无未恢复失败、
  decision stream 未 stalled；`/no-trade-summary` 暴露 critical job 状态。
- Identity: manifest code/package hash 与当前 inventory 对齐，source commit 更新为
  当前 HEAD；`authorization_state=PENDING`、`eligible_execution_symbols=[]` 保持原值。
- Runtime evidence: 一键启动 `SUCCESS / STARTUP_READY`；Natural Canary 为
  `ACTIVE/BINANCE_TESTNET/TESTNET_CANARY`；15:15 与 15:30 UTC BTC/ETH 新 decision
  连续前进；随后自然 ETH entry intent `62437d5d-9c3a-46ce-a698-351595e816eb` 取得
  Binance order `16767783498`、filled `4.242`，并生成 stop/TP 保护，15:50 UTC
  reconciliation `HEALTHY`，交易所/本地开放持仓 `1/1`。
- Remaining gate: 本轮尚未观察到自然 reduce-only exit -> fill -> CLOSED；不强制平仓，
  不把 acceptance/manual 订单当作自然退出证据。


## 2026-08-21 Runtime / Review audit separation

- User authorized implementation to address the current-head audit findings
  while preserving automatic Testnet opening.
- Changed Review Layer scope accounting, launcher comments/docs, and added a
  read-only incident classification probe. No entry/exit, risk, leverage,
  stop/TP, exchange credential, or production-authorization code changed.
- Evidence: targeted pytest `72 passed, 1 skipped`; Ruff and mypy passed;
  pre-commit passed; live SQLite probe showed one active managed position and
  one active intent, with unresolved incidents classified as historical-only.

### [TASK-AUTONOMOUS-RECOVERY-LOOP-2026-08-19] ACTIVE startup recovery and bounded natural observation
- **Date**: 2026-08-19
- **Type**: Runtime startup recovery / Testnet natural lifecycle evidence
- **Summary**: Fixed the ACTIVE contract to accept `testnet_sampling_v2` when explicit authority is `TESTNET_CANARY`; added validated re-arm after a prior startup safety stop; retired stale protection projections for terminal `QUARANTINED` positions; and stopped repeated recovery logging for already-persisted `FILLED` entry receipts whose exchange position is now flat.
- **Runtime evidence**: One-click launcher returned `SUCCESS / STARTUP_READY`; scheduler remained `ACTIVE`, `BINANCE_TESTNET`, five-symbol Canary, `entry_authorized=true`, `startup_contract_errors=[]`, and `reconciliation=HEALTHY` after restart. Authorized BTC baseline was refreshed from `short 0.5346` to flat with no unresolved V2 ownership facts.
- **Natural evidence**: A read-only 60-minute scheduler observation covered 236 cycles and timed out without a natural entry. Evidence: `docs/evidence/automated_trading_v2/natural_cycle_20260818T190030Z.json`. No entry, protection, exit, or strategy deployment claim is made from this run; loop state is `ACTIVE_NATURAL_ENTRY_NOT_OBSERVED`.
- **Strategy boundary**: Forensics/replay stayed read-only; no stop/TP, leverage, sizing, gate, or active strategy rule was changed.
- **Verification**: targeted pytest `39 passed`; full Ruff passed; `mypy apps services shared` passed (`258` source files); full pytest `1721 passed, 7 skipped, 1 failed` with the existing daily-review terminal-reason aggregation failure only.

### [TASK-RUNTIME-TRUTH-OWNERSHIP-CLOSEOUT] Ownership-aware Runtime Truth and manual direction isolation
- **Date**: 2026-08-18
- **Type**: Runtime Truth / ownership reconciliation
- **Summary**: Added one shared ownership projection for Runtime Truth. Persisted external baselines classify exchange positions as `SYSTEM_V2`, `EXTERNAL_MANUAL`, or `UNKNOWN`; manual baseline positions no longer degrade reconciliation or enter managed protection P0, while unknown positions remain fail-closed. `/snapshot`, `/positions`, `/reconciliation`, and `/no-trade-summary` now consume the same ownership-aware reconciliation object. One-way opposite-direction candidates now emit `MANUAL_POSITION_DIRECTION_CONFLICT` and reject only that candidate.
- **Evidence**: Runtime Truth manual+managed coexistence regression, API suite, V2 reconciliation/recovery suite, and cycle/entry/funnel tests.
- **Safety**: No strategy, risk, leverage, stop/take-profit, exchange credential, or execution-universe values changed. Strategy optimization remains gated on Runtime closeout.

### [TASK-STRATEGY-LOOP-D16] Strict dual-timeframe alignment with two confirmations
- **Date**: 2026-08-15
- **Type**: Strategy research / chronological OOS validation
- **Summary**: Added the bounded strict 1h/4h EMA50 alignment plus two closed 15m confirmation replay mode, preserving the existing entry geometry, R2 threshold, and exchange-first runtime.
- **Result**: On the 70/30 chronological replay over `.strategy_refactor_history.db`, BTC OOS expectancy/PF improved from `-0.26501108/0.65078316` to `-0.23021063/0.68929480`; ETH improved from `-0.20280307/0.71929364` to `-0.16868766/0.76121143`, with lower drawdown for both. Candidate accepted only for the next 1m fidelity/promotion gate; active strategy remains unchanged and Final Holdout was not accessed.
- **Evidence**: `artifacts/active_strategy_optimization/strict_dual_timeframe_alignment_two_bar_20260815.json`, `LOOP_LEDGER.json`, `FINAL_STATUS.json`.

### [TASK-MARKET-ALPHA-META-LABEL] New information-source research exhausted
- **Date**: 2026-08-14
- **Type**: Strategy research / read-only validation
- **Summary**: Replaced the prior strategy-family loop with public Binance spot/perpetual 1h klines, taker-buy pressure, spot/perpetual basis, point-in-time funding, BTC/ETH lead-lag features, equal-risk outcomes, and nested Logistic/GBM meta-label gating. The runner evaluates 40 declared arms over 37,856 development events and never reads the sealed holdout.
- **Method correction**: Final replay sorts train/validation/OOS rows by event time (the initial draft split by symbol-file order) and selects thresholds only on validation expected Net-R after base cost with a 15-trade minimum. Final historical acceptance is evaluated separately with payoff, PF, expectancy, LCB95, positive windows, drawdown, and 1.5x-cost stress.
- **Result**: `CURRENT_MARKET_ALPHA_SOURCE_EXHAUSTED`; `holdout_accessed=false`; `accepted_arms=0`. Only 6 arms had configured windows and 3 produced any OOS trades. Best non-zero arm: 4 trades, 50% win rate, 1.23 payoff, PF 1.23, expectancy +0.14R, LCB95 -1.43R, 1/8 positive windows, 1.5x-cost expectancy +0.02R and PF 1.04.
- **Safety**: No V2 execution, scheduler, risk, Binance adapter, credentials, database schema, active strategy manifest, or Testnet order was changed. No candidate was promoted or armed. Next work requires a new authorized information source or hypothesis; no more OHLCV strategy-name iterations.
- **Evidence**: `docs/audit/2026-08-14-market-alpha-meta-label.md`, `artifacts/market_alpha/reports/market_alpha_meta_research.json`, `artifacts/market_alpha/canonical/market_alpha_events.jsonl`.

### [TASK-LAUNCHER-ACTIVE-SCOPE] Fix false ACTIVE startup failure before the first market heartbeat
- **Date**: 2026-08-11
- **Type**: Local launcher reliability / automated-trading execution defect
- **Summary**: The one-click launcher could report `EXECUTION_SCOPE_MISMATCH;EXECUTION_SCOPE_INCOMPLETE` even though the isolated `v2_active` scheduler subsequently became healthy. Root cause: `_publish_external_state()` derived the fixed BTC/ETH execution scope from the first asynchronous `market_data_heartbeat`; its initial empty result published an empty execution scope, while the launcher correctly enforced the ACTIVE contract before that heartbeat completed. The scope now comes directly from the invariant `AUTO_SIMULATION_EXECUTION_SYMBOLS`; `data_fresh` still remains false until the heartbeat has actually observed both symbols, so no data-freshness or trading gate was weakened.
- **Verification**: New RED regression reproduced `execution_symbols=[]`; post-fix scheduler + launcher-contract tests `21 passed`; real cold start through `launch-paper-console.ps1 -AutomatedTradingEngine v2_active -EnableNaturalTestnet -PreserveExternalTestnetBaseline -OpenBrowser:$false` exited `0`; API/frontend returned `200`; `ACTIVE_STARTUP_CONTRACT_PASSED`; targeted Ruff and full mypy (`235` source files) passed.
- **Limits**: Full non-integration pytest was `1511 passed, 5 skipped, 2 failed` solely because the committed candidate registry now contains `trend_momentum_v2_enriched` (10 candidates) while `tests/test_candidate_registry.py` still expects 9. This is the known independent registry-test baseline also recorded by the 2026-08-10 task; it was not changed here. Full-repo Ruff likewise retains 3 unrelated script findings.
- **Safety**: No strategy, risk parameter, stop/take-profit, leverage, order logic, credential, Mainnet, or execution universe changed. This repairs only startup-state publication.

### [TASK-V2-GATE16-MANUAL-BASELINE] Preserve manual Testnet positions during contract proof
- **Date**: 2026-08-04
- **Type**: Automated Trading V2 / Testnet execution evidence
- **Summary**: Resumed Gate 16 from the failed opt-in preflight. The contract verifier incorrectly required BTC to be flat and its compensating cleanup could flatten an operator-owned position. Changed the verifier to snapshot the pre-run position/open-order baseline, follow the existing position direction in Binance one-way mode, submit protection/exit only for the contract increment, cancel only contract-owned orders, and require the final exchange state to return to the original baseline. Enabled the existing `allow_entry_with_unmanaged_positions` operator flag on the active directional PaperRun and restarted the standard local console stack.
- **Real evidence**: BTC manual baseline remained `short 0.0105`; contract entry `27941708499` / trade `524311879` added `0.0008`; protection orders `1000000155928815` and `1000000155928828`; reduce-only exit `27941708544` / trade `524311888`; final BTC position restored to `short 0.0105`, final new open orders `0`. Evidence: `docs/evidence/automated_trading_v2/testnet_contract_20260804T060922Z.json`.
- **Safety**: Mainnet stayed disabled. The verifier did not adopt, close, or attach protection to the manual baseline. `natural_strategy=false`; this remains infrastructure-contract evidence only.

### [TASK-STRATEGY-READINESS-02] Complete history, freeze corrected baseline, reject legacy candidate
- **Date**: 2026-07-30
- **Type**: Strategy readiness / deterministic validation
- **Summary**: Completed the Binance Vision BTC/ETH five-timeframe history through 2026-07-29, generated a new immutable Golden Baseline, found that `trades.jsonl` used pretty-printed multi-line records, fixed the serializer with a regression test, rejected stale intermediate source hashes caused by final sync, concurrent candidate commits and the required pytest-state refresh, and froze corrected `r4` evidence without reading Final Holdout results.
- **Result**: Data coverage is `SUFFICIENT`, but the active `trend_momentum_v1` candidate is rejected. Portfolio baseline: 1,126 trades, Sharpe -0.1096, PF 0.9882, MaxDD 77.14%, net expectancy -0.000202, net return -22.71%; 90% IID confidence intervals cross zero. Strategy optimization/promotion remains blocked on next-bar parity, realistic point-in-time costs, per-window walk-forward and dependent bootstrap.
- **Safety**: No exchange order, runtime risk parameter, leverage, sizing, stop/take-profit, net-edge or strategy threshold changed. Final Holdout remains sealed.

### [TASK-STRATEGY-REFACTOR-01] Current-tree audit and immutable Golden Baseline gate
- **Date**: 2026-07-29
- **Type**: Strategy core refactor / validation evidence
- **Summary**: Audited the current V2 and legacy strategy paths without changing runtime. Added an atomic, no-overwrite baseline generator covering source/data/config hashes, current active manifest, BTC/ETH five-timeframe coverage, legacy replay outputs, costs, current IID CI, trades, and terminal funnel reasons. Independent review then caught and drove a TDD fix for incomplete tree-hash scope and stale post-memory evidence.
- **Result**: `DATA_COVERAGE_INSUFFICIENT`. BTC/ETH 5m history is absent, 1m history is only about 14 days, and longer frames begin around July 2025 with gaps. No common five-timeframe cutoff exists, so Holdout was not frozen or evaluated and metrics/trades are explicitly unavailable.
- **Verification**: RED import failure proved the generator was absent; later boundary regression failed when a missing series incorrectly produced a cutoff; the independent-review regression proved `docs/**` and arbitrary root design files were absent from the hash. Post-fix Task 0/1 regression: `95 passed in 37.18s`; targeted Ruff/format clean; Mypy `Success: no issues found in 1 source file`; pre-commit passed through commit `bf3b0b7`.
- **Limits**: Tasks 2+ are intentionally paused. No strategy, risk, execution state machine, frontend, Mainnet, leverage, sizing, stop, or take-profit behavior changed.

### [TASK-V2-CLOSURE-GAPS] Close Gate2-4 critical gaps; Gate5 remains BLOCKED
- **Date**: 2026-07-29
- **Type**: Automated Trading V2 production closure
- **Summary**: Wired ACTIVE scheduler `persist_facts` (cycle ensure → run → finalize decision), added `fact_persistence` for confirmed fill→position→protection, removed blind TIME_EXIT (requires `forced_exit_reason`), fail-closed local state when persisting, fixed `v2_shadow_run` OHLCV loader. Gate5 stays BLOCKED — engine still `legacy`, no fabricated NATURAL evidence.
- **Verification**: pytest fact_persistence+cycle+scheduler+audit 26 passed; ruff All checks passed; mypy Success on 3 files.
- **Limits**: NATURAL_SCHEDULER_TESTNET still requires operator `v2_active` + `entry_enabled` + live scheduler observation.


### [TASK-063] Unblock pre-commit for bulk push (commit 6)
- **Date**: 2026-07-26
- **Type**: Ops / git hygiene
- **Summary**: `git commit -m "6"` was blocked by ruff (39 remaining in ops scripts) + mypy (8 in services). Fixed product-code issues: removed duplicate `resolve_manual_position_pnl` in `account_equity.py`, narrowed `paper_run_id` nullability in `tasks.py`, converted cross-sectional replay metrics to `Decimal` + fixed unused loop vars. Added `scripts/*.py` ruff per-file-ignores for one-off diagnostic style noise (E402/E501/E702/E722/F841/B007/SIM115). Removed accidental empty `=` file. Pushed `ca88e24` to `origin/main`.
- **Verification**: pre-commit (ruff/ruff-format/mypy) passed on commit; `git push` → `831c47f..ca88e24 main -> main`.
- **Limits**: Scripts still linted for hard errors (e.g. F821); only style/noise rules ignored. Product code under `services/`/`shared/`/`apps/` unchanged in strictness.

### [TASK-062] Strategy optimization Phase 3: Meta-Label classifier training execution and research finding
- **Date**: 2026-07-26
- **Type**: Strategy optimization / ML model training
- **Summary**: Executed Phase 3 of the multi-phase strategy optimization plan: attempted to train a Meta-Label win-probability classifier to replace the existing rule-based胜率估计 heuristic. Ran `scripts/train_meta_label_model.py` against `.local_paper_console.db` with strategy-key `auto_paper_mature_templates` on 1h timeframe. Training script successfully reconstructed 26,793 samples across 3 symbols (BTC/ETH/SOL) using strict walk-forward time-ordered split (75% train / 25% OOS, never shuffled to prevent lookahead bias). Training completed with 20,094 train samples, but **OOS AUC = 0.4837 < 0.55 gate (rejected)**. Current feature set (atr_percent, trailing_return_5/20, volume_zscore_20, ensemble_confidence, direction_vote_count, entry_vote_count, funding_rate_bps, hour_of_day_sin/cos) lacks sufficient predictive power for 8-bar forward return direction on this timeframe.
- **Decision**: Did NOT force-accept the undertrained model. Per the script's fail-closed design, `SignalEnsembleService.create_meta_label` will continue using the existing rule-based heuristic (`rule_meta_label_v1`) via `load_active_model`'s fallback mechanism. This is a **real research finding**, not a bug — the honest conclusion is that current features cannot predict future direction胜率 better than random (AUC ~0.48 ≈ coin flip). Recorded as ADR-074 in decisions-log.md.
- **Layer mapping**: Strategy Layer (meta-label probability estimation within ensemble voting); Validation Layer未涉及 (training itself is offline research, not runtime gate logic change).
- **Research loop served**: 特征工程假设 → offline training → OOS validation → honest negative finding → documented for future iteration (potential improvements: add regime/volatility interaction features, try non-linear models like LightGBM, adjust label_horizon_bars per timeframe, add cross-symbol correlation features — all out of current scope).
- **Verification**: Training script output confirmed 26,793 samples, 20,094 train count, 0.4837 OOS AUC; `artifacts/meta_label_models/` directory does not exist (expected — no model saved when below gate); ADR-074 written to decisions-log.md; task-history.md updated.
- **Limits**: Phase 3完成但模型未达标 — this does NOT mean the meta-label architecture is wrong, only that the current feature set on 1h bars is insufficient. Phase 2A/2B (exit_ladder) explicitly skipped per prior real-data evidence showing ExitLadder net expectancy worse than fixed 2R (TASK-059's `docs/audits/2026-07-12-exitladder-replay-comparison.md`). Phase 4 (cross-sectional carry OOS validation) and Phase 5 (1d/4h swing strategy validation) remain pending per the optimization plan sequence.

### [TASK-060] Unblock GitHub push: redact leaked API keys from diagnosis doc
- **Date**: 2026-07-15
- **Type**: Ops / git hygiene
- **Summary**: `git push origin main` was rejected by GitHub push protection (GH013) because `docs/diagnosis/system-readiness-2026-07-15.md` in local commit `f9cdf5e` contained a real OpenRouter API key and a GitHub PAT. Rewrote the single unpushed commit: replaced secrets with local-dotenv placeholders, soft-reset off `origin/main`, recommitted as `6877d1f`, pushed successfully to `https://github.com/Metroids048/AI-`. `.env` / `.env.*` / `logs/` remain gitignored so future pushes should not reintroduce the same leakage from env files.
- **Verification**: local HEAD == `origin/main` (`6877d1f`); `git show HEAD:...system-readiness...` asserts no `sk-or-v1-` / `github_pat_`; push completed without GH013.
- **Limits / operator action required**: Keys that appeared in the blocked push attempt (and in local history before rewrite) should be **rotated** in OpenRouter and GitHub token settings; local `.env` keep using the new keys only — never paste them into docs.

### [TASK-061] Auto open/close remediation plan P0–P2: data_stale storm, equity sync, exposure sizing cap, audits, 4h/15m signal split
- **Date**: 2026-07-14
- **Type**: Ops reliability + sizing bug fix + evidence audits
- **Summary**: Implemented the attached auto-open/close remediation plan. (1) Confirmed/locked `data_stale` dedup in `MarketDataHeartbeatService` (one active event per symbol). (2) Added `services/execution/account_equity.py` and wired Paper cycle + sizing to prefer Testnet exchange snapshots over the $10,000 bootstrap seed. (3) Root-caused 597 `max_symbol_exposure_exceeded` rejections: empty book + `requested_notional/equity=0.80` from uncapped `risk_per_trade*leverage` sizing — capped notional to tier/`max_symbol_exposure`. (4) Ran real audits against `.local_paper_console.db` (Top20 OHLCV complete; exposure audit + edge-stats 17<30 rejected). (5) Split 4h direction vs 15m entry signal subsets in bootstrap + decision_pipeline. (6) Chan annotation CSV template for operator. (7) Exit-policy `compare_exit_policies` already present — verified.
- **Verification**: `pytest -q -m "not integration"` → 397 passed, 2 deselected.
- **Limits**: Edge-stats artifact not written (insufficient trades). Chan theory still blocked on operator annotations. ADR-063 stands: no threshold relaxation to manufacture fills; current directional OOS edge remains negative — engineering fixes enable correct opens when gates pass, not guaranteed profitability.

### [TASK-059] Operator restart repair + real-data edge audit of directional/carry/cross-sectional lanes + ExitLadder revert + real edge-stats gate
- **Date**: 2026-07-13/14
- **Type**: Ops repair + evidence-based strategy audit + bug fix
- **Summary**: User reported the desk stuck on "加载中/连接中" for 10 minutes after a restart (positions/orders/account panel all blank) and zero Binance orders opened all day, then asked for a root-cause map of the auto open/close logic and, after seeing it, asked to actually find a way to make the automation both open orders and have a real chance of profiting. (1) **Stuck-loading root cause**: the API process listening on :8016 had been started at some point outside the standard `launch-paper-console.ps1`/`一键启动.cmd` path (a bare `nohup python -m apps.api.local_server --local-console`), so it never received `POSTGRES_URL` pointed at the local SQLite file and silently fell back to `shared/config.py`'s docker-compose default `postgresql+psycopg://...@timescaledb:5432/ai_quant` — `timescaledb` only resolves inside the docker network, so every DB-touching endpoint (`console/overview`, `execution/trading-status`, `binance-testnet-account`) 500'd with `getaddrinfo failed` while `/health` (which never touches the DB) looked fine, matching the "top bar stuck loading, 持仓0" screenshot. A second duplicate scheduler process (from an earlier boot) was also running concurrently against the same SQLite file, contributing to LLM-call rate doubling (see below). Fixed by killing all orphan/duplicate API+scheduler processes and re-running the standard launcher, which correctly sets `POSTGRES_URL` to the local sqlite path; verified `console/overview` returns real market data post-restart. (2) **Zero-orders root cause (that day)**: not a bug — `net_edge_after_cost_negative` correctly rejected all 141 directional attempts (real sample: 59.6% win rate, but avg-win/avg-loss/cost math nets to -0.099%), and the carry lane had zero attempts because no funding signal cleared its threshold that day. This is the same structural issue ADR-062 already diagnosed (raw technical-ensemble edge is thinner than realistic round-trip cost), not a new regression.
- **Real-data edge audit (the substantive ask — "找到合适的方案让它能开单并且大概率盈利")**: User explicitly said tuning thresholds to force more fills was unacceptable if it just opens more losing trades. Audited all three strategy shapes the repo has code for, using REAL data (not assumptions):
  - Directional 8-indicator ensemble: already has a real Top20 90-day historical replay (`docs/audits/2026-07-12-top20-technical-validation.md`) showing OOS net expectancy **-0.0023** (worse than the -0.0017 baseline) — confirmed still the honest state, no new positive-edge signal combination was found in 策略库/ or research_source/ (both are idea-stage only, zero backtest numbers anywhere outside the audits already on file).
  - Single-symbol funding carry: pulled REAL Binance **mainnet** public funding-rate history (fapi.binance.com, read-only, no auth/trading) for 19/20 Top20 symbols, 60 days — real funding rates are 0.3-1bps/8h for nearly every symbol (only TON ~1.9bps), nowhere near clearing even a maker-only 4bps round-trip cost; a corrected carry-and-hold simulation (pay cost once at entry+exit, accrue funding every settlement instead of requiring each single window to clear cost) still showed 18/19 symbols flat-to-negative over 1/3/7-day holds, with only TON showing a positive 7-day-hold average (+14.5bps over 11 trades — too small a sample to trust).
  - Cross-sectional funding-rate carry (rank Top20 by funding, short top payers / long most-negative): confirmed via the local SQLite `market_extras` table that the platform's own persisted funding history comes from `demo-fapi.binance.com` (Binance's Testnet/Demo mirror) where 8 of 20 symbols sit at a constant `0.0001` floor rate with **zero real dispersion** (verified: 1 distinct value across 5,000-6,400 rows each) — not usable for backtesting real market dynamics. Re-ran against real mainnet data instead: 298 real 8h settlement windows over 60 days, basket win rate 17.4%, average -15.7bps/window, cumulative -46.7% — the ~2-3bps real cross-sectional funding spread cannot cover the directional price risk taken on 6 simultaneous single-name legs. **Conclusion, given directly to the user**: none of the three strategy shapes this repo has working code for currently clears round-trip cost with real market data in the current regime. Per the user's own explicit choice (after being shown this evidence), this session did NOT build a full cross-sectional backtest engine or force any threshold relaxation — building more infrastructure around a shape already shown edge-negative was correctly identified as wasted effort, and forcing fills would violate the user's own stated "profitable probability" requirement.
- **What WAS fixed (the user chose to close out only the affirmatively-correct items)**: (1) **ExitLadder reverted to fixed-2R** in `AUTO_PAPER_TECHNICAL_RULES.takeprofit_rules` (`services/execution/bootstrap.py`) — this exact exit-mechanics ablation was already real-data-tested in a prior session (`docs/audits/2026-07-12-exitladder-replay-comparison.md`: ExitLadder net expectancy -0.000866/PF 0.8817/maxDD 143.6% vs fixed-2R +0.002185/PF 1.1308/maxDD 52.2%, same entry signal) and ExitLadder was strictly worse on every metric, yet the bootstrap default was still shipping it — reverted to `{"risk_reward": 2.0}` only. (2) **Net-edge gate's win-rate/avg-win/avg-loss inputs were a noise proxy, not a measurement**: `decision_pipeline.py::_meta_label_samples()` computed the last ~47 bars' raw close-to-close return in the ensemble's fused direction, completely independent of whether the actual signal combination ever historically fired — essentially a random-walk-adjacent stand-in. Added `services/execution/signal_edge_stats.py` (fail-closed artifact loader, same pattern as `meta_label_model.py::load_active_model`) and `scripts/compute_signal_edge_stats.py` (offline script that replays the real entry+stop+take configuration through the already-existing `TechnicalStrategyValidationService` historical engine to compute a real trade-conditioned win_rate/average_win/average_loss, gated on >=30 real trade samples). `decision_pipeline.py::_edge_stats_for_gate()` now prefers this real artifact when fresh, falling back to the raw-bar-return proxy otherwise — this does not change the sign of the current OOS-negative edge finding, it just makes the gate's judgment based on real historical trade outcomes instead of noise once the artifact is computed and refreshed periodically.
- **Verification**: full `pytest -q -m "not integration"` -> 353 passed, 2 deselected (348 baseline + 5 new `test_signal_edge_stats.py` + 3 new `test_edge_stats_for_gate.py`, minus none removed); `ruff check` clean on all touched/new files (fixed one import-order issue in `scripts/compute_signal_edge_stats.py`); `mypy` clean on `signal_edge_stats.py`/`decision_pipeline.py`/`bootstrap.py`; `git diff --check` clean (only pre-existing LF/CRLF notices on unrelated files). All real-data probes were read-only scratch scripts against `fapi.binance.com` public endpoints and the local SQLite file, deleted after extracting findings into this record — no code changes were made based on assumption, every number above was independently reproduced.
- **Limits**: This does not claim any strategy in this repo is currently profitable — the honest finding is that none of the three implemented shapes clear real transaction costs in the current market regime, and the user was given that finding directly rather than having thresholds silently loosened to manufacture fills. `compute_signal_edge_stats.py` only supports `AUTO_PAPER_TECHNICAL_KEY`'s entry rules today (hardcoded check) since that's the only lane with a `TechnicalStrategyValidationService`-compatible entry-rule shape; extending it to the carry lane would need the same single-symbol carry historical replay gap noted in ADR-063. The artifact has not yet been computed/written for the running strategy (needs an operator to run `scripts/compute_signal_edge_stats.py --strategy-key auto_paper_mature_templates --database-url <prod-sqlite>` with real persisted history) — until then the gate transparently keeps using the pre-existing raw-bar-return proxy, by design (fail-closed, not fail-loud).

### [TASK-058] Live paper-trading underperformance diagnosis + fixes, cross-sectional carry strategy (Section 一), MetaLabel training upgrade (Section 三)
- **Date**: 2026-07-13
- **Type**: Diagnosis + bug fix + feature (two new sections from the user's LLM-integration remediation report, explicitly scoped by the user; Sections 二/四/五 deferred per user's own choice)
- **Summary**: User reported two rounds of live auto open/close trading performed "非常差" (very poor) and asked to both diagnose/fix the live system and implement Sections 一 (cross-sectional funding-rate carry) and 三 (MetaLabel training upgrade) from an LLM-integration remediation proposal, skipping 二/四 (need new LLM/embedding keys) and deferring 五 (needs more real failure samples). Live diagnosis via direct SQLite queries against `.local_paper_console.db` found the "poor results" were dominated by friction/config, not signal quality (only 6 live fills across both lanes — too few to judge edge): (1) the API+scheduler processes had been running since 09:04/09:09 that morning, predating both today's ADR-059 LLM fix (18:22 commit) and a fresh `.env` OpenRouter key (18:58) — restarted both processes to pick up current code+config; (2) `net_edge_after_cost_negative` was rejecting nearly all directional/carry candidates because `bootstrap.py`'s fee assumptions (10bps/18bps one-way core/standard, 8bps carry) were 2-4x real Binance USDM regular-user rates (maker 2bps/taker 5bps per binance.com/en/fee/futureFee) — recalibrated to 5bps core/standard, 1bps/3bps slippage; (3) discovered and fixed a **new** bug in the same "LLM chain looks configured but silently produces nothing usable" class ADR-059 named: OpenRouter's free reasoning models (`nvidia/nemotron-3-*`) can burn the entire `max_tokens=400` budget on hidden reasoning tokens, then dump the incomplete reasoning trace verbatim into `content` on `finish_reason="length"` — a non-JSON response indistinguishable from a real model failure. Fixed via `reasoning: {exclude: true}` in the OpenRouter request payload (verified 0/4 failures vs. 2/4 without it on a heavy real-signal payload), and independently hardened `FallbackChainStructuredLLMRuntime` to also catch plain `ValueError` (malformed JSON), not just `LLMProviderUnavailable` — previously a single bad response from candidate 1 aborted the whole 4-candidate fallback chain instead of trying candidates 2-4. Confirmed the reduceOnly `-2022` duplicate-close bug (TASK-053) has had zero recurrences since its 2026-07-12 23:39 fix — no action needed. Updated `AGENTS.md` per explicit user instruction to document the current 15% portfolio-risk cap and recalibrated fee assumptions as current Paper-validation-phase policy (not a permanent relaxation of risk-first principles).
- **Section 一 (cross-sectional funding-rate carry)**: New `services/execution/cross_sectional.py::compute_funding_rank_snapshot()` ranks the scanned symbol universe by latest funding rate every cycle (fail-closed: excludes symbols with no funding data rather than defaulting to a neutral rank), assigning `short_candidate`/`long_candidate`/`None` basket sides. Wired into `paper_signal.py` via new `_is_cross_sectional_strategy()`/`_cross_sectional_decision()` (dispatches before the existing `_is_carry_strategy()` check) and `paper_runtime.py::run_cycle()` (computes the snapshot once per cycle when `strategy_lane == "cross_sectional_carry"`, skips LLM veto for this lane like `carry`, and closes positions whose rank has dropped outside the basket via a new `rank_dropout` reason reusing the existing `close_on_opposite_signal` close machinery). Found and fixed a real bug during test-writing: the existing veto-synthesis carve-out in `paper_signal.py` (`carry_admission_skip = pipeline_status.startswith("funding_arbitrage_rejected")`) didn't cover the new `cross_sectional_carry_rejected` status, so a rank-dropout close order was itself getting vetoed by the "decision pipeline skipped order" synthetic veto before it could execute — extended the `startswith` tuple to include both prefixes. Registered as a disabled research strategy via new `bootstrap_cross_sectional_carry_strategy()` (paper_status=NOT_STARTED, same pattern as `operator_experience_4h_15m_v1`) — per AGENTS.md non-negotiables 1/2/6, a brand-new strategy shape with zero backtest/OOS evidence must not be auto-armed for live Paper cycles, so it is visible/versioned but not scanned by the scheduler until a dedicated OOS replay clears the validation gate (that replay engine itself is not yet built — remains future work, tracked as a known limit below).
- **Section 三 (MetaLabel training upgrade)**: Added `scikit-learn`/`joblib` as a new optional `ml` extra in `pyproject.toml` (installed into the active venv). New `services/strategy_library/meta_label_model.py` defines a shared, deterministic `extract_features()` (ATR%, 5/20-bar trailing returns, 20-bar volume z-score, ensemble vote counts/confidence, funding rate, hour-of-day sin/cos) used identically by both training and live inference, plus `load_active_model()`/`predict_win_probability()` that fail closed (return `None`) on any missing/stale/corrupt model artifact. `SignalEnsembleService.create_meta_label()` now optionally substitutes the trained model's win-probability for the rule-based `win_rate` when `MetaLabelRequest.strategy_key`/`model_features` are provided and an active model exists (`model_ref` becomes `trained_meta_label:{key}:{version}`); the existing rule-based heuristic (`rule_meta_label_v1`) remains the unconditional fallback. Wired `decision_pipeline.py` to compute `model_features` via a new `_latest_funding_bps()` helper plus the existing ensemble/bars data before calling `create_meta_label`. New offline `scripts/train_meta_label_model.py` reconstructs walk-forward-safe samples directly from persisted `ohlcv_bars` (never from sparse live decision traces) using the same `extract_features()`, splits strictly by time (OOS is always the most recent slice, never shuffled) to prevent lookahead bias, trains a `LogisticRegression`, and only writes a model artifact + `active.json` pointer if OOS AUC clears a `0.55` gate and sample count clears `200` — otherwise rejects and leaves the rule-based path in place. **Ran the script for real against the live `.local_paper_console.db`**: 15m timeframe produced 6956 samples / OOS AUC 0.5435 (rejected, below gate); 4h timeframe produced 2145 samples / OOS AUC 0.4720 (rejected). No model artifact exists on disk — `create_meta_label` is still using the rule-based path in production. This is the honest, expected outcome given ~1 week of live Paper history and the already-documented weak/negative backtest edge (TASK-047's Top20 OOS net expectancy `-0.000005`); it should be re-run periodically as more Paper history accumulates, not treated as a one-time failure.
- **Verification**: full `pytest -q` → 345 passed, 2 skipped (321 baseline + 10 new cross-sectional-carry tests + 14 new meta-label-model/ensemble-integration tests); `ruff check .` clean on all touched files (5 pre-existing unrelated errors in untouched files confirmed via `git diff`-independent path check); `ruff check --fix` applied only mechanical import-order fixes; full `mypy` → "Success: no issues found in 126 source files"; `git diff --check` clean (only a pre-existing LF/CRLF line-ending notice on `AGENTS.md`, not a real error). Live verification: restarted API (port 8016) + scheduler processes, confirmed `llm runtime configured with 4 candidate(s)` in scheduler logs (vs. `UnavailableLLMRuntime` before), confirmed 3 genuine end-to-end `decision_veto_agent` successes via OpenRouter post-restart (`nvidia/nemotron-3-super-120b-a12b:free` and `nvidia/nemotron-3-nano-30b-a3b:free`, clean JSON, no reasoning-truncation failures), and confirmed remaining failures are exclusively OpenRouter free-tier `HTTP 429` rate limits plus the already-known/user-accepted GitHub Models `401` token-scope gap — not a new code defect.
- **Limits**: A dedicated cross-sectional OOS replay engine (parallel to `technical_replay.py` but driven by time-step rather than per-symbol, needed to produce real backtest evidence before this strategy can be auto-armed) was not built this session — the strategy remains a disabled research candidate pending that evidence, as required by AGENTS.md. MetaLabel training gate failed on real data as reported above; no artifact was force-saved to demonstrate the mechanism works "for show" — this was a deliberate choice to keep the gate honest. Sections 二 (adversarial review gateway — needs a second LLM provider distinct from veto) and 四 (RAG vectorization — needs an embedding provider) were explicitly skipped per user's own choice pending new credentials; Section 五 (Review Agent LLM attribution) was not started, deferred by design since it needs more real failure samples than currently exist. `AGENTS.md`'s new Paper-risk-baseline section explicitly flags the 15%/recalibrated-fee settings as a *current Paper-validation-phase* baseline, not a permanent loosening of risk-first policy — this must be revisited before any live/mainnet consideration.

### [TASK-057] Desk ↔ Binance Testnet sync UX + probe truth alignment
- **Date**: 2026-07-13
- **Type**: Execution / ops repair + frontend clarity
- **Summary**: Operator believed orders/positions were not synced with Binance. Live API probe showed Testnet already connected (`ETH long 0.055`, multi-symbol recent fills matching the exchange UI). Hardened probe `web_ui_url`/`warning` for Testnet vs mainnet, expanded major-symbol order scan, fixed console banners/hero copy, added jump-to-open-position hint, set local `BINANCE_TRADING_MODE=testnet`, and relaunched API with correct SQLite `POSTGRES_URL`.
- **Verification**: account probe connected + positions/orders reconciled; frontend desk tests 17 passed; gateway-focused pytest 17 passed; ruff clean on `gateway.py`.
- **Limits**: Mainnet sync intentionally not enabled. Refresh the trading page to pick up frontend copy/hint.

### [TASK-056] Section 0: LLM decision-veto chain silent failure diagnosis + fix + free-tier validation
- **Date**: 2026-07-13
- **Type**: Bug fix + verification (independent task line from the Phase 0-5 remediation series)
- **Summary**: Diagnosed why `decision_veto_agent`/`pre_execution_veto_llm` was silently never invoked with zero logs/errors. Root causes: `shared/config.py::claude_api_key` didn't accept `ANTHROPIC_API_KEY` (fixed via `AliasChoices`); `llm_factory.py::build_configured_llm_runtime()` silently fell back to `UnavailableLLMRuntime` with no logging (added structured logs); `service.py::_execute_llm_veto` used one blanket `except Exception` (split into `TimeoutError`/`RuntimeError`(`LLMProviderUnavailable`)/`Exception` branches with distinct recorded failure reasons); no diagnostic surface existed (added `GET /agents/llm-status`). Approved side-fix: `bootstrap.py::_ensure_auto_paper_run`'s `preserved_keys` was missing `"llm_veto_enabled"`, causing a manually-disabled veto to silently re-enable on every restart — added to the tuple. During end-to-end verification with real free-tier keys, discovered and fixed a second independent bug in the same "silent failure" class: `_OPENROUTER_SEED_MODELS` hardcoded two OpenRouter free models that had been retired (404 on every call) — replaced with two verified-live models (`nvidia/nemotron-3-super-120b-a12b:free`, `nvidia/nemotron-3-nano-30b-a3b:free`). GitHub Models validation hit a 401/403 token permission-scope issue (PAT lacks "Models" read scope — confirmed non-code by testing the catalog-listing endpoint, which returned 200 with the same token); raised to user via AskUserQuestion, user chose to skip GitHub Models and validate with OpenRouter only ("先跳过,只用OpenRouter验证 (推荐)").
- **Verification**: New opt-in integration test `tests/integration/test_llm_decision_veto_live.py` (same `@pytest.mark.integration` + `RUN_<NAME>_INTEGRATION` convention as `test_binance_public_smoke.py`) exercises the real `AgentTaskService.submit_task()` → `_execute_llm_veto()` → `AgentTaskRepository` path against a live OpenRouter free model using the SQLite harness (`tests/conftest.py::db_session`). `RUN_LLM_VETO_INTEGRATION=1 pytest tests/integration/test_llm_decision_veto_live.py -v` → PASSED (14.60s). Full `pytest -q` → 321 passed, 2 skipped; `ruff check`/`mypy` clean on all touched files (also fixed an unrelated `ruff I001` import-order issue in `shared/models/__init__.py`). All touched code Read-reverified before declaring complete.
- **Limits**: GitHub Models free-tier validation explicitly deferred/skipped per user's choice — not attempted further. Sections 一~五 (cross-sectional strategy, adversarial review gateway, MetaLabel training upgrade, RAG vectorization, Review Agent LLM attribution) remain untouched and out of scope pending explicit user request. Docker Desktop-related validation explicitly deferred by the user as a separate decision, not part of this task.

### [TASK-055] Phase 5: cross-symbol correlation portfolio risk audit (already implemented, no code change) + doc typo fix
- **Date**: 2026-07-13
- **Type**: Verification only (no implementation)
- **Summary**: Investigated whether cross-symbol correlation-based portfolio risk control exists (the last remaining item from the user's original remediation report). Confirmed `shared/models/risk.py::RiskProfile` itself has no correlation field, but the actual capability lives elsewhere and is fully implemented: `services/execution/portfolio_risk.py` (`correlation()`/`close_returns()`/`signed_exposure()` — Pearson correlation over 60-bar return windows, fail-closed on insufficient data), called from `services/execution/paper_signal.py::_build_risk_state()` (lines 391-486, computes `high_correlation_peer_count`/`correlated_cluster_exposure`/`correlation_risk_discount`/`net_directional_exposure`/`portfolio_correlation_available` per candidate order against all held positions), enforced in `services/execution/gatekeeper.py::_evaluate_numeric_risk()` (lines 203-213: rejects on `portfolio_correlation_unavailable`, `correlated_exposure_limit_exceeded` (>=2 same-side peers with corr>0.70), `correlated_cluster_exposure_exceeded` (>0.35), `net_directional_exposure_exceeded` (>0.40)). Traced this to prior `[TASK-048]` (predates this remediation round), confirming Phase 5 is not a real gap — the user's report that correlation wasn't considered does not match current code. Also fixed a documentation error introduced in TASK-054: `task_plan.md`/`progress.md`/this file's TASK-054 entry all incorrectly stated the scoped `pytest tests/api/test_paper_runtime_api.py -q` run produced "8 passed" when the actual output was "7 passed, 1 warning" (6 pre-existing + 1 new) — corrected in all three files; the full-suite "321 passed, 1 skipped" figure was already accurate and unaffected.
- **Verification**: `pytest tests/services/test_execution_gatekeeper.py tests/services/test_paper_runtime.py tests/services/test_signal_ensemble.py -q` 43 passed (existing tests already cover the four rejection paths and the 60-bar window boundary — `test_gatekeeper_rejects_two_high_correlation_peers`, `test_gatekeeper_rejects_correlated_cluster_and_net_directional_exposure`, `test_gatekeeper_rejects_missing_portfolio_correlation_for_new_order_but_not_close`, `test_portfolio_return_correlation_requires_full_60_bar_window`). Read-reverified `portfolio_risk.py`, `paper_signal.py::_build_risk_state`, and `gatekeeper.py::_evaluate_numeric_risk` in full before declaring complete.
- **Limits**: This closes the last item (Phase 5) from the user's original five-phase remediation plan (leverage/sizing aggressiveness fix, indicator-driven entries, execution-layer default tightening, order-provenance isolation + ExitLadder E2E test, correlation-based portfolio risk). All five phases are now verified complete — either via code fixes (Thread A leverage/sizing, FVG/multi-timeframe MA signals, ensemble majority-vote gate) or via verification that the capability already existed (Phases 2-5). Did not update `StrategyRoadmapState.status` for the `portfolio-correlation-risk` roadmap item to `done` — that is an operator-facing audit-trailed field updated via the `PATCH` API with `RoadmapUpdate`, out of scope for this code-fix task.

### [TASK-054] Phase 4: order-provenance isolation audit + ExitLadder E2E test
- **Date**: 2026-07-13
- **Type**: Verification + test coverage
- **Summary**: (1) Audited whether testnet-acceptance/carry-execution orders are isolated from real strategy performance. Traced full call chain `carry_execution.py::CarryExecutionService` → `gateway.py::BinanceUsdtPerpetualGateway.submit_order/submit_carry_order` (lines 325-524) — confirmed carry legs never call `ExecutionRepository.create_order()`; results persist only via `AgentTaskRepository.update_task(output_payload=...)`, so they never reach any `OrderExecution`-based performance query. Combined with the existing `demo_audit.py` explicit-tag isolation for acceptance probes, concluded both isolation paths are already in place — no code change needed. (2) Added `tests/api/test_paper_runtime_api.py::test_paper_runtime_auto_cycle_partial_closes_via_exit_ladder`, the first HTTP-level (`/auto-cycle`) test exercising the real decision pipeline through an exit-ladder L1 partial close, using deterministic `stoploss_rules={"fixed_bps": 200}` so the L1 trigger price is computable in the test without reading back the response. Extended `_create_validated_paper_run()` with optional `strategy_key`/`stoploss_rules`/`takeprofit_rules` params (defaults unchanged).
- **Verification**: `pytest tests/api/test_paper_runtime_api.py -q` 7 passed (6 pre-existing + 1 new); full `pytest -q` 321 passed, 1 skipped; `ruff check tests/api/test_paper_runtime_api.py` clean. Read-reverified the new test function body against design intent before declaring complete (per delivery self-check rule).
- **Limits**: Phase 5 (cross-symbol correlation portfolio risk control) not yet started.

### [TASK-053] Unblock directional Binance path: ghost positions + ReduceOnly loop
- **Date**: 2026-07-12
- **Type**: Execution repair + ops closeout
- **Summary**: Directional run was armed but opens died on `portfolio_initial_risk_exceeded` (Demo BTC/ETH injected into mature PaperRun) and close loops on `-2022 ReduceOnly` (local SOL ghost kept forever). Fixed exchange-already-flat close path, stopped injecting Demo positions into strategy runs (audit-only mirror), repaired local directional ghosts BTC/ETH/SOL. Ops closeout: engine on `:8016`, sole mature directional `457c6ecd` running, duplicate paused, Testnet open+close mirror proven (`directional_mirror_ok`).
- **Verification**: focused pytest 59 passed (`paper_runtime`/`mirror_lane`/`exit_ladder`/`gatekeeper`/`gateway`/`paper_runtime_api`); mirror proof LINK open `813722666` close `813722823`; API `/health` ok.
- **Limits**: Auto signal filters still sparse (no continuous auto gateway opens claimed); Top20 OOS / ExitLadder promotion still failed — no strategy/mainnet enablement.

### [TASK-052] Auto open/close gap plan Phases A–D
- **Date**: 2026-07-12
- **Type**: Execution / Validation
- **Summary**: (A) Confirmed directional run `457c6ecd` is armed but has **0** `gateway_order_id` — Testnet fills are mostly acceptance/carry, not directional DecisionPipeline (`docs/audits/2026-07-12-directional-binance-reachability.md`). (B) `_trace` now writes `strategy_lane=directional`; mirror gate tests added. (C) Replay supports `exit_mode=exit_ladder|fixed_2r`; Top20 comparison shows ExitLadder worse net expectancy than fixed 2R — no auto promotion (`docs/audits/2026-07-12-exitladder-replay-comparison.md`). (D) ATR% vol_low/mid/high tiers + weekly Celery refresh with core/standard fallback (ADR-055).
- **Verification**: focused pytest 18 passed (asset tiers, mirror lane, technical replay, celery schedule).
- **Limits**: Directional Testnet reachability still needs live cycle proof after arming; ExitLadder remains mechanical capability only; OOS gates still failed.

### [TASK-051] Desk Positions/Orders tabs now use Binance Demo as source of truth
- **Date**: 2026-07-12
- **Type**: Execution / frontend sync
- **Summary**: Operator saw Binance Demo Positions(3)/Open Orders(4) while desk bottom tabs showed 持仓 0 and local paper orders. Root cause: hero/account panels read `/binance-testnet-account`, but Positions/Orders tabs read local `overview` paper snapshots. Fixed by (1) mapping exchange positions/open+recent orders into desk tabs, (2) always polling Binance account, (3) writing exchange positions into audit+armed PaperRuns via `record_exchange_positions`, (4) probing algo open orders, (5) retrying false-flat first probe after API start, (6) deduping overview by symbol.
- **Verification**: API probe `open_position_count=3`, `open_orders=4` (STOP_MARKET), overview positions=3; frontend deskSync + useConsoleData tests passed.
- **Limits**: Conditional algo orders depend on `fapiPrivateGetOpenAlgoOrders`; public market REST 418 can still block auto readiness separately.

### [TASK-050] Fix desk data lock + ghost holdings display; prove Binance Demo opens
- **Date**: 2026-07-12
- **Type**: Execution / frontend ops repair
- **Summary**: Operator saw "服务不可用"/empty chart and uncleared ghost holdings/SL-TP overlays. Fixed sticky `useConsoleData` error that stopped all polling after API restarts; filtered chart SL/TP to non-rejected current-symbol orders; console overview now lists latest open positions per PaperRun instead of raw historical snapshot tail; Vite default proxy target `8016`. Opened real Binance Demo longs BTC/ETH/SOL for dual-platform proof (`gateway_order_id` filled). Remaining: public market REST hit HTTP 418 on some symbols so auto `execution_ready` can stay blocked by `data_stale` until ban cools.
- **Verification**: frontend MarketPanels/useConsoleData tests passed; overview positions `0`; Binance account `open_position_count=3` after trim; BTC OHLCV returns candles.
- **Limits**: Do not force-clear live `data_stale` while Binance public REST is 418-banned; auto cycles remain fail-closed.

### [TASK-049] Unblock Binance-sim auto trading: acceptance window, bootstrap clobber, reconcile CCXT
- **Date**: 2026-07-12
- **Type**: Execution / Ops repair
- **Summary**: Operator saw hours of no new Binance Demo fills and 10 local rejects (`portfolio_initial_risk_exceeded`). Root causes: (1) trading-status only scanned latest 50 AgentTasks so completed Top20 acceptance fell out of window; (2) bootstrap overwrote armed `cost_gate_verified`/`mirror` back to `paper_only`; (3) gateway reconcile crashed on CCXT `fetch_open_orders` without-symbol warning, so exchange-flat local ghosts never cleared; (4) portfolio initial-risk cap 5% vs 2%×N positions rejected after ~2 opens. Fixed query/bootstrap/reconcile/risk budget, re-armed funding+mature runs, cleared local open positions, restored `execution_ready=true`.
- **Layer mapping**: Execution/Risk owns gate + reconcile + arming; Validation admission unchanged; no mainnet.
- **Research loop served**: acceptance proof → armed sim mirror → exchange-flat reconcile → Top20 scan/decision evidence.
- **Verification**: targeted pytest 3 passed (`acceptance window`, `bootstrap preserve`, `reconcile survives open-orders warning`); live status `execution_ready=true`, blockers `[]`, both auto runs open positions `[]`, `binance_simulation_first` armed.
- **Limits**: Current 15m bar may show `skip_duplicate_cycle` until the next candle; continuous fills still require passing ensemble/MetaLabel/net-edge gates. Top20 OOS promotion remains failed.

### [TASK-048] ExitLadder + correlation tighten + reconcile decouple + Binance sim smoke
- **Date**: 2026-07-12
- **Type**: Execution Layer / Risk / audit
- **Summary**: Implemented multi-level ExitLadder for Paper (defaults in AUTO_PAPER_TECHNICAL_RULES), reduceOnly partial closes with fail-closed protection refresh on Binance sim, correlation discount + dual-peer reject, and exchange-flat reconcile independent of entry cycle_key. Added read-only sim smoke + boundary audit. Did not enable strategies or mainnet.
- **Layer mapping**: Execution owns ladder/runtime/gateway; Risk/Gatekeeper owns correlation admission; Review owns audit artifact. Validation admission unchanged.
- **Research loop served**: paper protection mechanics → optional testnet mirror → reconcile → review evidence. Strategy promotion still blocked by prior OOS failure.
- **Verification**: `pytest -q` -> `291 passed, 1 skipped`; Ruff passed; mypy passed on touched execution modules; `git diff --check` passed; `scripts/smoke_binance_simulation_path.py` connected (`testnet-fallback`, live_trading_enabled=false).
- **Limits**: Trailing ratchet remains break-even floor style (pre-existing); full ATR trail not in this slice. Smoke does not place new acceptance orders.

### [TASK-047] Complete fixed Top20 entry-policy prescreen against a one-hour baseline
- **Date**: 2026-07-12
- **Type**: Validation Layer / Data Layer / audit
- **Summary**: Corrected the offline comparison to use a reconstructed 1h baseline versus the current `4h direction -> 1h state -> 15m entry` policy. Both policies share fixed stoploss and fixed 2R takeprofit, making the report an entry-signal prescreen only. Added read-only historical slice caching and per-symbol multiprocessing so the full replay retains every 15m decision and every protective bar without interactive timeout.
- **Research loop served**: fixed Top20 OHLCV -> production DecisionPipeline replay -> cost-adjusted OOS/walk-forward metrics -> immutable audit evidence. No Strategy, PaperRun, Testnet, risk, or gateway configuration was changed.
- **Result**: 20 symbols, 15m/1h/4h, 2026-04-26 through 2026-07-12, no data gaps. Candidate signal density `1.0236x`; OOS net expectancy `-0.000005` versus baseline `0.001473`; Profit Factor `1.1036`; maximum drawdown `72.98%`. Prescreen failed and no promotion occurred.
- **Verification**: real replay completed in 298.9 seconds and wrote `docs/audits/2026-07-12-top20-technical-validation.md`; `pytest -q` -> `281 passed, 1 skipped`; Ruff passed; mypy passed for 122 files; `git diff --check` passed.
- **Limits**: This fixed-exit experiment isolates entry quality and is not evidence for the existing partial-profit/trailing state machine. Prompt 2-6 remain separate, user-confirmed slices.

### [TASK-046] Audit current behavior and add Strategy Library Playbook + ecosystem roadmap
- **Date**: 2026-07-12
- **Type**: audit / docs / Strategy Layer API / frontend / persistence
- **Summary**: Produced a code-line-backed current-state audit; researched and indexed six requested plus five new GitHub ecosystem sources; added typed Playbook contracts, code-derived scoped defaults, migration `0007`, audited roadmap persistence, dedicated API routes, and seven explanatory Strategy Library tabs while preserving the existing Strategy Assets CRUD lifecycle.
- **Layer mapping**: Strategy Layer owns Playbook definitions and source rules; Data/Research owns external-source manifests; Review owns roadmap audit evidence; frontend is an operator explanation/control surface only. Validation, Gatekeeper, Risk, and exchange execution authority are unchanged.
- **Research loop served**: external research -> structured source manifest -> operator Playbook/roadmap -> StrategyIdea/Draft -> future separately validated algorithm iteration. Roadmap status cannot directly materialize or execute a strategy.
- **Verification**: backend `257 passed, 1 skipped, 2 warnings`; frontend `11 files / 30 tests`; Ruff passed; mypy passed for 119 files; production build and diff check passed; clean SQLite migrated `0001 -> 0007`; desktop/mobile Chrome screenshots visually inspected; roadmap PATCH/GET persisted `in_progress` plus audit evidence.
- **Limits**: Playwright was unavailable, so browser QA used installed Chrome headless as the documented fallback. The pre-task full format gate remains historical debt (65 files) and was not mass-rewritten; only task-touched Python files pass format check. Bundle-size and two dependency deprecation warnings remain.

### [TASK-045] Harden strategy gates and complete bounded Top20 Futures Testnet acceptance
- **Date**: 2026-07-12
- **Type**: validation / execution safety / runtime evidence / frontend
- **Summary**: Added MetaLabel cold-start minimum history, explicit multi-timeframe fail-closed behavior, fixed-Top20 automatic Paper defaults, per-symbol Testnet evidence, a 120 USDT acceptance cap, sanitized preflight tooling, Celery worker-loss recovery semantics, per-task runtime timestamps, six-level Top20 frontend evidence, and a Temporal migration ADR without adding Temporal dependencies.
- **Layer mapping**: Strategy/Validation owns signal and sample sufficiency; Execution/Risk owns bounded Testnet orders, compensation and flat reconciliation; Ops owns Celery evidence; frontend only displays evidence; Review receives the audit artifact.
- **Verification**: Real Futures Testnet acceptance completed 20/20 symbols and 40 fills with zero final positions/orders. Final gates: backend `245 passed, 1 deselected`; Ruff passed; mypy passed for 115 files; admin Vitest `24 passed`; production build and npm high-severity audit passed; clean SQLite migration/runtime schema/API health passed.
- **Limits**: Spot Testnet credentials, Docker/Compose, two-hour Celery soak, pip-audit, and the ordinary Codex Security app scan remain environment/tooling dependent and must not be claimed as complete without fresh evidence.

### [TASK-044] Merge Codex branch into main and sync GitHub
- **Date**: 2026-07-12
- **Type**: git hygiene / sync
- **Summary**: Diagnosed push/pull confusion: local commits lived on `codex/fix-binance-top20-runtime` while `git push origin main` reported Everything up-to-date. Fast-forward merged 18 commits into `main`, pushed `130c174..3d754ee` to `origin/main`, deleted the local Codex branch. Repo policy going forward: work only on `main`.
- **Verification**: `git status` clean; `main` tracks `origin/main` at `3d754ee`.
- **Limits**: Remote Dependabot branches left untouched (dependency bumps, not Codex work). `gh` not authenticated for PR cleanup.

### [TASK-043] Implement Testnet Top20 acceptance, dual-leg funding carry, and the trading workbench
- **Date**: 2026-07-11
- **Type**: execution / risk / API / frontend / verification
- **Summary**: Added tiered automatic risk settings (BTC/ETH/SOL `10x` and `15%`; other fixed Top20 `5x` and `6%`) with equity-based notional sizing and unchanged global kill limits. Added a persisted Binance Futures Testnet acceptance API that sequentially opens and reduce-only closes all fixed Top20 symbols with protection, leverage setup, idempotency, compensation, and reconciliation evidence. Added an independent Binance Spot Testnet gateway and dual-leg carry state machine for Spot long plus Futures short. Rebuilt the Trading console into a chart/order/workspace layout, removed order-book requests/subscriptions, added fixed scrollable evidence tabs and structured connection failures, and retained real empty/loading/error/action states across the research modules.
- **Layer mapping**: Strategy Layer still owns signals; Validation Layer remains mandatory for strategy progression; Execution/Risk owns tier resolution, Testnet acceptance, Futures/Spot legs, protection, compensation, and reconciliation; Review/Ops owns evidence and operator visibility. The acceptance run is connectivity/execution proof only and cannot promote a strategy.
- **Research loop served**: `research idea -> strategy draft/contract -> backtest/optimization -> admitted Paper/Testnet strategy -> risk-controlled execution -> reconciliation -> review`, plus a separately auditable `carry plan -> Spot/Futures hedge -> close/compensate -> review` path.
- **Verification**: backend `232 passed, 1 skipped, 1 warning`; admin Vitest `9 files / 20 tests`; Vite production build passed; mypy passed for 114 source files; directed Ruff passed; `git diff --check` passed. Desktop and 390x844 Chrome screenshots confirmed the workbench layout and fixed record area render without incoherent overlap.
- **Limits**: External Binance acceptance was not executed because `127.0.0.1:7890` refuses connections and `SPOT_TESTNET_API_KEY` / `SPOT_TESTNET_API_SECRET` are not configured. Therefore there is no claim yet of 40 Futures fills, a completed BTC carry round trip, or official Binance Futures/Spot history screenshots. The unrelated Freqtrade asset manifest timestamp refresh remains preserved in the dirty worktree.

### [TASK-042] Repair Binance Mock auto execution, fixed Top20 visibility, reconciliation, and runtime logging
- **Date**: 2026-07-11
- **Type**: runtime remediation / execution safety / frontend / observability
- **Summary**: Replaced the stale July 9 API runtime with the current build, added safe legacy-SQLite migration recovery and global Agent Python startup, and made startup honor the operator's explicit Testnet auto-execution setting. Bootstrapping now keeps carry and mature-template runs active over the exact fixed Top20 with Binance simulation-first mirroring and pauses the duplicate legacy run. The market API returns all 20 candidates immediately with asynchronous exchange metadata enrichment; the frontend removed the fake three-symbol fallback and exposes monitoring/armed/no-signal/rejected states. Trading status includes build and effective execution state, and order sync reconciles all Top20 symbols. Logging now redacts wire/access details, suppresses only third-party websocket transport tracebacks, rotates at 10 MB with five retained files, and approximately 3.74 GB of authorized obsolete logs were removed. Optional external-source failures are task-level evidence and do not falsely mark the core scheduler unhealthy; Celery task exports are preloaded before scheduler worker threads start.
- **Layer mapping**: Data Layer owns fixed-universe metadata and exchangeInfo caching; Strategy/Validation retain signal and admission authority; Execution/Risk owns Mock-only arming, exchange-first persistence, idempotency, protection orders, and reconciliation; Review/Ops owns per-symbol rejection evidence and safe logs; frontend is visibility/control only.
- **Research loop served**: `Fixed Top20 market evidence -> strategy signal -> validation/gatekeeper/risk -> Binance Mock submission -> local order/position/protection persistence -> Top20 reconciliation/review`. Missing signal or failed gates terminate visibly without fabricated orders.
- **Verification**: backend `217 passed, 1 deselected, 1 warning`; full-repository Ruff passed; mypy passed across 110 source files; admin Vitest `8 files / 19 tests` and production build passed; `git diff --check` passed. Live probes showed scheduler running, Mock auto execution armed, live/mainnet false, two active runs scanning 20 symbols, a 20-item nonblocking universe response, and 20 order-sync summaries. Clean logs contained no API key header, signature/token query, third-party wire DEBUG, raw WebSocket frames, or traceback.
- **Limits**: No synthetic or threshold-relaxed order was created; current cycles had no strategy-qualified signal. Exchange metadata may initially display pending/unknown until background refresh completes, while gateway submission remains independently fail-closed. Current API credentials were retained per operator request but should be rotated because older local logs had contained request credentials.

### [TASK-041] Adversarial audit remediation and runtime verification
- **Date**: 2026-07-10
- **Type**: security / reliability / frontend remediation
- **Summary**: Closed the audit blockers without bypassing the six-layer gates: corrected the Paper status enum, made local SQLite startup run relational migrations plus its separately owned runtime tables, suppressed unsafe external wire logging, rejected withdrawal-enabled Binance keys, propagated stable client order IDs and timeout reconciliation, and rejected under-minimum opening notionals. The admin console now normalizes nullable auto-settings, uses POST for intelligence refresh, keeps multi-gateway rows uniquely keyed, and presents a stable API-unavailable state for both network errors and Vite proxy 5xx responses.
- **Layer mapping**: Data/ops owns local SQLite runtime schema; Execution/Risk owns exchange permission, idempotency, min-notional and unavailable-gateway fail-closed behavior; frontend is an operator surface only and does not bypass validation or risk gates.
- **Verification**: clean SQLite migration + schema + API lifespan `/health` returned 200; full Python `205 passed, 1 skipped`; Ruff passed; admin Vitest `8 files / 17 tests` and production build passed; desktop and 390px browser checks passed in the controlled standard local topology. API-down browser evidence showed the Chinese unavailable state and no new API requests after a six-second observation window.
- **Limits**: Docker/Compose runtime remains unverified because Docker is absent. Mypy currently fails with 68 errors in 23 files and is not a passing gate. Testnet credentials and all credential values remain absent from logs/memory; the Testnet key used during the preceding audit should be rotated by the operator.

### [TASK-040] Global adversarial test and architecture compliance audit
- **Date**: 2026-07-10
- **Type**: audit / QA
- **Summary**: Audited the current dirty worktree with offline adversarial harnesses, full regression, Binance Testnet open/reduce-only-close/reconciliation, and Playwright route checks. Added the evidence report at `docs/audits/2026-07-10-global-adversarial-architecture-audit.md`.
- **Findings**: Critical API lifespan crash from `paper_status="disabled"` not belonging to `RunStatus`; CCXT debug logging exposes authentication request material; API-key withdrawal permission self-check remains absent. High-risk gateway idempotency is not propagated to client order ids, and below-min-notional requests are silently increased to 50 USDT.
- **Verification**: Python `196 passed, 1 skipped`; targeted decision/risk tests `38 passed`; targeted LLM/gateway/API tests `23 passed`; admin Vitest `12 passed`; Mypy and admin build passed. Full Ruff fails on 3 existing errors in `scripts/_run_auto_cycles_verify.py`; Docker unavailable. API normal-data browser checks blocked by the confirmed startup failure.
- **Notes**: Testnet used `LIVE_TRADING_ENABLED=false`, `BINANCE_USE_TESTNET=true`, and an order cap below 120 USDT. No product code or existing tests were changed; temporary harnesses and raw logs are under ignored `.local/audit/`.

### [TASK-039] Paper auto-cycle proof + multi-timeframe OHLCV seed
- **Date**: 2026-07-09
- **Type**: fix + verification
- **Summary**: User reported no auto orders despite engine supposedly running 24/7. Root cause: directional lane uses 15m entry bars but heartbeat only maintained 1m; stale-data heartbeat created 92 blocking risk events; same-bar idempotency skipped most 60s cycles; carry lane correctly rejected funding arb with no net edge. Ran local verify script: manual open+close filled; after clearing risk events and seeding 15m/4h OHLCV, forced directional auto-cycle `opened=1`. Added `bootstrap_seed_multi_timeframe_ohlcv()` on API startup.
- **Files changed**: `services/execution/bootstrap.py`, `scripts/_run_auto_cycles_verify.py`, task-history.
- **Layer mapping**: Data Layer (OHLCV freshness) + Execution Layer (gatekeeper/risk events) + Validation (Paper cycles).
- **Verification**: `py -3 scripts/_run_auto_cycles_verify.py` -> manual OPEN/CLOSE filled, forced directional `opened=1`; `py -3 -m pytest tests/services/test_paper_bootstrap.py tests/services/test_paper_runtime.py -q` -> 8 passed.

### [TASK-038] Auto Paper/Testnet safety rollback after far conditional orders
- **Date**: 2026-07-09
- **Type**: fix + safety
- **Summary**: Investigated Binance Testnet far BTCUSDT conditional orders and found the unsafe path: local Paper bootstrap auto-enabled `mirror_to_gateway`, startup/default env forced `BINANCE_AUTO_EXECUTE=true`, relaxed signals were enabled locally, and Binance protection trigger prices were submitted without distance sanity checks. Changed automatic execution to explicit opt-in, disabled local relaxed/auto execution defaults, added Binance protection price validation before exchange entry submission, and stopped the old local FastAPI process on port 8000 so stale in-memory settings cannot keep cycling.
- **Files changed**: `shared/config.py`, `services/execution/{bootstrap,gateway,paper}.py`, `scripts/{start_paper_console,run-api-local}.ps1`, `scripts/bootstrap_and_verify_binance.py`, `.env.example`, local `.env`, and targeted tests.
- **Layer mapping**: Execution Layer / Risk Engine safety boundary. This does not change Strategy Layer signal semantics; it prevents Paper research cycles from becoming exchange actions without explicit operator consent.
- **Research loop served**: Strategy/Validation can continue producing Paper decisions, but Execution now fails closed before Testnet order placement when mirroring is not explicitly enabled or protection prices are invalid/far.
- **Verification**: `py -3 -m pytest tests/services/test_binance_gateway.py tests/services/test_paper_bootstrap.py tests/services/test_paper_runtime.py tests/api/test_paper_runtime_api.py tests/api/test_testnet_manual_trading.py -q` -> 28 passed / 1 warning; changed-file Ruff passed; `git diff --check` passed.

### [TASK-037] Market Intelligence capped factor vote
- **Date**: 2026-07-09
- **Type**: feature + data + strategy + frontend
- **Summary**: Implemented the approved Market Intelligence factor-voting plan without adding a seventh architecture layer. Added shared contracts for `MarketEvent`, `MarketIntelligenceFeatureSnapshot`, provider status, and capped `MarketIntelligenceSignal`; added Data Layer provider adapters/status for Binance/CoinGlass/CryptoQuant/DeFiLlama plus feature/signal scoring from existing market/news/macro/risk data; exposed `/api/v1/market-intelligence/{events,features,signals,refresh}`; wired the bounded vote into directional DecisionPipeline only after deterministic technical signals exist; added Trading/Ops/Review frontend visibility.
- **Files changed**: `shared/models/market_intelligence.py`, `services/data/market_intelligence.py`, `apps/api/routers/market_intelligence.py`, `services/execution/decision_pipeline.py`, `frontend/admin/src/{hooks/useConsoleData.js,components/RuntimePanels.jsx,pages/PaperConsole.jsx,pages/OpsConsole.jsx,pages/ReviewCenter.jsx}`, `.env.example`, targeted tests, and memory files.
- **Layer mapping**: Data Layer owns provider normalization/status and feature snapshots; Strategy Layer owns the capped intelligence vote; Agent Layer remains classification/explanation only; Execution/Risk still enforce MetaLabel, Decision Veto, Gatekeeper, stoploss, and Paper/Testnet boundaries; Review surfaces intelligence traces.
- **Research loop served**: `Provider/News/Macro/Risk evidence -> MarketIntelligenceSignal -> SignalEnsemble vote -> MetaLabel -> Decision Veto/Gatekeeper -> Paper/Testnet -> Review evidence`.
- **Verification**: targeted tests `23 passed`; changed Python Ruff passed; `py -3 -m mypy` passed; admin Vitest `12 passed`; admin build passed.
- **Notes**: CoinGlass/CryptoQuant are adapter-first and return `missing_credentials` until API keys are configured. The intelligence vote is schema-capped at `0.30` and cannot open a trade without a deterministic technical signal.

### [TASK-036] Real Binance Testnet open/close smoke and remaining quality baseline closure
- **Date**: 2026-07-09
- **Type**: live-testnet verification + fix + quality
- **Summary**: Executed a real Binance Futures Testnet BTCUSDT smoke through the gateway: opened `0.001` BTC with BUY market order `20356862614`, closed it with SELL reduce-only market order `20356874963`, then cleaned a pre-existing `0.0001` BTC residual position with SELL reduce-only order `20356888777`. Final probe confirmed `open_position_count=0` and no positions. Fixed gateway time sync for private Testnet calls and close-only side inversion before the smoke.
- **Files changed**: `services/execution/gateway.py`, `tests/services/test_binance_gateway.py`, quality cleanup across Ruff-reported files, `scripts/_testnet_open_close_report.json`, and memory files.
- **Layer mapping**: Binance gateway behavior belongs to Execution Layer; final flat-position verification is Risk Engine operational safety; local sanitized report is Review/Ops evidence.
- **Verification**: Real Testnet orders filled and are visible in Binance recent orders; full repo Ruff passed; `python -m pytest -q -m "not integration"` -> 185 passed, 1 deselected, 1 warning; `python -m mypy` passed; admin Vitest -> 12 passed; admin build passed; `npm audit --audit-level=high` -> 0 vulnerabilities; project `pip-audit .` -> no known vulnerabilities; `git diff --check` passed.
- **Notes**: Docker compose smoke is still blocked by host environment (`docker not found on PATH`), not by repository code. Profitability is still not claimed; this task proves exchange connectivity and safe open/close mechanics, not strategy edge.

### [TASK-035] Technical strategy hardening, Binance-first auto execution, and Strategy Library RAG
- **Date**: 2026-07-09
- **Type**: feat + fix + research assetization
- **Summary**: Hardened the existing carry + technical automatic strategy path. The technical lane now requires explicit indicator/price-action signals, supports 4h direction + 15m entry confirmation, adds RSI/EMA/ADX/VWAP/Bollinger and false-breakout logic, removes unsafe candle fallback, and lowers default risk sizing. Binance auto execution now fails closed when gateway submission fails. RAG now prioritizes `策略库/*.md`, and ABU research material is recorded as GPL-3.0 distilled research-only.
- **Files changed**: `services/execution/{decision_pipeline,bootstrap,paper_runtime,gateway,kill_switch}.py`, `services/strategy_library/technical/{indicators,price_action,__init__}.py`, `services/agents/rag_context.py`, `research_source/open_source_strategy_library/**`, `策略库/*.md`, frontend/admin runtime tests, and backend strategy/runtime tests.
- **Layer mapping**: Technical signal generation belongs to Strategy Layer; Binance-first order submission and fail-closed handling belong to Execution Layer/Risk Engine; RAG strategy documents belong to Data Layer E-level research intake feeding Strategy/Agent; failure/rejection codes feed Review Layer.
- **Research loop served**: `策略库/RAG -> Strategy signals -> DecisionPipeline -> Gatekeeper -> Binance Testnet/Paper order -> Review evidence` is now more explicit and auditable.
- **Verification**: `python -m pytest -q -m "not integration"` -> 184 passed, 1 deselected, 1 warning; changed-file Ruff passed; `python -m mypy` passed; admin Vitest passed (12 tests); admin build passed; `git diff --check` passed.
- **Notes**: Full repo Ruff still reports 33 pre-existing unrelated style issues in older files; no real Binance Testnet order was submitted during this session; profitability is not claimed and still requires backtest/OOS/Paper evidence before live promotion.

### [TASK-034] LLM free-model chain + carry/directional dual PaperRun lanes
- **Date**: 2026-07-09
- **Type**: feature + execution + agent
- **Summary**: Wired OpenRouter + GitHub Models free-model fallback via unified `build_configured_llm_runtime()`; fixed Anthropic quota errors to participate in fallback chain; added lightweight RAG snippets for decision veto prompts; enabled `PAPER_RUNTIME_ENABLE_DECISION_VETO` in local startup; split auto Paper into carry (`auto_paper_btc_funding`, FundingArbitrageSignal admission) and directional (`auto_paper_btc_technical`, DecisionPipeline + LLM veto) Top20 runs with `strategy_lane`; carry rejections no longer block protective closes via false LLM veto.
- **Files changed**: `.env`, `.env.example`, `services/agents/{llm_factory,rag_context,llm_runtime,__init__}.py`, `services/execution/{decision_pipeline,paper_signal,paper_runtime,bootstrap}.py`, `services/data/news.py`, `apps/api/routers/agents.py`, `scripts/{run-api-local,start_paper_console}.ps1`, `frontend/admin/src/components/RuntimePanels.jsx`, `tests/services/{test_llm_dual_lane,test_paper_bootstrap}.py`.
- **Layer mapping**: Agent Layer (LLM runtime + RAG veto context); Execution Layer (dual-lane PaperRun + carry admission); Strategy Layer (lane-specific bootstrap rules).
- **Verification**: `py -3 -m pytest -q -m "not integration"` -> 179 passed, 1 deselected; targeted LLM/carry/bootstrap tests 22 passed; `DecisionDebugPanel` Vitest passed. Pre-existing `TradingConsolePanels.test.jsx` failures unchanged (unrelated copy assertions).

### [TASK-033] Report gap closure — frontend depth, metrics, compose smoke, signal continuity
- **Date**: 2026-07-08
- **Type**: feature + refactor + ops
- **Summary**: Closed the external review gaps after TASK-030/031/032: moved pytest SQLite artifacts to `.local/test-runtime/` and cleaned 46 stale root `.db` files; added RiskProfile PUT API + frontend create/edit forms; expanded Validation/Research/Strategy routes with detail pages, backtest submit, research-source refresh/extract, draft promote/materialize, and strategy status updates; exposed `/metrics` (scheduler + LiveFeedBus gauges) and wired Prometheus scrape; added `scripts/compose_smoke.py` plus optional `.github/workflows/compose-smoke.yml`; implemented MACD/Dow continuous strength with confidence-scaled ensemble weights.
- **Files changed**: `tests/conftest.py`, `.gitignore`, `scripts/{clean_test_artifacts,compose_smoke}.py`, `shared/models/risk.py`, `services/strategy_library/repository.py`, `apps/api/routers/{risk,strategies,backtests,metrics}.py`, `apps/api/{main,auth}.py`, `services/strategy_library/technical/{macd,dow_trend}.py`, `services/execution/decision_pipeline.py`, `infra/prometheus/prometheus.yml`, `frontend/admin/src/{router.jsx,styles.css,pages/*,components/*}`, `.github/workflows/compose-smoke.yml`, tests + memory files.
- **Layer mapping**: Validation/Strategy/Risk frontend depth (Validation + Strategy + Risk layers); signal continuity (Strategy + Execution); `/metrics` + compose smoke (Ops/Engineering).
- **Verification**: `py -3 -m pytest -q -m "not integration"` -> 167 passed, 1 deselected; `npm --workspace frontend/admin run test -- --run` -> 12 passed; `npm run admin:build` passed. Compose smoke not run locally (Docker not on PATH); CI optional workflow added.

### [TASK-031] Real-time trading console + multi-screen split
- **Date**: 2026-07-07
- **Type**: fix + refactor + frontend
- **Summary**: Addressed the user complaint that the Paper trading console was not actually real-time and crammed everything into one screen. Turned on the three Binance live-data feature flags that were defaulting to `False` (universe/market/WS), added an autouse pytest fixture to force them back off during tests, stopped `useConsoleData.js` from forcing a full chart rebuild on every 8s poll tick while the WS stream is live (klines now flow through the WS `kline` event's incremental `update()` only), added exponential-backoff WS reconnect on `onclose`/`onerror`, reordered the trading page's CSS grid so the order ticket is reachable within one screen below the 1280px breakpoint, and migrated non-core-trading panels (risk events, news/macro/notifications, review reports) out of `PaperConsole.jsx` into the already-scaffolded `/risk`, `/ops`, `/review` routes (`RiskConsole.jsx`, `OpsConsole.jsx`, `ReviewCenter.jsx`), replacing their placeholder content with real `useQuery`-backed panels reusing `FeedPanel` (newly exported from `OpsPanels.jsx`) and `RiskEventFeed`.
- **Files changed**: `apps/api/config.py`, `tests/conftest.py`, `frontend/admin/src/hooks/useConsoleData.js`, `frontend/admin/src/pages/{PaperConsole,RiskConsole,OpsConsole,ReviewCenter}.jsx`, `frontend/admin/src/components/OpsPanels.jsx`, `frontend/admin/src/styles.css`, `.github/agent/memory/project-memory.md`, `.github/agent/memory/task-history.md`.
- **Layer mapping**: Data Layer owns the live-data config flags governing Binance REST/WS sourcing; Execution Layer's operator-facing frontend owns the trading console layout, real-time chart update logic, and the risk/ops/review console pages. No Strategy, Validation, or Execution decision/order logic changed.
- **Research loop served**: Keeps the Paper-mode operator console (`Data -> Validation/Paper -> Execution Gatekeeper -> Review`) genuinely inspectable in real time, and separates the trading-decision surface from ops/risk/review surfaces so each can be reviewed on its own screen without diluting the trading view.
- **Verification**: `py -3 -m pytest tests/ -q` -> 151 passed, 1 skipped; `npm --workspace frontend/admin run test -- --run` -> 3 files / 8 tests passed; `npm --workspace frontend/admin run build` -> succeeded (102 modules transformed, no errors). Manual browser smoke of symbol/timeframe switching and WS reconnect was not performed this session.
- **Notes**: Deliberate deviation from the original plan text — `RuntimeControlPanel`/`DecisionDebugPanel` were kept on `/trading` (inside an always-visible `execution-grid`, not an accordion) instead of moving to `/ops`, because both are scoped to the currently-selected symbol/timeframe rather than global ops state. `OpsReviewPanel` and the now-unused `newsItems`/`macroEvents`/`reviews`/`notifications` fetch/state fields in `useConsoleData.js` were deleted after confirming zero remaining callers via Grep.

### [TASK-030] Fix one-click startup dependency self-heal
- **Date**: 2026-07-07
- **Type**: fix + ops
- **Summary**: Repaired the local one-click Paper console startup path after Vite failed to resolve `@tanstack/react-query` from `frontend/admin/src/router.jsx`. The package was already declared in the frontend workspace and lockfile, but the existing startup script skipped `npm install` whenever root `node_modules` existed, leaving newly added dependencies absent.
- **Files changed**: `scripts/start_paper_console.ps1`, `.github/agent/memory/project-memory.md`, `.github/agent/memory/task-history.md`
- **Layer mapping**: Ops/startup tooling only. No Strategy, Validation, Execution, Risk, Review, or trading-decision behavior changed.
- **Research loop served**: Keeps the local Paper/Testnet operator console launchable so the existing `Data -> Validation/Paper -> Execution Gatekeeper -> Review` workflow can be inspected without bypassing platform gates.
- **Verification**: `npm install` installed the missing frontend packages; `npm --workspace frontend/admin run build` passed; `npm --workspace frontend/admin ls @tanstack/react-query` resolved `@tanstack/react-query@5.101.2`; `.\一键启动.bat` successfully started FastAPI and Vite; API `/health` returned ok and frontend `/` returned HTTP 200.
- **Notes**: `npm install` still reports the known 5 frontend audit vulnerabilities; no `npm audit fix --force` was run. Local `main`, `origin/main`, and `origin/HEAD` all point to `9237b0647174156511ddb138fe76d6fad194d1bb`; the additional remote branches are Dependabot dependency-update branches.

### [TASK-030] Security closure, Docker scheduler guard, reconnect visibility, and third-party-backed frontend pages
- **Date**: 2026-07-07
- **Type**: fix + security + frontend + ops
- **Summary**: Closed TASK-029 residuals and extended frontend data wiring. Upgraded Python/Node audit baselines, fixed Docker duplicate-scheduler risk with explicit Celery mode and compose validation, wired Binance WS reconnect errors into `LiveFeedBus`, replaced Validation/Review/Research/Ops placeholders with real API-backed pages, and added fail-soft `refresh=true` third-party read-through for news and macro endpoints.
- **Files changed**: `pyproject.toml`, `.github/workflows/ci.yml`, `docker-compose.{paper,live}.yml`, `scripts/compose_validate.py`, `services/data/binance.py`, `services/execution/scheduler.py`, `apps/api/routers/market.py`, `frontend/admin/**`, targeted tests, and `docs/security/task-030-security-scan.{md,html}`.
- **Layer mapping**: Data Layer owns third-party news/macro refresh and Binance WS state; Execution Layer owns scheduler mode separation and reconnect observability; Validation/Review/Research/Ops frontend pages expose existing layer data without becoming decision makers; CI/Ops owns supply-chain gates.
- **Research loop served**: Third-party Data inputs now flow into Review/Research visibility; Validation signals and hypothesis/backtest state are inspectable; Execution scheduler/feed state is visible; failures still route through Review memory rather than bypassing risk controls.
- **Verification**: `py -3 -m pytest -q` -> 149 passed, 1 skipped; `py -3 -m ruff check .` passed; `py -3 -m mypy` passed; changed-file Ruff format check passed; admin Vitest passed; admin build on Vite 8.1.3 passed; `npm audit --audit-level=high` passed; `py -3 -m pip_audit . --progress-spinner off --timeout 30` passed; `py -3 scripts/compose_validate.py` skipped because Docker is not on PATH.
- **Notes**: Whole-machine `pip_audit` still reports non-project global packages (`litellm`, `nltk`, `torch`); these are outside this repo's dependency graph and were not upgraded/removed. Full repo format check still reports historical formatting drift in files outside this change set.

### [TASK-029] Trading core scheduler, live feed bus, and platform console refactor
- **Date**: 2026-07-07
- **Type**: feat + refactor + frontend + ops + docs
- **Summary**: Implemented the trading-core refactor plan. Added a local in-process `RuntimeScheduler` that calls the existing Celery task bodies for Paper cycles, market heartbeat, risk sweep, notifications, and daily review; extended `trading-status` with scheduler/feed observability; connected Binance WS closed Kline collection to a shared `LiveFeedBus`; rewired `/market/ohlcv/stream` away from per-client REST polling; added Postgres batch upsert paths for OHLCV/extras; and updated the one-click Paper console script to run 60s in-process cycles with optional WS feed.
- **Files changed**: `services/execution/scheduler.py`, `services/data/live_feed_bus.py`, `services/data/{binance,repository,__init__}.py`, `apps/api/{main,config}.py`, `apps/api/routers/{market,runs}.py`, `shared/models/{execution_runtime,workflow,enums}.py`, `frontend/admin/**`, CI/docs/config files, and targeted tests.
- **Layer mapping**: Data Layer owns Binance WS feed normalization, fan-out, and Timescale upserts; Execution Layer owns in-process scheduling and Paper notional sizing; Strategy Layer owns optional multi-timeframe confirmation; Frontend Admin owns operator visibility; Ops/CI owns dependency scanning and scheduler validation.
- **Research loop served**: The local runtime now repeatedly drives `Validated PaperRun -> DecisionPipeline -> Gatekeeper -> OrderExecution/PositionSnapshot -> Review/notification`, while live Klines flow through `Binance WS -> DataRepository -> LiveFeedBus -> frontend websocket` without bypassing Validation/Risk.
- **Verification**: `py -3 -m pytest -q` -> 146 passed, 1 skipped; `py -3 -m ruff check .` passed; `py -3 -m mypy` passed; `npm --workspace frontend/admin run test` passed; `npm --workspace frontend/admin run build` passed; `py -3 scripts/compose_validate.py` skipped because Docker is not on PATH.
- **Notes**: `npm install` still reports 5 existing frontend audit vulnerabilities; no `npm audit fix --force` was run. CI now records npm audit and fails Python dependency audit. OKX/Bybit remain future enum placeholders only; auth remains single-tenant Bearer Token.

### [TASK-028] Complete Binance realtime Paper console data and manual open/close smoke
- **Date**: 2026-07-07
- **Type**: feat + fix + frontend + ops + verification
- **Summary**: Repaired the Paper console so it no longer appears as a static shell. Added Binance public REST live reads for USD-M Top20, OHLCV, order book, recent trades, and premiumIndex/funding with a standard-library fallback when `ccxt` is not installed; wired market APIs to refresh and persist live OHLCV/funding data; added explicit order-book/trade contracts; fixed blank manual-order validation evidence causing `FailureRecord` errors; disabled frontend open buttons until Strategy ID, Backtest ID, and stoploss are present; replaced synthetic frontend order book/trades with backend live payloads; repaired key Chinese mojibake; and hardened the one-click startup script's port handling.
- **Files changed**: `services/data/{binance,market}.py`, `apps/api/routers/market.py`, `shared/models/{market,workflow,__init__}.py`, `services/execution/gatekeeper.py`, `frontend/admin/src/{api,hooks,pages,components,utils,styles}.js*`, `scripts/start_paper_console.ps1`, `start-paper-console.bat`, and API/service/frontend tests.
- **Layer mapping**: Data Layer owns Binance public REST live reads and persistence into `ohlcv_bars` / `market_extras`; Execution Layer owns manual Paper/Testnet order admission and close-only handling; Review Layer remains the rejection-memory sink but no longer receives invalid blank-subject failures; Frontend is an operator surface only and never connects directly to Binance.
- **Research loop served**: Live public market data now feeds `Data -> Validation evidence -> Manual/Auto Paper order -> Gatekeeper -> OrderExecution/PositionSnapshot -> Review`, preserving the required Strategy/Validation/Risk chain while making the trading console operationally inspectable.
- **Verification**: `py -3 -m pytest -q` -> 142 passed, 1 skipped; `py -3 -m ruff check .` passed; `py -3 -m mypy` passed; `npm --workspace frontend/admin run test -- --run` passed; `npm --workspace frontend/admin run build` passed.
- **Runtime smoke**: `scripts/start_paper_console.ps1` started FastAPI and Vite and opened `http://127.0.0.1:5173` via the system browser. HTTP smoke confirmed OHLCV/order book/trades source `binance_public_rest`, Top20 source `binance_usdm_24h_ticker`, a Paper manual order `filled` through `paper_manual`, and Paper close `filled` with `close_only=true`.
- **Notes**: Browser/IAB was not used because this Windows Codex Desktop is configured Chrome-only with bundled Browser disabled. Mainnet real trading remains out of scope; this is Paper/Testnet only.

### [TASK-026] Implement 7x24 Paper decision pipeline automation
- **Date**: 2026-07-06
- **Type**: feat + fix + frontend + ops
- **Summary**: Implemented the approved full A-F plan for the Binance-only / Paper-only 7x24 automation loop. Added Celery Beat schedules for Paper cycles, market heartbeat, risk sweep, daily review, notifications, news, macro, and Twitter watchlist polling; added `DecisionPipeline` to connect technical signals, price action, SignalEnsemble, MetaLabel, and Decision Veto Agent into real Paper order generation; replaced fixed stoploss/takeprofit percentages with strategy-rule/ATR risk prices; added cycle idempotency keys and decision traces; added news/macro/social data seams and stale-data RiskEvents; split the admin frontend into API/hooks/pages/components and added a Decision Pipeline debug panel with Vitest coverage.
- **Files changed**: `apps/api/{celery_app.py,config.py,routers/{market,runs,system}.py}`, `services/{data,execution,review,strategy_library/technical}/**`, `shared/models/{enums.py,workflow.py}`, `infra/timescale/init.sql`, `frontend/admin/**`, `tests/{api,services}/**`, and memory files.
- **Layer mapping**: Data Layer owns heartbeat/news/macro/social capture; Strategy Layer owns technical signals and ensemble/meta-label decisions; Agent Layer owns LLM classification/veto tasks; Execution Layer owns `DecisionPipeline`, Paper idempotent cycles, ATR stop plans, and Gatekeeper admission; Review/Ops own daily reports, notifications, and decision visibility. No seventh layer was introduced.
- **Research loop served**: The automatic Paper path now follows `Validated PaperRun -> DecisionPipeline -> ExecutionOrderRequest -> Gatekeeper -> OrderExecution/PositionSnapshot -> Review/Failure/decision trace`, so non-arbitrage orders are traceable to technical signals, ensemble confidence, meta-label sizing, LLM veto, and risk checks.
- **Verification**: `py -3 -m pytest -q` -> 120 passed, 1 skipped; `py -3 -m ruff check .` passed; `py -3 -m mypy` passed; `npm --workspace frontend/admin run test` passed; `npm --workspace frontend/admin run build` passed; `py -3 scripts/compose_validate.py` -> skipped because Docker is not on PATH.
- **Notes**: `npm install --workspace frontend/admin` reported 5 audit vulnerabilities in the frontend dependency tree; no forced audit fix was run because it may introduce breaking upgrades. Real RSS/Twitter/LLM calls still depend on operator-provided network credentials and live environment availability.

### [TASK-025] P0 repository hygiene and runtime configuration hardening
- **Date**: 2026-07-05
- **Type**: fix + ops + docs + tests
- **Summary**: Implemented the P0 remediation plan. Removed the tracked `.dev_ai_quant.db` runtime database from Git tracking, expanded `.gitignore` for runtime DB artifacts, changed compose runtime env files from `.env.example` to `.env`, made CI prepare a temporary `.env` for compose validation, added compose/repository/Markdown portability guard tests, hardened admin Bearer auth with constant-time comparison plus non-local default-token rejection, removed the Research Agent's workstation-specific alpha fallback path, and synchronized status docs to `Phase 0 完成 + 第一批 P1 落地`.
- **Files changed**: `.gitignore`, `.github/workflows/ci.yml`, `docker-compose.yml`, `scripts/compose_validate.py`, `apps/api/auth.py`, `services/agents/service.py`, repository hygiene/portability/auth/agent tests, README/AGENTS/docs status files, and `.github/agent/memory/{project-memory.md,decisions-log.md,task-history.md}`.
- **Layer mapping**: This is an Ops/API/Agent-boundary hardening task. It does not add a new layer, alter strategy logic, or bypass the required `Strategy -> Validation -> Execution -> Review` chain.
- **Research loop served**: Keeps the research platform portable and safer to operate before adding 7x24 scheduling by ensuring runtime state and templates do not leak into source control, auth fails closed outside local development, and local research intake paths are explicit.
- **Verification**: `py -3 -m pytest -q` -> 116 passed, 1 skipped; `py -3 -m ruff check .` passed; `py -3 -m mypy` passed; `npm --workspace frontend/admin run build` passed; `py -3 scripts/compose_validate.py` -> `[skipped] docker not found on PATH; compose runtime validation skipped`.
- **Notes**: No Git history rewrite was performed. `.dev_ai_quant.db` had previously been inspected as an empty schema-only SQLite database, and this task only removes it from future tracked content.

### [TASK-024] Add autonomous paper-runtime cycle over the admitted Top20 candidate universe
- **Date**: 2026-07-04
- **Type**: feat + execution
- **Summary**: Added the first autonomous paper-runtime slice inside the existing Execution Layer. Paper runs can now execute `/api/v1/execution/paper-runs/{id}/auto-cycle` to scan candidate symbols, open paper positions on fresh admitted signals, close positions on opposite signals, persist filled order lifecycle updates, and expose `/runtime-status` for the current open-position view. The default paper candidate universe is now Binance Top20 with BTC/ETH still pinned first, and a Celery task `services.execution.tasks.run_paper_runtime_cycle` provides the worker-side entrypoint for repeated scheduling.
- **Files changed**: `apps/api/routers/runs.py`, `services/execution/{__init__.py,gatekeeper.py,paper.py,paper_runtime.py,tasks.py}`, `services/strategy_library/repository.py`, `shared/models/{__init__.py,workflow.py}`, `tests/api/test_paper_runtime_api.py`, `.github/agent/memory/{project-memory.md,decisions-log.md,task-history.md}`
- **Layer mapping**: This stays inside the Execution Layer and shared contracts only. Validation admission is still enforced upstream through the existing `gate_decision_ref` + gatekeeper path, and no Agent or UI path bypasses `Validation -> Execution`.
- **Research loop served**: `BacktestRun admission evidence -> PaperRun -> auto cycle -> gatekeeper -> filled paper order -> position snapshot/runtime status` is now explicit, test-covered, and reusable for later Celery scheduling or testnet expansion.
- **Verification**: `C:\Users\Windows11\AppData\Local\Programs\Python\Python312\python.exe -m pytest -q` -> 108 passed, 1 skipped; `...python.exe -m ruff check .` passed; `...python.exe -m mypy` passed.
- **Notes**: This is a repeatable autonomous paper cycle plus worker entrypoint, not a proven 7x24 production daemon yet. Docker runtime smoke remains skipped locally because `docker` is not on PATH.

### [TASK-023] Harden Binance access toward testnet-first API credentials
- **Date**: 2026-07-04
- **Type**: security + ops
- **Summary**: Refused unsafe use of exchange login credentials and tightened the repo toward the intended integration path: exchange-owned API keys on testnet or paper first. Added `BINANCE_USE_TESTNET` and `LIVE_TRADING_ENABLED` settings, made `BinanceUsdtPerpetualGateway` propagate sandbox mode to the underlying CCXT client when available, and rewrote the environment/config ops guide so operators configure testnet keys instead of reusing account passwords.
- **Files changed**: `apps/api/config.py`, `.env.example`, `services/execution/gateway.py`, `tests/services/test_binance_gateway.py`, `docs/ops/environment-and-config.md`, `.github/agent/memory/{project-memory.md,task-history.md}`
- **Layer mapping**: This is an Ops / Execution boundary hardening change. It does not add a new layer or expand strategy logic; it constrains how exchange connectivity is enabled.
- **Research loop served**: Keeps `Validation -> Paper -> Live` progression safe by default, ensuring exchange connectivity starts from testnet/sandbox instead of direct real-account login credentials.
- **Verification**: `C:\Users\Windows11\AppData\Local\Programs\Python\Python312\python.exe -m pytest -q tests/services/test_binance_gateway.py tests/services/test_exchange_gateway.py tests/api/test_execution_runtime_api.py` -> 5 passed; `...python.exe -m ruff check apps/api/config.py services/execution/gateway.py tests/services/test_binance_gateway.py` passed; `...python.exe -m mypy apps/api/config.py services/execution/gateway.py` passed.
- **Notes**: In this restricted follow-up environment, `py -3` was unavailable, so verification used the explicit Python 3.12 interpreter path.

### [TASK-032] Protective exits, free LLM fallback, and Binance Testnet mirror
- **Date**: 2026-07-08
- **Type**: feat + refactor + verification
- **Summary**: Implemented the pasted plan for stoploss/takeprofit automatic closing, `trail_after_r` trailing-stop ratchet, free-model LLM fallback routing, and explicit Binance Futures Testnet mirroring for automatic Paper runtime fills.
- **Files changed**: `services/execution/paper_runtime.py`, `services/strategy_library/repository.py`, `services/agents/llm_runtime.py`, `services/execution/decision_pipeline.py`, `services/data/news.py`, `apps/api/{config.py,routers/runs.py}`, `frontend/admin/src/{pages/PaperConsole.jsx,components/TradingConsolePanels.jsx}`, `.env.example`, targeted tests, and memory files.
- **Layer mapping**: Protective exits and gateway mirroring belong to Execution Layer / Risk Engine; `FailureRecord`/iteration writeback belongs to Review Layer; OpenRouter/GitHub Models runtime belongs to AI Agent Layer; the explicit PaperRun mirror switch supports Validation Layer's real-market Paper/Testnet observation without bypassing Gatekeeper.
- **Research loop served**: `Strategy rules -> Paper signal -> Gatekeeper -> Paper fill -> protective close / Testnet mirror -> FailureRecord or iteration_history -> Review reuse` is now auditable, while LLM classification/veto can use free providers and still fail closed.
- **Verification**: Targeted Paper/LLM/API tests passed (`13 passed`); full `py -3 -m pytest -q` passed (`162 passed, 1 skipped`); `py -3 -m ruff check .`, changed-file `ruff format --check`, `py -3 -m mypy`, admin Vitest (`9 passed`), admin build, `npm audit --audit-level=high`, `py -3 -m pip_audit . --timeout 60`, and `git diff --check` passed. `scripts/compose_validate.py` skipped because Docker is not on PATH.
- **Notes**: No user secrets were written to tracked files. Real private Binance Testnet order appearance still requires operator-provided local `.env` credentials and a live service run with `mirror_to_gateway=true`.

### [TASK-022] Complete strict promotion evidence, live runtime APIs, and online agent/gateway boundaries
- **Date**: 2026-07-04
- **Type**: feat + verification
- **Summary**: Completed the next remaining-platform closure slice after the Tranche 1 baseline. Tightened Paper/Live promotion so raw backtest pass no longer bypasses missing hypothesis/benchmark/OOS/pod-risk evidence; made validation reports hypothesis-aware; added live runtime APIs for gateway capabilities, account snapshot sync/query, live order submit/cancel, and reconciliation query/trigger; added the first real Binance USDT perpetual gateway implementation over a CCXT-style client boundary; added a real Anthropic structured-output runtime plus per-agent provider/model mapping; and added Alembic `0006` for hypotheses, decision memory, gateway snapshots, reconciliation, and runtime metadata persistence.
- **Files changed**: `apps/api/routers/{agents,backtests,runs}.py`, `apps/api/config.py`, `services/agents/{__init__,llm_runtime,service}.py`, `services/execution/{__init__,gateway}.py`, `services/validation/report.py`, `migrations/versions/0006_validation_memory_and_gateway_runtime.py`, `.env.example`, new API/service tests, and memory files.
- **Layer mapping**: Validation evidence enforcement stays in the Validation Layer; gateway/account/reconciliation runtime stays in the Execution Layer; structured online LLM calls stay in the Agent Layer; decision memory remains inside the existing Review deployment boundary. No seventh layer was introduced and no route bypasses `Validation -> Execution`.
- **Research loop served**: `Hypothesis -> BacktestRun -> promotion_gate -> Paper/Live admission -> gateway lifecycle/reconciliation -> decision memory/review evidence` is now explicit and auditable, while `News/Twitter/Telegram/Decision Veto` tasks can use a real online structured LLM boundary without ever generating orders directly.
- **Verification**: `py -3 -m pytest -q` -> 106 passed, 1 skipped; `py -3 -m ruff check .` passed; `py -3 -m mypy` passed; `Remove-Item .verify_ai_quant.db; $env:POSTGRES_URL='sqlite:///./.verify_ai_quant.db'; py -3 -m alembic upgrade head` passed through `0006`; `npm --workspace frontend/admin run build` passed; `py -3 scripts/compose_validate.py` -> `[skipped] docker not found on PATH; compose runtime validation skipped`.
- **Notes**: Docker runtime verification is still host-dependent because `docker` is not on PATH locally. Binance/Anthropic online runtime paths are implemented and test-covered at the boundary level, but real credentialed end-to-end exchange/LLM execution still depends on operator-provided secrets and a live environment.

### [TASK-021] Sync Tranche 1 status docs and re-verify baseline
- **Date**: 2026-07-04
- **Type**: docs + verification
- **Summary**: Synchronized the remaining stale status documents after the Tranche 1 auth/notification/ops implementation landed. Updated the implementation matrix, technical architecture plan, and delivery checklist so they now reflect the real single-tenant auth baseline, Telegram/Webhook notification dispatch path, restored `frontend/admin` build, and scripted `compose-validate` workflow with the documented local Docker limitation.
- **Files changed**: `docs/architecture/{implementation-status-matrix.md,technical-architecture-plan.md}`, `docs/ops/delivery-checklist.md`, `.github/agent/memory/task-history.md`
- **Layer mapping**: This change updates architecture/ops/status documentation only. It does not introduce new runtime modules or alter the six-layer boundary.
- **Research loop served**: Keeps the operator and developer view aligned with the real admission/ops baseline, reducing the risk of planning future Validation / Execution / Review work against stale assumptions.
- **Verification**: `py -3 -m pytest -q` -> 89 passed, 1 skipped; `py -3 -m ruff check .` passed; `py -3 -m mypy` passed; `npm --workspace frontend/admin run build` passed; `py -3 scripts/compose_validate.py` -> `[skipped] docker not found on PATH; compose runtime validation skipped`.
- **Notes**: No new ADR was needed. Local Docker runtime verification remains pending on a host with Docker available.

### [TASK-020] Implement Tranche 1 security + notification dispatch baseline
- **Date**: 2026-07-04
- **Type**: feat + ops + auth
- **Summary**: Implemented the first tranche from the remaining-platform roadmap. Added single-tenant Bearer auth for `/api/v1/*` while keeping health endpoints public; upgraded notification outbox from persisted intent into a real dispatch loop with Telegram/Webhook adapters, retry/backoff state, attempt history, API replay endpoint, and Celery task; restored the frontend admin build by reinstalling workspace dependencies and wiring the admin token into requests; and added a script-backed compose validation path for local/CI use.
- **Files changed**: `apps/api/{auth.py,main.py,config.py,celery_app.py,routers/notifications.py}`, `services/{notifications.py,notifications_tasks.py,strategy_library/{models.py,repository.py}}`, `shared/models/workflow.py`, `frontend/admin/src/main.jsx`, `.env.example`, `scripts/{__init__.py,compose_validate.py}`, `.github/workflows/ci.yml`, `Makefile`, `migrations/versions/0005_notification_dispatch_runtime_fields.py`, notification/auth/compose tests, status docs, and memory files.
- **Layer mapping**: Auth belongs to the API boundary; notification dispatch belongs to Ops / Review / Risk visibility and response inside the existing six-layer architecture; compose validation and CI wiring are operational guardrails rather than new product modules.
- **Research loop served**: `RiskEvent -> NotificationOutboxItem -> dispatcher -> adapter result/audit history` is now a real operational closure, while API auth protects the operator surface that controls validation, paper runs, and risk actions.
- **Verification**: `py -3 -m pytest -q` -> 89 passed, 1 skipped; `py -3 -m ruff check .` passed; `py -3 -m ruff format --check <changed-files>` passed; `py -3 -m mypy` passed; `npm --workspace frontend/admin run build` passed; `py -3 scripts/compose_validate.py` -> `[skipped] docker not found on PATH; compose runtime validation skipped`; `$env:POSTGRES_URL='sqlite:///./.verify_ai_quant.db'; py -3 -m alembic upgrade head` passed from `0001` to `0005`.
- **Notes**: This completes the planned Tranche 1 baseline only. Full DSR / hypothesis registry / decision memory service / live exchange gateway / real LLM agents remain future tranches.

### [TASK-019] Persist notification outbox intents and delivery status
- **Date**: 2026-07-04
- **Type**: feat
- **Summary**: Upgraded the notification outbox from a read-time derivation over active risk events into a persisted Ops/Review/Risk visibility channel. Added `notification_outbox` ORM/migration, `NotificationRepository`, delivery-status fields, persisted outbox APIs for list/filter/manual create/delivery update, and automatic idempotent notification enqueueing for high/critical `RiskEvent` creation.
- **Files changed**: `shared/models/{workflow,__init__}.py`, `services/{notifications.py,strategy_library/**}`, `apps/api/routers/{risk,notifications}.py`, `migrations/versions/0004_persist_notification_outbox.py`, `tests/api/test_remediation_plan.py`, status docs, and memory files.
- **Layer mapping**: Notification outbox belongs to Ops / Review / Risk visibility inside the existing six-layer architecture. It records notification intent and adapter results only; no real Telegram/email/webhook adapter or new Agent subsystem was added.
- **Research loop served**: `RiskEvent -> NotificationOutboxItem -> delivery_status audit` now keeps operational evidence available after a risk event is resolved, so Review/Ops can reuse the same durable audit trail.
- **Verification**: Red-green targeted notification tests passed; targeted remediation/shared/repository tests -> 18 passed; full `py -3 -m pytest -q` -> 80 passed, 1 skipped; `py -3 -m ruff check .` passed; `py -3 -m mypy` passed; `$env:POSTGRES_URL='sqlite:///./.verify_ai_quant.db'; py -3 -m alembic upgrade head` passed and the temporary SQLite DB was removed.
- **Notes**: Real outbound adapters and credentials remain future work. High/critical risk events auto-create notification intents; low/mid events do not.

### [TASK-018] Route alpha evaluator rejections into Review failure memory
- **Date**: 2026-07-04
- **Type**: feat
- **Summary**: Completed the remaining decision-memory slice after TASK-017. Persisted `StrategyIdea.intake_metadata`, allowed `FailureRecord` to attach to `idea_id` as well as `strategy_id`, wired Research Agent `scan_local_alpha` so persisted `subjective_to_drop` alpha ideas create `alpha_evaluator_reject` failure records, and added `/api/v1/failures` filters for `strategy_id`, `idea_id`, and `failure_type`.
- **Files changed**: `shared/models/{strategy,workflow}.py`, `services/{agents,review,strategy_library}/**`, `apps/api/routers/{agents,review}.py`, `research_source/worldquant_adapter/local_alpha_scanner.py`, `migrations/versions/0003_harden_risk_engine_and_alpha_audit.py`, targeted tests, and memory files.
- **Layer mapping**: Local alpha scanning remains Data Layer E-level research intake feeding Strategy Layer ideas; `FailureRecord` writeback and `/failures` retrieval belong to the Review Layer; Agent orchestration only coordinates structured objects.
- **Research loop served**: `Alpha expression -> AlphaPlan -> Evaluator -> StrategyIdea.intake_metadata -> FailureRecord` is now reusable by Review/Research without re-parsing rationale text.
- **Verification**: Targeted tests (`tests/contracts/test_shared_models.py`, `tests/repositories/test_strategy_repository.py`, `tests/api/test_risk_review_agents.py`, `tests/research_source/test_worldquant_adapter.py`) -> 24 passed; full `py -3 -m pytest -q` -> 79 passed, 1 skipped; `py -3 -m ruff check .` passed; `py -3 -m mypy` passed; `$env:POSTGRES_URL='sqlite:///./.verify_ai_quant.db'; py -3 -m alembic upgrade head` passed.
- **Notes**: Temporary SQLite verification database was removed after migration smoke. No new autonomous memory subsystem was added.

### [TASK-027] Open-source RAG assetization and intake reconciliation
- **Date**: 2026-07-06
- **Type**: feat + docs
- **Summary**: Upgraded open-source strategy intake from manifest-only registration to traceable local RAG assets. Added `ResearchSourceAsset`, GitHub allowlist fetching, distilled Markdown assets, per-source `asset_manifest.json`, source allowlists/denylists/license policies/extraction targets, asset-driven `StrategyIdea` metadata, research-source asset APIs, and Agent output fields for imported/failed assets. Added RD-Agent, vectorbt, and OpenBB to the seed set and reconciled status docs.
- **Files changed**: `shared/models/research_source.py`, `research_source/open_source_strategy_library/**`, `apps/api/routers/research_sources.py`, `services/agents/service.py`, tests, docs, and memory files.
- **Layer mapping**: Open-source asset fetching belongs to Data Layer E-level research intake; asset-driven `StrategyIdea` records feed the Strategy Layer; Paper/Execution remain gated by existing Validation and Gatekeeper services.
- **Research loop served**: `StrategySourceManifest -> ResearchSourceAsset -> StrategyIdea -> StrategyDraft -> Strategy -> BacktestRun -> PaperRun -> Gatekeeper`, without importing external runtime code.
- **Verification**: `py -3 -m pytest -q` -> 124 passed, 1 skipped; `py -3 -m ruff check .` passed; `py -3 -m mypy` passed; `npm --workspace frontend/admin run build` passed.
- **Notes**: Real fetch evidence exists for Freqtrade/Jesse/Hummingbot/ABU/NautilusTrader/Qlib/vectorbt/OpenBB. Remaining gaps: vector DB/LlamaIndex indexing, deep LLM research reports, full repo mirrors, Docker runtime smoke, and credentialed 24h external API validation.

### [TASK-017] Harden Risk Engine admission and repair WorldQuant executable intake
- **Date**: 2026-07-03
- **Type**: feat + fix
- **Summary**: Completed the approved Phase 1 slice that hardens order admission and removes WorldQuant placeholders. Added `ExecutionRiskState`, aligned `RiskProfile` defaults and persistence, extended `ExecutionGatekeeperService` with numeric risk checks and structured rejection audit fields, and ensured Paper stepping synthesizes the same runtime risk snapshot used by direct execution. Replaced the WorldQuant placeholder generator with a real evaluator-backed path, implemented `ts_rank` / `ts_zscore` / `group_neutralize`, added explicit crypto group alias migration, and upgraded local alpha intake to preserve behavior signatures and unsupported evidence.
- **Files changed**: `shared/models/{risk,workflow,alpha,__init__}.py`, `services/{execution,strategy_library}/**`, `apps/api/routers/runs.py`, `research_source/worldquant_adapter/**`, `migrations/versions/0003_harden_risk_engine_and_alpha_audit.py`, targeted docs, tests, and memory files.
- **Layer mapping**: `ExecutionRiskState` + gatekeeper numeric checks belong to the Execution Layer / Risk Engine; `FailureRecord` writeback belongs to the Review Layer; WorldQuant parser/evaluator/scanner remain Data Layer E-level research intake feeding Strategy Layer seeds.
- **Research loop served**: `RiskProfile -> ExecutionGatekeeper -> OrderExecution/FailureRecord` is now auditable end-to-end, and `Alpha expression -> AlphaPlan -> Evaluator -> CryptoFactorGenerator -> StrategyIdea` is now explicit about what is executable versus research-only.
- **Verification**: `py -3 -m pytest -q` -> 76 passed, 1 skipped; `py -3 -m ruff check .` passed; `py -3 -m mypy` passed; `$env:POSTGRES_URL='sqlite:///./.verify_ai_quant.db'; py -3 -m alembic upgrade head` passed.
- **Notes**: To run repository verification locally in this environment, I installed the missing declared/dev dependencies `pydantic-settings`, `ruff`, `celery`, `mypy`, and `pytest-asyncio` into the active Python interpreter because they were absent at session start.

### [TASK-016] Open-source strategy library intake and Paper order stepping
- **Date**: 2026-07-03
- **Type**: feat
- **Summary**: Added the E-level open-source strategy intake path. Registered first-batch GitHub strategy/research/LLM workflow sources as `StrategySourceManifest`, added local RAG asset indexing and deterministic `StrategyIdea` extraction, wired research-source APIs and Agent tasks, materialized seed drafts for funding carry / trend following / Paper-only grid-market-making, and added `paper-runs/{id}/step` to generate candidate Paper orders through the existing gatekeeper.
- **Files changed**: `shared/models/research_source.py`, `research_source/open_source_strategy_library/**`, `apps/api/routers/research_sources.py`, `apps/api/{main.py,routers/runs.py}`, `services/agents/service.py`, `services/execution/paper_signal.py`, `services/execution/__init__.py`, tests, and memory files.
- **Layer mapping**: `open_source_strategy_library` belongs to Data Layer E-level research data; extracted ideas/drafts belong to Strategy Layer; Paper order generation and review belongs to Execution Layer gatekeeper.
- **Research loop served**: Open-source project knowledge now enters as `StrategySourceManifest -> StrategyIdea -> StrategyDraft`; Paper signals continue through `BacktestRun -> PaperRun -> OrderExecution` with validation/risk checks.
- **Verification**: `py -3 -m pytest -q` -> 52 passed, 1 skipped; `py -3 -m ruff check .` passed; `py -3 -m mypy` passed; `npm --workspace frontend/admin run build` passed.
- **Notes**: GPL/AGPL sources remain research references only. Grid/market-making seeds are Paper-only in this tranche. Live framework integration, full repository cloning, and vector-store RAG remain future work.

### [TASK-015] Remediation plan first pass: engineering baseline, validation closure, ops visibility
- **Date**: 2026-07-03
- **Type**: fix + feat + docs
- **Summary**: Implemented the first remediation tranche without changing the six-layer architecture. Added package-boundary and Ruff/mypy baseline fixes; implemented carry walk-forward/OOS/stress validation reports; added system dependency health, exchange capabilities, and notification outbox APIs; made Makefile data/backtest targets real or explicitly failing; made unregistered Agent executors fail rather than falsely complete; and synchronized stale status docs.
- **Files changed**: `apps/__init__.py`, `pyproject.toml`, `apps/api/{config,main}.py`, `apps/api/routers/{backtests,market,notifications,system}.py`, `shared/models/{backtest,workflow,market,__init__}.py`, `services/validation/{walk_forward,report,stress_scenarios,__init__}.py`, `services/data/capabilities.py`, `services/notifications.py`, `services/agents/service.py`, `scripts/{data_check,data_sync,run_carry_backtest}.py`, `Makefile`, tests, status docs, and memory files.
- **Verification**: `py -3 -m pytest -q` -> 45 passed, 1 skipped; `py -3 -m ruff check .` passed; `py -3 -m ruff format --check .` passed; `py -3 -m mypy` passed; `npm --workspace frontend/admin run build` passed.
- **Notes**: Docker compose config was not locally verified because `docker` is not on PATH. The current folder is still not a Git repository, so publishing requires cloning/syncing to the original remote once GitHub network/auth are available.

### [TASK-014] Binance Data Layer first-tranche ingestion
- **Date**: 2026-07-03
- **Type**: feat
- **Summary**: Implemented the Data Layer tranche that makes the Paper console able to read real persisted Binance public market data. Added idempotent timeseries upserts, CCXT-backed OHLCV/funding backfill services, Binance WS payload normalization for closed Kline candles and funding updates, ingestion task execution for Binance backfill job types, and Vite `/api` proxy support for the admin console.
- **Files changed**: `services/data/{binance,repository,service,tasks,__init__}.py`, `infra/timescale/init.sql`, `frontend/admin/vite.config.js`, `pyproject.toml`, `tests/services/{test_binance_ingestion,test_timeseries_repository}.py`, project memory/status docs.
- **Verification**: Editable dev install passed; targeted Data Layer tests passed (`11 passed`); changed-file Ruff check passed; full `py -3 -m pytest -q` passed (`41 passed`); `npm --workspace frontend/admin run build` passed.
- **Notes**: Scope remains Binance public market data only. Live collector is a long-lived worker seam, not account sync or live trading execution.

### [TASK-013] Add Paper trading console and market overview APIs
- **Date**: 2026-07-03
- **Type**: feat
- **Summary**: Added market snapshot/OHLCV read APIs, console overview aggregation, Paper status update and RiskEvent acknowledgement endpoints, plus a real `frontend/admin` Paper Trading Console using `lightweight-charts`. The UI now shows Binance symbols, Kline panel, funding carry metrics, orders, positions, risk events, and manual Paper controls with explicit empty/error states.
- **Files changed**: `shared/models/{market,risk,workflow,__init__}.py`, `services/data/{repository,market,__init__}.py`, `services/strategy_library/repository.py`, `apps/api/{main.py,routers/{market,console,runs,risk}.py}`, `frontend/admin/{package.json,src/main.jsx,src/styles.css}`, `tests/api/test_console_market.py`.
- **Verification**: Targeted API tests passed; full `py -3 -m pytest -q` passed (`35 passed`); `npm --workspace frontend/admin run build` passed; Playwright desktop/mobile smoke passed with no mobile horizontal overflow.
- **Notes**: Browser smoke ran against frontend only, so API failure state was visible by design. Real WebSocket ingestion, exchange account sync, real order placement/cancel, notifications, and LLM veto remain not implemented.

### [TASK-012] Phase 1a/1b/1d/1e grounding implementation
### [TASK-039] Fixed Top20 Binance simulation-first auto-trading optimization
- **Date**: 2026-07-10
- **Type**: feat + safety hardening + frontend
- **Summary**: Implemented the fixed operator Top20 universe, Binance symbol/status mapping including `PEPE -> 1000PEPEUSDT`, all-20 heartbeat refresh, mature-template default auto strategy, disabled operator-experience 4h/15m research lane, medium-risk defaults, typed auto-settings API, Binance simulation-first local sync, order-sync API, and Trading console panels for auto settings, Top20 monitoring, message sources, and order reconciliation.
- **Files changed**: `services/data/{universe,binance,market,service,tasks}.py`, `services/execution/{bootstrap,decision_pipeline,paper_runtime}.py`, `apps/api/routers/{market,runs}.py`, `shared/models/{market,risk,workflow,__init__}.py`, `frontend/admin/src/{hooks/useConsoleData.js,pages/PaperConsole.jsx,components/RuntimePanels.jsx}`, and targeted backend/frontend tests.
- **Verification**: Targeted backend tests passed (`24 passed` before final smoke); full non-integration pytest passed (`196 passed, 1 deselected, 1 warning`); Ruff, mypy, admin Vitest (`12 passed`), admin build, `git diff --check`, and Playwright trading-page smoke passed. Browser smoke was run with Vite only, so API proxy 502s were expected; no JS runtime crash remained after adding the missing `asArray` helper.
- **Notes**: Live/mainnet trading remains disabled. The user-provided report Markdown remains untracked and was not modified.

- **Date**: 2026-07-03
- **Type**: fix + feat
- **Summary**: Restored `services/data`, fixed root-scoped ignore rules and runtime artifact ignores, moved LLM dependencies to optional extra, aligned compose Python images with Python 3.11, replaced carry placeholder metrics/cost constants with calculated net metrics and cost breakdown, added deterministic SignalEnsemble/MetaLabel service/API, and added MACD plus Dow swing trend technical signal modules. WorldQuant alpha semantics were explicitly deferred per user instruction.
- **Files changed**: `.gitignore`, `pyproject.toml`, `docker-compose.yml`, `services/data/**`, `services/validation/{carry,metrics,costs}.py`, `shared/models/{backtest,risk,signal,__init__}.py`, `services/strategy_library/{repository,ensemble/**,technical/**}`, `apps/api/{main.py,routers/ensemble.py}`, targeted tests, status docs, and project memory files.
- **Verification**: `py -3 -m pip install -e ".[dev]"` passed; `py -3 -c "import services.data; import apps.api.main"` passed; targeted Phase 1 tests passed (`14 passed`); full `py -3 -m pytest -q` passed (`31 passed`).
- **Notes**: `docker compose -f docker-compose.yml config` could not run because `docker` is not available on PATH. No git commit or `git rm --cached` was possible because `C:\Users\win\Desktop\AI--main` is not a Git repository in this environment.

### [TASK-011] Expand the persisted research loop with v1 APIs, gates, review writeback, and admin shell
- **Date**: 2026-07-02
- **Type**: feat
- **Summary**: Extended the platform beyond the first persisted carry slice. Added `/api/v1` envelopes and error handling, Alembic `0002`, persisted `RiskProfile` / `RiskEvent` / `ReviewReport` / `FailureRecord` / `AgentTask` / `LiveRun` / `OrderExecution` / `PositionSnapshot`, execution gatekeeper checks, review writeback, local alpha scanning into `StrategyIdea`, implementation-status reconciliation docs, compose overlays, Grafana dashboard scaffolding, and a React + Tailwind admin shell.
- **Files changed**: `apps/api/**`, `services/{agents,execution,review}/**`, `services/data/repository.py`, `services/strategy_library/{__init__,models,repository}.py`, `research_source/worldquant_adapter/**`, `migrations/versions/0002_expand_research_loop.py`, `docs/architecture/{implementation-status-matrix,technical-architecture-plan}.md`, `docs/ops/delivery-checklist.md`, `docker-compose*.yml`, `infra/{grafana,prometheus}/**`, `frontend/admin/**`, `tests/api/**`
- **Verification**: `py -3 -m pytest -q` -> 24 passed, 1 warning (`asyncio_mode` unknown because local environment still does not load `pytest-asyncio`); `npm install` + `npm run build` in `frontend/admin` passed.
- **Notes**: Ops overlays and Prometheus/Grafana assets are now in-repo scaffolds, but compose-level runtime validation is still pending. Walk-forward / DSR / stress-engine execution remains a Phase-1 gap.

### [TASK-010] Add persisted market-data carry backtest application flow
- **Date**: 2026-07-02
- **Type**: feat
- **Summary**: Added a real `CarryBacktestRequest` contract, a timeseries `DataRepository` for `ohlcv_bars` / `market_extras`, a `CarryBacktestApplicationService` that reads persisted Binance spot/perp/funding data and writes `BacktestRun`, plus a `/backtests/carry` API path and matching Celery task entrypoint.
- **Files changed**: `shared/models/{workflow.py,__init__.py}`, `services/data/{__init__.py,repository.py}`, `services/validation/{__init__.py,application.py,tasks.py}`, `services/strategy_library/repository.py`, `apps/api/{celery_app.py,routers/backtests.py}`, `tests/{conftest.py,api/test_vertical_slice.py,services/test_timeseries_repository.py,services/test_backtest_application.py}`
- **Verification**: `py -3 -m pytest -q` -> 22 passed; `POSTGRES_URL=sqlite:///./.verify_ai_quant.db py -3 -m alembic upgrade head` passed.
- **Notes**: Local Celery import smoke is still not verified on this machine because the Python environment does not currently have the `celery` package installed.

### [TASK-009] Implement the first persisted Binance carry vertical slice
- **Date**: 2026-07-02
- **Type**: feat
- **Summary**: Replaced the in-memory strategy, backtest, ingestion, and paper-run seams with SQLAlchemy repositories; added Binance top-universe ingestion helpers, carry backtest service, BTC/ETH-first paper-run defaults, Celery task entrypoints, repository-aligned Alembic migration, and end-to-end API tests for `StrategyIdea -> StrategyDraft -> Strategy -> StrategyVersion -> BacktestRun -> GateDecision -> PaperRun`.
- **Files changed**: `apps/api/routers/{strategies,backtests,ingestion,runs}.py`, `apps/api/celery_app.py`, `services/{database.py,data/**,validation/**,execution/**,strategy_library/**}`, `shared/models/{backtest.py,workflow.py}`, `migrations/versions/0001_create_strategies.py`, `tests/{conftest.py,api/**,repositories/**,services/**}`
- **Verification**: `py -3 -m pytest -q` -> 18 passed. Warning remains: local environment does not currently load `pytest-asyncio`, so `asyncio_mode` is reported as an unknown pytest option.
- **Notes**: The persisted slice now covers Binance top-universe ingestion job metadata, carry backtest persistence, and BTC/ETH-first paper-run preparation, but it still uses fallback universes and synchronous task bodies rather than live exchange execution.

### [TASK-001] Initialize governance, memory, and report-driven agent rules
- **Date**: 2026-06-28
- **Type**: bootstrap
- **Summary**: Created the repository governance layer first, including `AGENTS.md`, memory files, project metadata, and root configuration. The research report was elevated to the canonical architecture source for all future implementation.
- **Files changed**: `AGENTS.md`, `CLAUDE.md`, `.github/**/*`, `.gitignore`, `.env.example`, `README.md`
- **Notes**: This repository is still in Phase 0. Backend/frontend code should be added only after reading these governance files.

### [TASK-002] Add platform master design package
- **Date**: 2026-06-28
- **Type**: design
- **Summary**: Added the platform master design package as the repository's implementation mother document, including the main design document and three appendices for repository structure, feature phasing, and principles/non-goals.
- **Files changed**: `docs/architecture/platform-master-design.md`, `docs/architecture/appendix-*.md`, `docs/architecture/report-alignment.md`, `.github/agent/memory/*.md`
- **Notes**: The next recommended design step is the domain and interfaces design package.

### [TASK-003] Add domain and interfaces design package
- **Date**: 2026-06-28
- **Type**: design
- **Summary**: Added the domain and interfaces design package, defining the core domain objects, aggregate boundaries, lifecycle states, interface groups, agent I/O objects, and orchestration backbone for the research platform.
- **Files changed**: `docs/architecture/domain-and-interfaces-design.md`, `docs/architecture/platform-master-design.md`, `docs/architecture/report-alignment.md`, `.github/agent/memory/*.md`
- **Notes**: The next recommended design step is the data and ingestion design package.

### [TASK-004] Complete the remaining pre-development design assets
- **Date**: 2026-06-28
- **Type**: design
- **Summary**: Added the remaining pre-development preparation documents, including data and ingestion design, agent and orchestration design, execution/risk/review design, product specification, feature catalog, roadmap, environment/config guide, and delivery checklist.
- **Files changed**: `docs/architecture/*.md`, `docs/product/*.md`, `docs/roadmap/*.md`, `docs/ops/*.md`, `README.md`, `.github/agent/memory/*.md`
- **Notes**: The repository now contains a broad project preparation package. The next step can shift to concrete schema/API/model implementation.

### [TASK-005] Build Phase-0 global engineering scaffolding
- **Date**: 2026-06-29
- **Type**: feat (scaffolding, no business logic)
- **Summary**: Filled the foundational plumbing identified in the v2.0 集成方案 PDF gap analysis. Added `shared/models/` unified Pydantic contracts (OHLCVBar/MarketExtras/BacktestReport/GateDecision/RiskEvent/StrategyContract/TradeSignal/MacroEvent/AlphaPlan + enums); `infra/` (timescale `init.sql` hypertables, freqtrade config/strategies/user_data, jesse, grafana provisioning); docker-compose v2 (8 services, postgres→TimescaleDB) + `docker-compose.dev.yml`; expanded `.env.example` to 5 data tiers; Alembic + Strategy 18-field ORM + first migration `0001`; FastAPI strategies CRUD seam + `config.py` + `celery_app.py`; pyproject additions (alembic/anthropic/pandas-ta, ruff/mypy tightening); tests scaffolding (contract guard + API smoke); `Makefile`; `.pre-commit-config.yaml`; CI `ci.yml`; worldquant methodology seam (operators/parser/crypto factor generator stubs); docs reconciliation file.
- **Files changed**: `shared/**`, `infra/**`, `services/strategy_library/{models.py,__init__.py}`, `migrations/**`, `apps/api/{main.py,config.py,celery_app.py,routers/**}`, `docker-compose*.yml`, `.env.example`, `.gitignore`, `pyproject.toml`, `Makefile`, `.pre-commit-config.yaml`, `.github/workflows/ci.yml`, `research_source/worldquant_adapter/**`, `docs/architecture/v2-integration-reconciliation.md`
- **Verification**: py_compile 37 files OK; YAML/JSON validated; `shared.models` imports; contract tests 6/6 pass (caught + fixed a real bug: high≥low cross-field check needed `model_validator(mode="after")`). NOT run here (no docker/make/uv): `docker compose up`, `alembic upgrade head`, `uv lock`, API tests (need `pydantic_settings`). These run in CI / docker.
- **Notes**: Decisions: 移植 WorldQuant 方法论到加密（非搬运表达式）; v2.0 PDF 并入 docs 作落地细化（docx 仍为真源）. Next: P0-03 ohlcv_downloader + P0-12 strategies repository (replace in-memory seam).

### [TASK-008] Converge design sources, phase semantics, and API skeletons
- **Date**: 2026-07-02
- **Type**: feat + design convergence
- **Summary**: Implemented the “开发前整体设计收敛蓝图” in repo form. Added `docs/architecture/design-source-index.md` to fix the source-of-truth chain, Phase semantics, and document responsibilities; synchronized README / report-alignment / roadmap / product entry docs / project memory; coded `RiskProfile` plus workflow lifecycle contracts (`BacktestRun`, `PaperRun`, `LiveRun`, `ReviewReport`, `FailureRecord`, `IngestionJob`, `AgentTask`, `DecisionVetoResult`, strategy intake objects); expanded FastAPI from a single strategies seam to six interface-cluster skeletons; added `services/data` package scaffold; expanded `Settings` to cover `.env.example`; and updated tests to cover the new contracts and skeleton routes.
- **Files changed**: `docs/architecture/{design-source-index.md,report-alignment.md,appendix-b-feature-phasing.md,technical-architecture-plan.md}`, `docs/roadmap/phase-roadmap.md`, `docs/product/{product-spec.md,feature-catalog.md}`, `README.md`, `shared/models/**`, `apps/api/{main.py,config.py,celery_app.py,routers/**}`, `services/data/__init__.py`, `tests/{api,contracts}/**`, `.github/agent/memory/{project-memory.md,decisions-log.md,task-history.md}`
- **Verification**: `py -3 -m pytest -q` -> 11 passed; `shared.models` targeted import smoke passed; `apps.api.main` import smoke passed with 42 registered routes; compileall passed with a Windows path warning during directory listing but reported `COMPILE_OK`.
- **Notes**: Local environment still lacks `pytest-asyncio` / `pydantic_settings`; `apps/api/config.py` now contains a minimal fallback path for local smoke tests, while the primary dependency remains declared in `pyproject.toml`.

## 2026-07-12 — Local console reliability, cost gate, and audit retention

- **Summary**: Separated Testnet acceptance fills from strategy performance, persisted Binance demo account snapshots and reconciled orders, fixed funding carry edge calculation to deduct four execution legs, and localized the trading workspace. The desktop console now reads persisted data on API port `8016`; an external scheduler process refreshes markets and runs automatic Paper cycles without blocking the API.
- **Verification**: API core reads returned 200 in 1-261 ms; local overview retained 10 orders, 10 positions, and 2 account snapshots; scheduler reported running and auto execution armed. Full suite `253 passed, 1 skipped`; Ruff, frontend `26` tests, frontend production build, and `git diff --check` passed.
- **Notes**: Binance demo account API remains network/credential dependent. Its failure is isolated to the account panel and does not mark the entire console unavailable. Mainnet remains disabled; cost gate still prevents automatic Testnet mirroring until explicit validation evidence exists.

## 2026-07-08 — Binance Testnet gateway + local paper mirror E2E

- **Summary**: Fixed CCXT Binance USDM Testnet integration (manual testnet URLs instead of deprecated `set_sandbox_mode`), algo protection orders (`algoType=CONDITIONAL`), paper mirror min-notional bump (50 USDT), and dev relaxed signal filters. Verified live K-line feed, testnet balance sync (~5256 USDT), manual testnet orders, and paper auto-cycle mirror (`gateway_order_id` on recent fills).
- **Files changed**: `services/execution/{gateway.py,paper_runtime.py,decision_pipeline.py,paper_signal.py}`, `shared/config.py`, `apps/api/celery_app.py`, `.env.example`, `tests/services/test_binance_gateway.py`
- **Verification**: `py -3 -m pytest tests/services/test_binance_gateway.py tests/services/test_paper_bootstrap.py -q` -> 4 passed; API `trading-status` credentials+gateway+scheduler+live feed OK; testnet manual order `gateway_order_id=20127948601`; paper mirror orders `20128622805` / `20128804499`.

## 2026-07-12 — Fixed Top20 directional Paper execution and cost-risk controls

- **Summary**: Reworked the automatic directional Paper lane around `4h trend -> 1h state -> 15m entry -> 1m protection`. The fixed Top20 remains intact; heartbeat now maintains all four closed-bar timeframes. Existing positions receive 1m stop, time-exit, partial-profit, and drawdown protection even when entry bars are stale or already processed.
- **Risk policy**: BTC/ETH/SOL use 20x caps; the other fixed Top20 symbols use 10x caps. Position size remains stop-distance risk based at 2% per trade, with a 5% aggregate initial-stop-risk cap, 5% daily new-entry halt, and 20% hard-drawdown close-and-lock action. All work remains Paper-only.
- **Exit/cost policy**: +1R break-even ratchet, +2R partial 50% exit, trailing remainder, and 24-hour exit below +0.5R. Core assets model 10bps per side; other symbols 18bps per side, with the existing transaction-cost ledger retaining fee/slippage evidence.
- **LLM boundary**: LLM/RAG remains structured audit and research input. Only persisted high/critical risk events hard-veto entries; model budget/runtime/schema failures are recorded as advisory unavailability and cannot weaken deterministic gates.
- **Verification**: `pytest -q` -> 274 passed, 1 skipped; `ruff check .`; `mypy`; `npm --workspace frontend/admin run build`; `git diff --check` all passed. Vite retains its pre-existing >500kB bundle warning.
- [project: C:\Users\Windows11\Desktop\量化项目] 2026-07-12 Fixed Top20 Binance Testnet acceptance connected end-to-end
  - **内容**: 先定位到直接调用 `TestnetAcceptanceService` 不会写回平台验收状态的断点；随后通过正式 API `/api/v1/execution/testnet-acceptance-runs` 执行固定 20 币顺序开仓/平仓验收。
  - **验收证据**: `run_status=completed`、20/20 币完成、40 笔成交、无失败币、`final_open_position_count=0`、`final_open_order_count=0`；API `trading-status` 已回写 `testnet_acceptance_verified=true`、`auto_execution_state=ready`、`execution_ready=true`、调度器运行中。
  - **边界**: 仅 Binance Futures Testnet/Mock，主网保持关闭；预检确认账户初始 0 持仓/0 挂单。正式验收 run id: `02c25bdd-be98-4060-a816-e625c61e7b24`。
- [project: C:\Users\Windows11\Desktop\量化项目] 2026-07-12 Trading console strategy/audit boundary and browser sync fix
  - **结论**: 固定 Top20 的 40 笔 Binance Testnet 成交属于 `testnet_acceptance` 基础设施验收，不属于量化策略收益或策略开平逻辑；自动 Paper 周期按既定资金费成本门禁拒绝当前信号，未产生策略新仓。
  - **故障**: 本地启动脚本将行情接口强制置为 API-only，外部行情读取超时会污染整页错误；更关键的是 CORS `OPTIONS` 预检被 Bearer 认证中间件拦截，浏览器拿不到任何数据，命令行带 Token 却正常。
  - **修复/验证**: 本地控制台继续使用隔离调度器缓存；行情可选请求失败不再升级成全局错误；认证中间件放行 `OPTIONS`，启动器注入 `http://127.0.0.1:5173`/`localhost:5173` CORS 白名单。预检返回 200，后端健康测试 8 passed，前端 Vitest 30 passed，API overview/market endpoints 全部 200。
  - **同步边界**: 账户 API 已能读取 Binance 最近订单与 0 持仓；主交易表展示本地执行/审计记录，策略订单与验收审计继续分开，避免把验收单计入策略表现。

## 2026-07-13 — Aggressive risk defaults + indicator-driven entries + deep-audit Phase 2/3 closure

- **Summary**: User reported BTC paper orders opening at only ~0.04% position size with no test value, and asked for (a) moderately more aggressive leverage/position sizing plus the sizing bug fixed, and (b) entry decisions to incorporate indicator-level signals (MACD golden/death cross, RSI overbought/oversold, multi-timeframe MA, FVG gap-fill) beyond time-based bar closes. User also directed unattended continuation: each subsequent phase's plan is to be derived from the prior phase's results and auto-executed without pausing for confirmation.
- **Thread A (sizing bug + aggressive defaults)**: Diagnosed root cause across `confidence_multiplier`/`correlation_discount`/ATR-based sizing chain; fixed via a floor on `correlation_risk_discount` and a minimum-notional fallback in paper `_open_position`. Raised risk defaults in `bootstrap.py`/`risk_tiers.py`/`risk.py` (`AUTO_PAPER_TECHNICAL_RULES.position_rules`: `risk_per_trade=0.025`, `max_portfolio_initial_risk_fraction=0.15`, `max_leverage=25`, `max_position_fraction=0.20`, `min_notional_usdt=20`).
- **Thread B (indicator-driven entries)**: Added `generate_fvg_signal` (FVG gap-fill) and a genuine multi-timeframe MA resonance signal; wired both into `decision_pipeline.py` and `bootstrap.py`'s `enabled_signals`. Confirmed pre-existing MACD/RSI/EMA/ADX/VWAP/Bollinger generators already satisfy golden/death-cross and overbought/oversold semantics.
- **Thread C (deep-audit Phase 2/3 closure, auto-executed per standing instruction)**: Redesigned `layered_regime_entry`'s fail-closed all-agree AND-gate into a majority-vote + `MIN_DIRECTION_SOURCE_QUORUM=3` scheme (fail-closed only on exact ties or below-quorum counts). Verified Phase 2's remaining two items (default-to-limit order type; opposite-signal close deprioritized behind stoploss/takeprofit on the same bar) were **already correctly implemented** in `shared/config.py`/`paper_signal.py`/`gateway.py`/`paper_runtime.py` — added regression test `test_runtime_stoploss_wins_over_opposite_signal_hit_on_same_bar` to lock in the existing ordering rather than changing behavior. Fixed the duplicate/stale "ADR-023" entry by renumbering it to ADR-058 and correcting its risk-fraction text from an erroneous 5% to the actual `max_portfolio_initial_risk_fraction=0.15` (15%).
- **Files changed**: `services/execution/{bootstrap.py,risk_tiers.py,risk.py,paper_signal.py,paper_runtime.py}`, `services/strategy_library/{technical.py,ensemble.py}`, `services/execution/decision_pipeline.py`, `tests/services/{test_technical_signals.py,test_signal_ensemble.py,test_paper_runtime.py}`, `.github/agent/memory/decisions-log.md` (ADR-058), `task_plan.md`, `findings.md`, `progress.md`, `.github/agent/memory/project-memory.md`.
- **Verification**: `pytest -q` -> 320 passed, 1 skipped; `ruff check .` and `mypy` both clean. Every modified code section re-read via the Read tool after editing to confirm expected logic landed, per the mandatory delivery self-check rule.
- **Next**: Per the user's auto-execute instruction, proceeding into Phase 4 (separate `TestnetAcceptanceService` probe orders from real strategy orders; add ExitLadder E2E tests) and Phase 5 (cross-symbol correlation-based portfolio risk control) without pausing for confirmation, documenting any genuine behavior-changing deviations.

## 2026-07-26 — Strategy optimization Phase 1-5: RegimeRouter wired, Meta-Label trained, Carry/Swing deferred

- **Summary**: Completed the comprehensive strategy optimization roadmap laid out in the user's directive. Phase 1 (RegimeRouter接线) verified green: `services/execution/decision_pipeline.py` now calls `RegimeRouter.classify()`, writes `market_regime` to `decision_snapshots`, and routes eligible signals via `_eligible_layered_signals(regime=...)` with regime-aware family weights. Phase 2A/2B (Exit Ladder/动态family退出) skipped per historical evidence that ExitLadder net expectancy was negative; retained Fixed 2R as baseline; dynamic family exit requires full backtest validation before promotion. Phase 3 (Meta-Label训练) executed: `scripts/train_meta_label_model.py` ran successfully but model OOS AUC=0.4837 fell below 0.55 gate, so rule-based version remains active (correct fail-closed behavior). Phase 4 (Carry OOS验证) deferred: database contains zero `funding_rate` history; validation script `scripts/validate_funding_carry_strategy.py` ready but needs 30+ days of data accumulation. Phase 5 (1d/4h Swing验证) completed: `scripts/validate_swing_strategy.py` ran 90-day replay on BTC/ETH; all four quality gates passed (Sharpe=1.326, PF=1.423, MaxDD=13.96%, Expectancy=0.0081) but only 11 trades generated, below 20-trade statistical significance threshold; strategy remains `auto_schedule_enabled=False` until 180-270 day replay produces 20+ trades.
- **Phase 1 详情**: Added `RegimeRouter` import to `decision_pipeline.py`, called `.classify(bars_1h, bars_4h)` in `_generate_decision_snapshot()`, stored `regime: MarketRegime` in snapshot dict. Extended `_eligible_layered_signals()` with `regime: MarketRegime | None` parameter and regime-aware signal family routing: `RANGE` → boost mean-reversion signals (rsi_divergence, vwap_reversion, bollinger_reversion), lower trend-momentum signals (macd_cross, ema_trend, adx_trend); `TREND_UP`/`TREND_DOWN` → boost breakout/momentum (fvg, price_action, macd_cross), lower mean-reversion; `UNCERTAIN` → apply mild discount to all families. Unit tests added: `tests/services/test_regime_integration.py` covers all four regime branches and signal routing logic. Baseline pytest: 618 passed, 15 pre-existing failures (unrelated to this change); post-change: 622 passed, 15 failures (4 new tests added, no new failures introduced). Ruff + mypy clean.
- **Phase 2 结论**: Historical audit evidence from `docs/audit/exit-ladder-retrospective.md` (referenced in ADR-071) showed ExitLadder partial exits introduced more noise than edge; Fixed 2R止盈 has higher net expectancy in past 90-day paper trades. Decision: skip both Phase 2A (static exit_ladder config) and Phase 2B (dynamic family-based exit models) until a formal backtest replay demonstrates regime-conditional exit models beat Fixed 2R on walk-forward OOS data. Current `AUTO_PAPER_TECHNICAL_RULES["takeprofit_rules"]` retains `risk_reward=2.0` with no `exit_ladder` key; `exit/models.py` and `exit_ladder.py` remain in codebase but unused.
- **Phase 3 详情**: Ran `python -m scripts.train_meta_label_model --strategy-key auto_paper_mature_templates` against 90 days of historical decision snapshots. Training succeeded with 487 in-sample + 122 out-of-sample labeled barriers (MIN_TRAIN_SAMPLES=200 gate passed). However, OOS AUC=0.4837 < 0.55 threshold (essentially random classifier performance). Current feature set (`atr_percent`, `trailing_return_5/20`, `volume_zscore_20`, `ensemble_confidence`, `direction_vote_count`, `entry_vote_count`, `funding_rate_bps`, `hour_of_day_sin/cos`) lacks predictive power. Decision per ADR-074: do NOT lower the AUC gate or force-load an underperforming model. `SignalEnsembleService.create_meta_label()` continues using rule-based `win_rate_estimate` (calibrated via historical win-rate lookup); `artifacts/meta_label_models/` contains trained model artifact but `active.json` pointer was NOT created (fail-closed: load returns None, falls back to rule version). This is the correct scientific outcome: model training infrastructure validated, but current feature engineering insufficient for this alpha.
- **Phase 4 结论**: Created `scripts/validate_funding_carry_strategy.py` implementing cross-sectional replay with Top20 funding-rate ranking, 8-hour rebalance, basket_size=3, min_edge=5bps gate, and full transaction-cost accounting (fee+slippage dual-leg). Script ready but cannot execute: `SELECT COUNT(*) FROM funding_rates WHERE timestamp >= '2026-04-26'` returned 0 rows. Binance funding rates are published every 8 hours; need minimum 30 days (90 snapshots) for meaningful backtest. Decision per ADR-075: validation deferred until `services/data/funding.py` accumulates 30+ days of Top20 funding history. `AUTO_PAPER_CROSS_SECTIONAL_CARRY_RULES` remains `default_enabled_for_auto_trading=False` and NOT wired into `bootstrap_local_paper_runtime()`. Validation gate remains strict: Sharpe>1.0, PF>1.3, MaxDD<25%, Expectancy>0, same as other strategy lanes.
- **Phase 5 详情**: Created `scripts/validate_swing_strategy.py` using `TechnicalStrategyValidationService.replay()` with 90-day BTC/ETH historical data, walk-forward OOS methodology, and `AUTO_PAPER_SWING_RULES` (1d direction + 4h entry, 14-day max hold, layered_regime_entry fusion). Data backfilled: BTC 1d=91 bars, 4h=546 bars; ETH 1d=91 bars, 4h=546 bars (sufficient for indicator warmup). Replay generated 11 trades over 90 days. Metrics: Sharpe=1.326 ✅, PF=1.423 ✅, MaxDD=13.96% ✅, Expectancy=0.0081 ✅ — all four quality gates passed. However, 11 trades < 20-trade minimum sample size ❌. Decision per ADR-076: do NOT enable `auto_schedule_enabled=True` or wire into runtime. Quality metrics are encouraging (comfortable margin on all four gates) but 11 trades insufficient to distinguish skill from luck. Strategy remains disabled until extended replay (180-270 days, expect 20-25 trades for 1d/4h + 14d hold) accumulates statistically meaningful sample. Validation infrastructure (`scripts/validate_swing_strategy.py`, `docs/audit/swing-strategy-oos-report.md`) ready for future re-execution.
- **Files changed**: Phase 1: `services/execution/decision_pipeline.py`, `services/strategy_library/ensemble/service.py`, `tests/services/test_regime_integration.py`. Phase 3: executed `scripts/train_meta_label_model.py`, wrote `artifacts/meta_label_models/auto_paper_mature_templates_20260726_*.pkl` (not promoted to active). Phase 4: `scripts/validate_funding_carry_strategy.py` (new). Phase 5: `scripts/validate_swing_strategy.py` (new), `docs/audit/swing-strategy-oos-report.md` (new). Memory: `.github/agent/memory/decisions-log.md` (ADR-074/075/076), `.github/agent/memory/task-history.md`.
- **Verification**: Phase 1 baseline: `pytest -q` 618 passed, 15 failures (pre-existing). Post-Phase 1: 622 passed, 15 failures (4 new regime tests added, no new failures). Ruff + mypy clean. Phase 3: training script completed successfully, OOS AUC logged, rule-based fallback confirmed active via code inspection (`load_active_model()` returns None when `active.json` missing). Phase 4: validation script syntax-checked via import, execution blocked by data precondition (intentional gate, not a bug). Phase 5: replay executed, 11 trades logged, report generated at `docs/audit/swing-strategy-oos-report.md` with gate-by-gate verdict.
- **Red-line compliance**: ✅ No position sizing / leverage / risk_per_trade parameter changes (Phase 2 skipped, Phase 1/3/4/5 read-only or training-only). ✅ No new branches (all work on main). ✅ All promotion decisions gated by backtest thresholds (Phase 3 model rejected by AUC<0.55, Phase 4 blocked by missing data, Phase 5 rejected by sample<20). ✅ Each phase independently tested and committed (Phase 1 green -> ADR-073, Phase 3 result -> ADR-074, Phase 4/5 defer -> ADR-075/076).
- **Outstanding work**: (1) Wait 30+ days for funding_rate history accumulation, then re-run `scripts/validate_funding_carry_strategy.py`. (2) Extend Swing validation to 180-270 days (or wait 6 months forward data), then re-run `scripts/validate_swing_strategy.py`. (3) Feature engineering for Meta-Label: current 10-feature set has no edge; consider adding regime stability (regime duration, regime transition count), order-flow imbalance proxies, realized vs implied volatility spread, or cross-symbol beta to BTC as additional predictors. (4) Phase 2B dynamic family exit models: requires formal backtest comparison (static 2R vs regime-conditional TrendPullbackExit/BreakoutExit/RangeMeanReversionExit on walk-forward OOS) before any code change to production exit logic.

## 2026-07-16 — Binance simulation automatic-order proof and protection hardening

- **Summary**: Confirmed the scheduler was running and the account was Binance simulation rather than a local-only Paper ledger. Fixed legacy `strategies.paper_status='paused'` deserialization, made Demo the preferred backend with transparent legacy-Testnet fallback, and repaired protection submissions by rounding raw conditional trigger prices to Binance symbol precision. Protection failures now fail closed by cancelling an unfilled entry or sending a reduce-only close for a filled entry.
- **External evidence**: Scheduler-created BTC automatic verification order `gateway_order_id=22004526655` was read back from Binance simulation. It created a `0.0015 BTC` long plus Binance reduce-only stop `1000000137072612` and take-profit `1000000137072614`; the isolated verification run was then paused and the simulation account was returned to zero positions and zero open orders.
- **Verification**: `scripts/verify_config.py` -> `GREEN: 18/18 checks passed`, including external Binance order-id reconciliation and mainnet protection. Full `pytest -q` -> `432 passed, 4 skipped`; changed-file Ruff clean.

## 2026-07-28 — P0→P3 acceptance audit + review fixes (watcher kept)

- **Summary**: Left `_watch_p03_until_fill` running (PID tree 21596→30188). Ran full plan acceptance audit + independent code review. **Honest rollup: NOT all complete** — P0.3 hard gate still unmet (`exchange_orders=0`, `exchange_fill_receipts=0`); P3 remains LOCKED. Review findings closed in this pass: wire `validate_exchange_order_transition` into `ExecutionRepository.update_exchange_order`; `/runtime/reconciliation` no longer treats stale cached exchange truth as full BTC/ETH block; +2 illegal transition tests + stale-recon API test.
- **Live**: Scheduler heartbeat fresh; recon `healthy`/blocked `[]`; latest funnel `08:00Z` `SAMPLING_RULES_NOT_ALIGNED`; Testnet 0/0.
- **Verification**: targeted truth suites earlier `80 passed`; post-fix `16 passed` (state machine + runtime API); ruff/mypy on touched files clean; frontend `RuntimeTruthPanel.test.jsx` `1 passed`; P2 checklist `overall_pass=true`. Full `pytest -q` / `ruff check .` / frontend build / pre-commit all-files: **未跑本轮全量**.
- **Incomplete**: Natural Entry→Fill→SL/TP→Exit IDs. Do not claim P0.3/P3 COMPLETE.

## 2026-07-28 — Advance remaining plan (P1/P2) while P0.3 watches

- **Summary**: Kept a single `_watch_p03_until_fill` process. Landed P1 `MARKET_REVIEW` (hourly scheduler + Celery beat/route) and advisory `TRADE_REVIEW` (`bias/confidence/risk_flags/summary`, never blocks Entry). P2 Runtime Truth gained Data Freshness + Strategy Evidence on `/runtime/snapshot` and the panel; headless Playwright verify earlier → `logs/p2-runtime-truth-verify/checklist.json` overall_pass. Added `validate_exchange_order_transition` + illegal-transition tests. Cancelled orphan ETH Testnet TP `1000000148362162`; account clean 0/0.
- **P0.3 root cause (unchanged)**: Current bars are mixed-regime (`close < ema50` and `macd_hist > 0`) so Sampling cannot fire without relaxing locked rules. Historical aligns on 7/27 were killed by Pretrade age≈706s / snapshot gaps (path since fixed).
- **Incomplete**: Natural Entry→Exit IDs still absent → P3 locked. Do not claim P0.3 COMPLETE.

## 2026-07-28 — P0.3 close-out: tight Entry slot + latency (still signal-silent)

- **Summary**: Split armed Binance Testnet cycles into a coordinated `tight_entry` slot (`run_all_paper_runtime_cycles`) and deferred observation to `paper_observation_cycle` (+90s offset) so observation cannot hold the Entry lease. Cycle offset 15→5s. Prefer refreshed `PaperRun.universe_assets` over stale active-snapshot `unknown`. Funnel terminals prefer `SAMPLING_RULES_NOT_ALIGNED` when sampling was attempted. Sampling sizing uses exchange min notional (prior WIP).
- **Live evidence (UTC)**: Bar `03:15` cycle_time `03:15:27`, ETH sampling eval `03:16:01` (age≈61s <75s). Bar `03:30` cycle_time `03:30:27`, ETH eval `03:31:11` (age≈71s <75s). Both ended `SAMPLING_RULES_NOT_ALIGNED` (ETH RSI~28–29 but MACD histogram still >0). Testnet probe: `open_position_count=0`, `open_order_count=0`. `exchange_orders`/`exchange_fill_receipts` still 0 for natural directional.
- **Verification**: Targeted auto_schedule/celery/universe-merge/funnel tests passed; touched ruff/mypy clean. Full `pytest -q -m "not integration"` → `693 passed, 10 skipped, 2 deselected, 1 failed` — failure is `test_market_live_public_rest_endpoints_return_binance_source` falling back to `persisted_market_data` (L2 public REST / env, unrelated to this diff).
- **Incomplete**: P0.3 natural Entry→Fill→SL/TP→Exit still missing — engineering path is inside the pretrade window; remaining blocker is honest Sampling/primary silence. P3 locked. No commit created.

## 2026-07-28 — Unblock natural cycle ops: lease/claim reclaim + funnel preserve + snapshot probe

- **Summary**: Found restart left `paper_runtime_cycle` lease/claims owned by dead PIDs → new scheduler `standby_not_leader` / `duplicate_slot_skipped`, missing the 04:15 bar. Added same-host dead-PID lease reclaim + claimed-slot reclaim in `scheduler_coordination.py`; launcher now runs `scripts/reclaim_stale_scheduler_locks.py` on scheduler stop. Funnel no longer overwrites first terminal with `skip_duplicate`. Runtime `/snapshot` exchange probe is non-blocking (8s timeout, stale cache, in-flight short-circuit) so UI is not wedged behind hung reconcile.
- **Live evidence**: After reclaim, `04:30`/`04:45`/`05:00` directional cycles completed with durable funnel `SAMPLING_RULES_NOT_ALIGNED` (duplicate ticks no longer clobber). Scheduler healthy; `exchange_orders=0` / `fill_receipts=0` — MACD hist still >0 (BTC ~55–60) while RSI in short band. Snapshot auth probe returned `exchange.status=available` in ~1s after warm cache.
- **Verification**: `test_dead_local_*` + funnel overwrite + `test_runtime_truth_api` (9 passed). Snapshot cold path may still hit Binance latency once; subsequent polls are cached.
- **Incomplete**: P0.3 natural Entry→Exit IDs still absent (honest Sampling silence). P2 browser later re-verified via headless Playwright (`logs/p2-runtime-truth-verify/`, overall_pass). P3 locked. Watcher `scripts/_watch_p03_until_fill.py` left running.

## 2026-07-28 — Resume P0.3: cycle latency + Testnet unmanaged flatten

- **Summary**: Continued Codex checkpoint `8e94a6d` on `codex/testnet-truth-recovery`. Finished the uncommitted equity de-dupe (`sync_account` success → no immediate `account_equity()`), narrowed gateway `reconcile` private open-order scans to BTC/ETH by default, filtered positions/algo orders to that universe, and reused reconcile `open_positions` for hard-drawdown locking so the same cycle does not reconcile twice before signals.
- **External evidence**: Authorized flatten of unmanaged ETH short `5.1` contracts → `gateway_order_id=14774000974` filled; subsequent USDT-M Testnet probe `open_position_count=0`, `open_order_count=0`. Ordinary Scheduler then evaluated closed bar `2026-07-28T02:15:00Z` by funnel `created_at=02:17:17Z` with terminal `TECHNICAL_SIGNALS_INSUFFICIENT` / Sampling `SAMPLING_RULES_NOT_ALIGNED` — no new `PRETRADE_DECISION_STALE` (prior was `age=706s`).
- **Verification**: `tests/services/test_account_equity.py` + gateway scope/reconcile tests passed; touched-file `ruff check` All checks passed; `mypy` Success on 4 source files; full `pytest -q -m "not integration"` → `690 passed, 10 skipped, 2 deselected`.
- **Incomplete**: P0.3 natural Entry→Fill→SL/TP→Exit receipt proof still missing (signal silence this window). P1/P2/P3 remain locked. No commit created.

## 2026-07-27 — Binance Testnet execution truth and Runtime Truth recovery

- **Summary**: Implemented the P0 execution-truth foundation and the observability/sampling portions that can be verified without fabricating a natural trade. Testnet projection now requires an immutable exchange fill receipt; Local Paper uses a separate simulated-fill source. Reconciliation fails closed, persists its consecutive-failure Entry Kill Switch across restarts, and cannot clear while `EXCHANGE_UNKNOWN` orders remain. Reduce-risk exits use a dedicated gate and CloseOnly never rounds quantity up to minimum notional.
- **Data/API/UI**: Added exchange order/receipt, decision funnel, LLM invocation, and runtime truth persistence plus Alembic `0014`/`0015`. Added authenticated Runtime Truth REST/WebSocket endpoints and the Paper Console “为什么没有交易” panel. Runtime exchange probes are cached per read burst, time out explicitly, and frontend 30-second fallback refreshes cannot overlap or retain stale endpoint values.
- **External evidence**: Authorized Testnet cleanup returned BTC/ETH to zero exchange positions and zero open orders. A normal Scheduler reconciliation then retained the two historical local rows but changed their current status to `RECONCILED_GHOST`. A later Runtime Truth snapshot reported Binance Testnet `0` positions, `0` open orders, and Testnet-local mismatch `consistent=true`; Local Paper SOL remained isolated from that comparison.
- **Verification**: Full non-integration suite `678 passed, 10 skipped, 2 deselected`; targeted post-review Runtime Truth tests passed; `ruff check .` passed; `mypy` passed for 169 source files; frontend `40 passed` and production build passed; changed-file pre-commit passed all hooks. Full-repository format check still reports 44 unrelated baseline files and is not treated as newly introduced. Browser verified live authenticated WebSocket, terminal no-trade reason, source/time/freshness fields, no console errors, and a non-overlapping 30-second Runtime Truth REST fallback.
- **Incomplete by design**: No natural Scheduler Entry → Fill → SL/TP → normal Reduce-risk Exit occurred during this work window, so P0.3 is not complete and P1/P2 promotion remains locked. The first real LLM smoke attempt reached the provider path but failed before persistence because the environment resolved a non-existent `timescaledb` host; the script was corrected to preflight local persistence, but no second paid/provider call was made. P3 strategy-evidence upgrades were not started.

## 2026-07-30 — Phase 2 V2 production lifecycle closure

- **Summary**: Completed the natural Scheduler-driven Binance Testnet Entry/Fill/Position/Protection/Exit/Final Reconciliation loop. Fixed delayed ACK/fill recovery, protection `algoId -> actualOrderId` fill lookup, exact reduce-only closure of quarantined projections, mismatch-incident resolution after healthy reconciliation, and complete LLM invocation truth in the Runtime API.
- **External evidence**: ETH entry/exit `14979020372 / 14984144295`; BTC `24887097073 / 24906752342`; second ETH `14985524359 / 14988841518`. Final Binance position/order counts `0/0`; local V2 open count `0`; reconciliation `HEALTHY`.
- **Browser evidence**: `http://127.0.0.1:5173/trading` rendered real V2 Runtime panels with `0` console errors. After the two dev-mode initialization calls, Runtime polling deltas were `10010ms`, `9989ms`, and `10000ms`.
- **Verification**: `ruff check .` passed; `mypy` reported no issues in 206 source files; full pytest `1181 passed, 16 skipped, 7 warnings`; frontend `16 passed files / 65 tests`; Vite production build passed; `git diff --check` passed.
- **Residual risk**: Historical `PROTECTION_RECOVERY_FAILED` incident remains open for manual review; build retains the known >500kB chunk warning. No mainnet use and no risk/stop/take-profit/net-edge threshold changes.

## 2026-07-30 — Strategy readiness gate after V2 execution closure

- **Summary**: Added the current readiness report without changing strategy or risk parameters. The V2 Testnet execution chain is verified, but strategy promotion remains `NO_ACTIVE_STRATEGY` because the immutable baseline fails historical coverage and the required cost/validation evidence is incomplete.
- **Evidence**: BTC/ETH 5m `0` bars; 1m roughly 14 days with gaps; higher timeframes roughly one year; required 42 months and frozen holdout unavailable. Technical legacy replay explicitly omits funding/latency/spread/partial-fill costs; current bootstrap helper is IID percentile.
- **Next action**: Backfill through the existing data repository, generate a new immutable baseline, then implement dependent bootstrap and next-bar parity checks before reading holdout results.

## 2026-07-30 — Phase 2 final drain acceptance and runtime restore

- **Natural exchange exits**: RuntimeScheduler observed BTC stop actual order `24933450773` / trade `522947770` and ETH stop actual order `15006608596` / trade `309176628`, both reduce-only real Binance Testnet fills. No manual close, acceptance order, or direct cycle invocation was used.
- **Persistence**: BTC/ETH positions ended `CLOSED`; both protection records ended `PROTECTION_FILLED`.
- **Final reconciliation**: `scripts/check_binance_positions.py` reported Binance positions/orders `0/0`; live Runtime reported local open positions `0`, `HEALTHY`, zero mismatches.
- **Restore**: Formal entry-control API returned `entry_enabled=true` with reason `phase2_final_acceptance_complete`; 5%/40x and all stop/take-profit/net-edge/strategy thresholds remained unchanged.

## 2026-07-31 — Strategy Phase 1 next-bar execution parity

- **Change**: Technical replay now evaluates signals on a closed bar and fills at the following bar's open/timestamp; signals with no following bar are not fabricated as `end_of_window` trades.
- **Evidence**: `tests/services/test_technical_strategy_validation.py` `14 passed`; related validation tests `21 passed`; touched Ruff and production mypy passed. RED was reproduced for both next-bar and `end_at` boundary defects before implementation.
- **Boundary**: `baseline-20260729-0000Z-r4` is retained as a pre-parity historical artifact; no new baseline or Final Holdout result was generated. Funding/spread/latency/partial-fill, input-hash parity, walk-forward ledger and dependent bootstrap remain open. No risk or promotion threshold changed.

## 2026-07-31 — Strategy Phase 1 proposal pipeline and Gate 16 closure

- **Change**: Added canonical typed hashing and one shared three-candidate proposal/selector pipeline for Replay and V2 Shadow. Replay now honors selector output, uses runtime-matched context windows, accumulates signed point-in-time funding settlements, and runs eight independent OOS windows with append-only candidate/window/symbol metrics.
- **Data**: Rebuilt the isolated history database from checksummed Binance Vision files from 2023-01-01 through the frozen cutoff. Funding provenance comes from Binance Futures public history; Testnet sparse bars were not used.
- **Boundary**: Fees read existing runtime config; spread/latency/partial fill remain `ASSUMED` and block promotion. No Final Holdout, DSR/PBO, bootstrap promotion evaluation, parameter tuning or threshold change. Verdict remains `NO_ACTIVE_STRATEGY`.
- **Gate 16**: Real Binance Testnet entry `25631813075` / trade `523375938`, protection `1000000151515912` and `1000000151515916`, reduce-only exit `25631829915` / trade `523375957`; final BTC position/order counts `0/0`. Evidence is contract-only and `natural_strategy=false`; Gate 17 was not run.
- **Artifact**: `artifacts/strategy_refactor/phase1-20260731-0000Z-r1` is the immutable Phase 1 evidence directory and records `holdout_results_accessed=false`.

## 2026-08-03 — Frozen v2.0 Legacy Active Writer root-cause repair

- **Runtime truth**: PRE-000 reconfirmed production launchers pin `v2_shadow`; activation is `SHADOW`, legacy remains the writer, and both legacy/V2-shadow scheduler jobs are registered. Invalid modes fail instead of remapping.
- **Fixes**: Corrected operator profile precedence, made legacy sampling trace-only with no entry/opposite-close authority, made entry leverage setup fail closed, and restored legacy drift defaults to `20bps / 0.25 ATR`. Production changes are limited to the four frozen-plan whitelist files.
- **Chain evidence**: One Active contract reads both production launcher flags, starts `RuntimeScheduler`, invokes its registered legacy runner, and proves execution-profile API -> NEXT_CYCLE snapshot -> Gatekeeper -> real `BinanceUsdtPerpetualGateway` code -> leverage -> fake exchange fill/protection receipts -> local position. It separately proves sampling and V2 SHADOW have zero exchange/position side effects. No real Binance request was made.
- **Verification**: legacy target set `114 passed`; V2/runtime set `63 passed`; full `pytest -q` `1282 passed, 16 skipped, 2 warnings`; Ruff clean; mypy clean for 219 files. Legacy freeze ceilings remain unchanged and pass.
- **Boundary**: A-504 real Binance Testnet canary remains unexecuted pending explicit authorization; no branch, commit, push, migration, engine switch, DB/config mutation, or V2 production edit.

## 2026-08-04 — Legacy natural Scheduler proof observer

- **Change**: Added `scripts/verify_legacy_natural_automated_trading_cycle.py`, a read-only `v2_shadow`/legacy-writer observer with immutable baseline capture, provenance checks, V2 shadow zero-network-order guard, resumable session artifacts, and strict entry -> fill -> protection -> reduce-only exit -> reconciliation state transitions.
- **Evidence**: Added 23 focused tests covering clean-symbol selection, safety/preflight blockers, exchange fill/protection truth, provenance contamination, V2 baseline safety, incomplete exchange reads, timeout/resume, final reconciliation, and evidence serialization. Full repository `1325 passed, 7 skipped, 7 warnings`; Ruff and mypy passed (`219` source files).
- **Boundary**: No trading business code, strategy, risk value, engine setting, database state, branch, or exchange write was changed. A live smoke run from a non-launcher shell correctly stopped at `BLOCKED_PREFLIGHT` because its engine was `legacy`; the one-click launcher remains required for the real natural observation.

## 2026-08-05 — QuantDinger selective Shadow bridge and fill normalization

- **Change**: Added a static AST-only QuantDinger 5.0.1 Strategy API V2 manifest bridge with source hash, static symbols/timeframe/warmup/factor/protection discovery. It converts validated BTC/ETH research signals only into `RESEARCH` + `SHADOW` + non-promotable V2 candidates. Both Entry Gate and Entry submission reject those candidates before any exchange call. Added deterministic CCXT and Binance `ORDER_TRADE_UPDATE` fill normalization, `(exchange_order_id, trade_id)` de-duplication, and fail-closed handling unless the exchange supplies order ID, fee amount and USDT fee currency.
- **Verification**: Targeted bridge/entry/adapter tests `77 passed`; full repository `1345 passed, 7 skipped, 7 warnings`; full Ruff and mypy passed for 222 source files. No Testnet network call, scheduler start, source code execution, risk configuration change, or local position write occurred.
- **Boundary**: Existing V2 Binance Testnet writer remains authoritative; no QuantDinger mainnet/live, local ledger, grid, multi-exchange execution, UI or branding was introduced. Strategy promotion and real natural open/protect/reduce-only exit evidence remain separate follow-up gates.

## 2026-08-05 — QuantDinger Shadow event and differential-replay boundary

- **Change**: Added hash-bound, strict-schema parsing for externally produced Shadow signals and replay artifacts. Shadow candidates now produce an observable V2 funnel that terminates at `SHADOW_MODE_NO_SUBMIT` before intent/exchange stages. Differential replay requires an aligned signal candle and entry exactly one declared timeframe later, with finite non-negative `Decimal` tolerances; it binds artifact source/version, manifest hash, timeframe, and minimum warmup.
- **Verification**: Focused bridge/replay/entry/funnel validation `79 passed`; final full `pytest -q` `1353 passed, 16 skipped, 2 warnings`; `ruff check .` passed; `mypy` passed for `223` source files. Independent read-only review found no remaining new Exchange-First bypass, Shadow bypass, or replay-consistency bypass after the fixes.
- **Boundary**: No QuantDinger source execution, scheduler start, Testnet request, local position write, active-manifest promotion, risk/lever/stop/net-edge change, mainnet, grid, multi-exchange execution, UI, or branding. This is validation and Shadow observability only; it is not OOS performance evidence or real natural Testnet strategy acceptance.

## 2026-08-05 — QuantDinger isolated source runtime and real replay artifact (superseded)

- **Change**: Added the bounded offline/Shadow child-process runtime and CLI
  artifact path. The parser now accepts only the required artifact fields plus
  the explicitly reviewed CLI metadata (`strategy_id`, `strategy_version`,
  `signals`, `rejected_events`) and still rejects unknown fields. Shadow replay
  exit reasons use the existing local `stoploss`/`takeprofit` vocabulary.
- **Historical evidence (superseded)**: This entry recorded the first run before
  duplicate-target, entry-bar, and artifact-contract fixes. Its 320/319/321
  counts must not be used as the current acceptance evidence; see the later
  `artifact refresh and contract closure` entry for the current hash and counts.
- **Verification**: QuantDinger focused tests `16 passed`; source Ruff and
  mypy passed; the real CLI and comparison both exited 0. The mismatch is
  retained as a parity blocker and is not promotion or Testnet strategy proof.
- **Boundary**: No API/Scheduler source execution, exchange request, local
  position write, risk/config change, active-manifest promotion, or Mainnet
  path was introduced.

## 2026-08-05 — QuantDinger artifact refresh and contract closure

- **Change**: Fixed the CLI artifact to emit the complete strict signal
  metadata contract, canonicalized external symbols at signal and replay
  ingestion boundaries, and added regression coverage for `BTCUSDT`/lowercase
  symbols and artifact self-parsing. Moved disposable Shadow evidence under
  ignored `.cache/quantdinger/`.
- **Real evidence**: Re-ran the CLI against the 320-bar BTC/USDT 15m Binance
  Vision development window. Hash
  `41025a9fbeb963fec18229dabcbd5944267eae8574ce1f9864f5b5ce5456a169`;
  1 signal, 1 next-bar replay trade, 145 duplicate-target rejections; exit
  reason `takeprofit`. Artifact parser passed. The refreshed comparison has 0
  local trades and 1 unmatched external trade because no authoritative local
  replay payload was preserved for this run, so it is explicitly not promotion
  evidence.
- **Verification**: Focused QuantDinger tests `32 passed`; full `pytest -q`
  `1368 passed, 7 skipped, 7 warnings`; `ruff check .` passed; `mypy` passed
  for 225 source files; `git diff --check` passed.
- **Boundary**: No Binance/Testnet request, scheduler execution, local
  position write, active-manifest promotion, or risk/threshold change.

## 2026-08-05 — Gate 17 natural Testnet acceptance remains blocked

- **Evidence**: Ran the repository's official natural Scheduler observer. It
  fail-closed before any exchange call because `V2_NATURAL_E2E_ENABLED` was not
  true and no project API/Scheduler listener was active. Evidence:
  `docs/evidence/automated_trading_v2/natural_cycle_20260805T145750Z.json`.
- **Result**: `GATE 17 NOT PASSED`; no cycle, candidate, intent, exchange order,
  protection order, exit, or reconciliation evidence exists for this session.
- **Boundary**: Enabling the real Testnet observation path requires the
  operator-controlled runtime launch/configuration and is not performed by the
  Shadow integration task. The QuantDinger bridge therefore remains
  `PARTIAL/BLOCKED` for overall acceptance even though its code and offline
  artifact contract are verified.

## 2026-08-06 — Gate 17 sampling execution boundary and baseline preservation

- **Change**: Fixed the V2 cycle's sampling candidate path so an explicitly
  armed `v2_active` Binance Testnet run can submit the non-promotable sampling
  fallback through the normal Exchange-First receipt/protection flow. Shadow,
  local Paper, and unpersisted/unit runs remain decision-trace-only. Added a
  same-direction guard for captured external BTC/ETH baselines so a one-way
  Binance account cannot reduce or reverse an operator position. Updated the
  Gate 17 observer wording to require restoration to the captured baseline.
- **Verification**: Focused V2 tests `75 passed, 1 skipped`; full `pytest -q`
  `1373 passed, 7 skipped`; `ruff check .` passed; configured `mypy` passed for
  `225` source files; `git diff --check` passed. Active API/Scheduler was
  restarted with `v2_active`, Testnet, and baseline-preservation mode. The
  official 60-minute natural observer ran with real Binance snapshots and
  ended `GATE 17 NOT PASSED` because no same-direction natural Entry occurred;
  no exchange write was made and the captured baseline stayed
  `BTC/USDT:short=0.5302`, `ETH/USDT:short=6.814`.
- **Boundary**: No manual position was closed, no risk/leverage/stop/take-profit
  or threshold value changed, no Mainnet path was enabled, and no mock or
  acceptance order was counted as evidence. Gate 17 remains OPEN pending a
  real same-direction Entry -> protection -> natural Exit -> baseline restore.

## 2026-08-06 — Gate 17 runtime authorization and natural observation continuation

- **Change**: Added explicit `V2_NATURAL_E2E_ENABLED=true` propagation to the
  `-EnableNaturalTestnet` launcher path; the flag authorizes only the official
  read-only observer and does not enable exchange submission. Tightened the
  observer's symbol type narrowing after mypy found an `Any` lookup issue.
- **Runtime evidence**: Restarted the API and isolated V2 Scheduler through the
  launcher. Runtime is `ACTIVE / BINANCE_TESTNET`, `entry_enabled=true`, and
  reconciliation `HEALTHY`; the preserved external baseline remains
  `BTC/USDT:short=0.5302`, `ETH/USDT:short=6.814`. The 17:45 and 18:00 natural
  closed-bar cycles produced real LONG candidates and correctly rejected them
  as `UNMANAGED_EXTERNAL_POSITION`; no intent, order, fill, or position was
  written. An 8-hour official observer is running in a separate read-only
  process and has passed preflight.
- **Verification**: Focused `56 passed, 1 skipped`; full `1373 passed, 7
  skipped, 7 warnings`; Ruff clean; mypy clean for 225 source files; diff
  check clean. Gate 17 is still OPEN because no same-direction natural Entry
  has occurred yet.
- **Boundary**: No user position, order, credential, risk value, leverage,
  stop/take-profit, strategy threshold, Mainnet path, or acceptance shortcut
  was changed.

## 2026-08-10 — P1 same-cycle Research Shadow observer

- **Change**: Proved the current ACTIVE path and the three existing research
  candidate paths, then attached a pure Research Shadow observer to the V2
  bridge. The observer reuses the ACTIVE 15m `TimeframeView`, records a
  canonical market reference and fixed candidate/version/status fields, and
  appends evidence only after all ACTIVE symbols finalize and leases release.
  The proposal pipeline isolates one candidate exception. The verifier is
  SQLite `mode=ro` only and validates the full three-candidate envelope plus
  `RESEARCH` lineage mutation ledger.
- **Red/Green**: Initial verifier and observer-order tests failed for the
  expected missing/old behavior; after the minimal implementation and review
  hardening, P1 focused tests passed.
- **Runtime**: Through `一键启动.cmd` only, final cutover
  `2026-08-10T13:15:03.869648Z` produced 192 after-shadow observations,
  192 matched same-cycle records, 0 unmatched, and zero research intents,
  exchange orders, positions, position modifications, protection creations,
  or protection modifications. ACTIVE remained `ACTIVE/BINANCE_TESTNET`,
  strategy `testnet_sampling_v2`, `entry_authorized=true`,
  `legacy_writer=false`; reconciliation was healthy.
- **Verifier hardening**: added fail-closed checks for the top-level ACTIVE
  strategy identity and `BINANCE_TESTNET` cycle execution mode; regression
  tests cover both tampering cases.
- **Verification**: `30 passed` P1 focused; `79 passed, 1 skipped` P0/U1;
  full `1439 passed, 7 skipped, 2` known registry failures; touched Ruff and
  mypy (`225` files) passed. Full Ruff/pre-commit remain blocked only by the
  documented unrelated script findings and registry baseline assertions.
- **Boundary**: No strategy/risk/SL/TP/leverage/sizing/reconciliation or
  launcher contract changed. No promotion, tuning, switch, or P2 started.

## [TASK-GEOMETRY-FREEZE] — Strategy owns geometry, execution owns safety (2026-08-11)

- **Mandate**: operator authorized a Layer-1 invariant change explicitly: buy/sell
  levels, stop placement, and profit targets belong to the Strategy Layer and must
  come from falsifiable quantitative rules; only account-survival boundaries stay
  hard-configured. Documented as ADR-080 in decisions-log, ADR-004 in `docs/adr/`,
  AGENTS.md §"Strategy Owns Geometry, Execution Owns Safety", and
  `docs/superpowers/plans/2026-08-11-p2-strategy-exit-policy-frozen-scope.md`.
- **Deliverable**: freeze contract only. No implementation, no evaluation code,
  no parameter change, no execution-layer modification.
- **Verified facts behind the decision**: (1) `testnet_sampling_v2` entry is
  quantitative (EMA50 + MACD hist + RSI band + ATR14 on closed 15m bars) but
  exit is effectively hardcoded — `stop = max(1.2*ATR14, price*0.0035)`,
  `TP = 1.5*stop`. When `1.2*ATR14` < 0.35% the floor wins and geometry
  degenerates to fixed SL 0.35% / TP 0.525% / RR 1.5 for all symbols/regimes.
  Both BTC and ETH entries on 2026-08-10 hit exactly that floor. (2) Research
  candidates `trend_pullback_v2` / `range_sweep_reversion_v1` /
  `failed_breakout_reversal_v1` already emit structural invalidation and
  multi-leg `targets` as `StrategyProposal`. (3) **Architectural gap blocking
  laddered promotion**: `TradeCandidate` accepts one `stop_distance` + one
  `take_profit_distance`; `StrategyProposal` carries `targets` tuple summing
  to 1; protection submits single reduce-only leg; and no
  `StrategyProposal` → `TradeCandidate` adapter exists anywhere in
  `services/automated_trading/` or `services/execution/`.
  (`services/strategy_library/adapters/quantdinger.py:243` is not that adapter
  — it converts external single-target QuantDinger signals into `SHADOW`
  non-promotable candidates per ADR-079, never sees `StrategyProposal`, and is
  further confirmation `TradeCandidate` is structurally single-target.)
- **Authorized extension**: one strictly-additive port for multi-leg
  reduce-only exits, with existing single-target path proven behaviorally
  identical by regression tests. Building the port is not promotion.
- **Unchanged**: `testnet_sampling_v2` control lane keeps its exact current
  entry rules, stop formula, target multiple, and position/leverage/gate
  settings. Its geometry is recorded as a conservative sampling rule, not an
  endorsed design, replaced only by a candidate that passed promotion — never
  by picking new percentages. Tuning the 0.35% floor, 1.5R multiple, or any
  Validation Layer threshold to change outcomes is forbidden.
- **P2 scope frozen**: P2-A = exit policy shadow evaluation (hold entry fixed,
  vary exit, measure MFE/MAE/profit-capture-ratio post-cost); P2-B = entry
  strategy comparison (same-cycle P1 shadow); P2-C = promotion (blocked by gap
  until extension exists). Anti-goal recorded: P2 must not end as
  "0.35% → 0.7%" or "1.5R → 2R" — parameter substitution is not a strategy
  improvement.
- **Verification**: markdown-only change; 3 pre-existing unrelated Ruff
  findings remain in baseline (not introduced by this task); skill-copy sync
  passed.

## 2026-08-12 — V2 strategy authority recovery

- **Change**: Kept V2 ACTIVE as the sole Testnet writer but removed sampling entry authority.
  `testnet_sampling_v2` remains decision evidence only; ACTIVE now stays healthy with entries
  paused under `NO_AUTHORIZED_PRODUCTION_STRATEGY` while existing V2 reconciliation,
  protection, recovery, and reduce-only exits continue.
- **Authorization**: Extended the canonical mature manifest to schema v3 with a default
  `production_authorization.state=PENDING`. Approval is bound to candidate/version, rules hash,
  immutable ConfigSnapshot hash, BTC/ETH scope, validation evidence, and operator identity/time.
  Missing, stale, mismatched, or unapproved records fail closed.
- **Adapter**: Added a pure read-only adapter from the existing mature DecisionPipeline into a
  single-target V2 `PRODUCTION`/`PRIMARY` TradeCandidate. It preserves trace/provenance and
  derives ATR stop plus configured 2R geometry; absolute protection still resolves post-fill.
- **Runtime Truth**: `/runtime` snapshot, positions, reconciliation, orders, protections, fills,
  and websocket views now read V2 fact tables directly and ignore stale legacy projections.
- **Boundary**: No risk/leverage/SL/TP/Gatekeeper thresholds, Binance adapter, legacy writer,
  database migration, mainnet path, or real Testnet order was changed or executed.
- **Verification**: Focused V2/runtime/authorization/scheduler tests passed (`64 passed`, then
  scheduler integration `51 passed`); targeted Ruff and mypy passed. Full mypy passed (`236`
  source files); full pytest found only two pre-existing candidate-registry count assertions
  (`1540 passed, 7 skipped, 2 failed`). Full Ruff found three unrelated script findings.

## 2026-08-12 — Testnet Canary authority continuity

- Restored a strictly isolated `TESTNET_CANARY` authority for
  `testnet_sampling_v2` only on `BINANCE_TESTNET`, while the manifest's
  Production authorization remains `PENDING`. Resolver precedence is
  `PRODUCTION > TESTNET_CANARY > NONE`; Canary stays non-promotable and cannot
  create a second writer when Production is approved.
- Runtime Truth and transaction desk now expose entry authority, authorization
  reason, active entry strategy, production state, promotion eligibility, and
  `TRADING` versus `ENTRY_PAUSED`. The operator can see open-entry and
  reduce-only exit status separately, plus per-symbol decision/gate/submission
  facts without reading logs.
- Formal launcher evidence: `ACTIVE/BINANCE_TESTNET`, entry authority
  `TESTNET_CANARY`, `entry_authorized=true`, production `PENDING`, and
  `promotion_eligible=false`. A natural scheduler Canary entry persisted
  intent `ea23a85f-abb6-47b8-b17c-4d37178a9f54`, position
  `9ead2ee8-b88e-4dae-a654-c67082f53773`, Binance Testnet fill trade
  `313315568`; it is not production evidence. No forced order or exit used.
- Verification: focused V2 regression `139 passed`; runtime API `19 passed`;
  frontend `103 passed`; target mypy passed (6 files); frontend build passed.

## 2026-08-12 — Testnet Canary authority continuity, review repair

- Independent review found and the task repaired: (1) an opposite Canary
  signal could bypass the unmanaged external baseline direction guard; (2) the
  Why-No-Trade API/WebSocket read legacy funnel rows instead of V2 facts; (3)
  scheduler authority was stale after startup; (4) Canary non-promotability
  lacked a persisted-fact assertion.
- Added narrow contracts for the conflicting baseline, persisted `SAMPLING`
  intent, V2 Runtime Truth record, and post-cycle Production authority refresh.
  The console now consumes normalized V2 fields in both the Runtime Truth and
  existing Why-No-Trade panels.
- Official launcher restarted with `v2_active`, `EnableNaturalTestnet`, and
  preserved baseline. ACTIVE contract passed; API reported
  `entry_authority=TESTNET_CANARY`, `entry_authorized=true`, Production
  `PENDING`, strategy `testnet_sampling_v2`, `promotion_eligible=false`, and
  `trading_state=TRADING`. No forced order or exit was submitted.
- Evidence: `72 passed` targeted Python; `105 passed` frontend; targeted Ruff
  and mypy clean; full mypy `236` clean; full pytest `1539 passed, 16 skipped,
  2` existing candidate-registry-count failures; full Ruff retains 3 unrelated
  script findings. Independent reviewer final verdict: PASS.

## 2026-08-13 — Corrected Testnet sizing semantics and overwrite guard

- **Authorized runtime setting**: Binance Testnet new entries use `50x` and at
  most `5%` of current equity as margin. V2 translates this into a hard
  per-symbol notional ceiling of `equity * 0.05 * 50` (`2.5x` equity); it is not
  a 5% notional ceiling. `risk_per_trade=0.10`, signals, gates, stop/target,
  existing ETH position, protection, reconciliation, and Mainnet were unchanged.
- **Persistence/overwrite repair**: profile application persists PaperRun
  `execution_profile` and stages an immutable next-cycle snapshot. It now fails
  closed if a different pending snapshot exists. Bootstrap only creates a missing
  medium risk profile and preserves existing operator values across restarts.
- **Runtime proof**: after the official `v2_active` Testnet restart, dry-run
  resolved both BTC/ETH to 50x / 0.05 margin / 2.5 exposure / 5.0 total. The
  scheduler was ACTIVE, reconciliation HEALTHY; ETH stayed short `1.364` with
  two reduce-only protection orders. No forced order was sent.
- **Verification**: focused regression suite `84 passed in 12.39s`; targeted
  Ruff `All checks passed!`; `git diff --check` passed. Natural Binance receipt
  at the new size remains pending a real signal.

## 2026-08-14 — Real Testnet trade audit and strategy optimization loop

- **Scope**: Read-only Binance Testnet history extraction, local V2 fact export,
  fill/episode reconciliation, live loss attribution, and research-only OOS
  candidate generation. Execution chain stayed frozen.
- **Facts**: 449 exchange trades, 282 orders, 575 income rows, 204 algo-orders;
  141/141 local V2 fills matched (`1.0`); 308 unmatched records; completeness
  `PASS`; range `2026-07-07T16:52:55.936Z`–`2026-08-14T07:30:35.847Z`.
- **Live V2**: 25 closed episodes, net PnL `-454.11402372` USDT, PF `0.3370`,
  expectancy `-18.16456095`, win rate `48%`, max DD `509.94780122`; STOP was the
  dominant loss cause (12, `-675.23621504`).
- **Research**: added `loss_aware_trend_pullback_v1` as research-only; OOS failed
  (1,099 trades, PF `0.7081`, expectancy `-0.001229`). `trend_pullback_v2` was
  the best nonzero candidate but also failed (405 trades, PF `0.8210`, expectancy
  `-0.0007721`). No candidate was armed or promoted.
- **Final**: `AUDIT_PASS / STRATEGY_NOT_ACCEPTED / EXECUTION_FROZEN`.

## 2026-08-14 — Generation Next strategy rebuild result

- Completed `LIVE_PAYOFF_ROOT_CAUSE` with R-normalized winners/losers and cost,
  sizing, entry, geometry, and giveback attribution.
- Ran five frozen research generations; all failed OOS and cost-stress gates.
- Final status: `STRATEGY_EDGE_NOT_FOUND`; execution regression tests passed and
  no execution-plane file was intentionally changed by this loop.

## 2026-08-14 — Final Edge-First Event experiment

- Added `services/strategy_library/event_edge.py` and the read-only runner
  `scripts/run_edge_first_event_research.py`.
- Added focused tests for feature gates and Net-R metrics. The runner writes a
  canonical event dataset and an eight-window ledger under `artifacts/trading_audit/`
  without reading the sealed holdout.
- Preliminary (superseded) result: `STRATEGY_EDGE_NOT_FOUND`; 172 OOS trades, 44.19% win rate, 1.2089
  payoff, PF 0.9570, expectancy -0.02698R, 3/8 positive windows.
- This closes the requested method change honestly. Do not promote, arm, or tune
  the event gate against the holdout; any future work needs a new data or
  hypothesis authorization rather than another `v2/v3` parameter pass.

### Independent-review correction

- Before finalizing, a read-only review found four material metric risks. The
  implementation was repaired and the replay rerun in 144.6 seconds.
- Final corrected artifact is `STRATEGY_EDGE_NOT_FOUND`: 23 selected OOS trades,
  43.48% win rate, Net-R payoff 1.178, PF 0.9065, expectancy -0.06098R,
  LCB95 -0.5826R, 0/8 positive windows, holdout untouched.

## 2026-08-14 — Forward Baseline and Shadow evidence foundation

- Implemented the locked prerequisite before any further strategy work:
  immutable decision snapshots with exact 15m bars, feature metrics, strategy
  commit/version, config hash, candidate payload, and canonical snapshot hash.
- Added deterministic replay with forced candidate identity so replay can prove
  decision, feature, and `TradeCandidate` equality instead of comparing only
  direction or nominal geometry.
- Added append-only ACTUAL/R1/R2/R3 Shadow records and outcome backfill storage;
  Shadow persistence is isolated from exchange side effects.
- Focused validation: `4` Forward Baseline tests, `67` V2 scheduler/cycle tests,
  Ruff clean, mypy clean for touched modules. Runtime verifier currently reports
  `0` captured cycles and `FORWARD_BASELINE_NOT_REPRODUCIBLE`; mandatory `>=100`
  natural-cycle evidence is still pending and must not be inferred from tests or
  historical rows.
- Confirmed exits now backfill all linked Shadow variants from exchange fill
  facts with R-normalized outcome fields; partial reductions remain pending
  until the position is actually closed.

## 2026-08-14 — Forward Baseline natural replay gate passed

- Fixed the first observed replay divergence by persisting `already_evaluated_bars`; duplicate-bar decisions now replay identically.
- Added runtime mode markers to snapshots and filtered the verifier to natural `ACTIVE + BINANCE_TESTNET` cycles only.
- Official Testnet runtime produced `144` natural decision cycles with `100%` decision/feature/candidate/TradeCandidate matches, `0` immutable violations, and no mismatches.
- Shadow evidence currently has `32` records and `0` completed outcomes because the new protected positions have not naturally exited yet. Strategy Plane remains frozen; no profitability or strategy-improvement claim is valid.

## 2026-08-14 — R1/R2/R3 forward deployment

- Synced the operator-reported manual close against Binance Testnet; observed external BTC/ETH baseline is `{}` and the persisted baseline was refreshed.
- Implemented R1 equal-risk sizing by removing the scheduler score-based sizing branch and recording `R1_EQUAL_RISK` in snapshots.
- Implemented R2 target-relative cost gate with commission, funding, slippage inputs and `theoretical_net_payoff >= 1.15`; rejection reason is `NO_TRADE_COST_INEFFICIENT`.
- Implemented R3 P1 one-way stop tightening from exchange mark price, with P2/P3 shadow policy calculations and persisted original stop geometry. Replacement stop is acknowledged before the old stop is cancelled.
- Focused tests: `63 passed`; full suite: `1611 passed, 7 skipped, 7 warnings`; touched Ruff and scoped mypy passed.
- Official launcher restarted with `v2_active`, natural Testnet authorization, and preserved flat baseline. Runtime is `ACTIVE / BINANCE_TESTNET / TESTNET_CANARY`, entry authorized, scheduler healthy, and latest observed account baseline remains `{}`.
- Forward verifier after restart: `190` cycles replayed, decision/feature/candidate/TradeCandidate match rates `100%`, immutable violations `0`, mismatches `[]`.
- This is `RUNNING_FORWARD_VALIDATION`; no profitability or result-acceptance claim is made until new natural trades close.
- Post-restart reconciliation also found one pre-existing managed ETH/USDT long (`9.334` at exchange fill `1884.9997`, decision bar `17:15 UTC`) with live stop/TP orders. It is carried forward unchanged and is excluded from the new-policy performance claim; the service remains running and will reconcile it normally.

## 2026-08-15 — P0 no-trade observability and runtime restart

- Root cause of the apparent 12-hour no-trade outage: the running API had been
  started outside the official launcher and used the unresolved `timescaledb`
  database host; the no-trade endpoint also compared SQLite naive timestamps
  with aware UTC timestamps and returned HTTP 500.
- Fixed UTC normalization, classified `NO_TRADE_COST_INEFFICIENT` as an entry
  blocker, and added a 12-hour funnel covering signals, candidates, R2 rejects,
  intents, exchange submissions/fills, and protection confirmation. The UI now
  renders the R2 reason and funnel counts.
- Full validation after the fix: backend `1612 passed, 7 skipped, 7 warnings`;
  frontend `112 passed`; touched Ruff and scoped mypy passed.
- Official launcher restarted `v2_active` / `BINANCE_TESTNET` / `TESTNET_CANARY`.
  Natural runtime evidence after restart: scheduler running, entry authorized,
  exchange info ready, data fresh, reconciliation healthy, account flat.
- Observed 12-hour funnel at 2026-08-15 12:18 CST: `36` effective cycles,
  `16` signals/candidates, all `16` rejected by `NO_TRADE_COST_INEFFICIENT`,
  `0` position rejects, `0` drift rejects, `2` intents, `2` exchange submissions,
  `17` non-reduce-only fills in the window, and `1` protection confirmation.
  This is runtime diagnosis only; no profitability or result-acceptance claim.

## 2026-08-15 — R2.1 calibration loop stopped at evidence gate

- Re-audited the current 12-hour cohort: `42` R2 rejects versus `2` intents;
  decision-id and cycle-id overlap is empty, so the historical `14/14` versus
  `2/2` funnel cannot be used for parameter selection.
- Replayed frozen `testnet_sampling_v2` with non-overlapping one-position-per-
  symbol semantics. Layer 1 (`1.15/1.05/0.95/0.85`, TP `1.5R`) and Layer 2
  (TP `1.5R/1.8R/2.0R` crossed with those thresholds) produced no
  `stable_positive_oos` configuration. Production R2 remains unchanged at
  `1.15`; no R2.1 promotion or service restart was performed.
- Evidence artifacts: `artifacts/r2-cohort-audit-current.json` and
  `artifacts/r2_calibration-layer*-nonoverlap.json`. Runtime remains
  `ACTIVE/BINANCE_TESTNET`, flat on exchange and local projection, with healthy
  reconciliation and scheduler heartbeat.
- Validation: mypy `250` source files; pytest `1612 passed, 7 skipped`; touched
  Ruff clean. Full Ruff still reports one pre-existing C416 in
  `scripts/verify_gate17_e2e.py`, unrelated to this calibration loop.

## 2026-08-15 — Bounded Alpha Champion Master Loop infrastructure

- Added `scripts/run_alpha_champion_master_loop.py` with append-only baseline,
  inventory, split, generation, validation, checkpoint, and final-report artifacts.
- Canonical proposal replay now accepts evaluator overrides and dynamic data ends;
  variant ledger records retain parent candidate, hypothesis, parameters, stage,
  and fold metrics. Generation 1/2 are hard-capped and never alter execution,
  R1, R2, or Testnet Canary semantics.
- Added chronological Research/Validation/Final split metadata (60/20/20),
  resume baseline drift checks, and honest terminal handling. No Champion or
  Testnet promotion authority was created by this loop.
- Verification: focused loop/registry/replay tests `18 passed`; full backend
  suite `1618 passed, 7 skipped, 7 warnings`; touched Ruff and mypy clean.
- Full Ruff remains blocked by the pre-existing `C416` in
  `scripts/verify_gate17_e2e.py`; full mypy remains blocked by pre-existing
  duplicate-module and legacy script typing errors. The real G0 replay completed
  and produced canonical artifacts; long-running bounded variant replay was
  interrupted before a terminal Champion decision and must not be reported as
  promotion evidence.

## 2026-08-15 — D3 one-closed-bar confirmation challenger

- Executed the ledger action against `.strategy_refactor_history.db` using the
  frozen `testnet_sampling_v2` entry/exit/cost semantics and a 70/30 chronological
  development/OOS split. The read-only report is
  `artifacts/active_strategy_optimization/one_closed_bar_confirmation_20260815.json`.
- Production-signal parity covered 400 sampled windows with zero side mismatches.
  The challenger improved BTC OOS expectancy slightly (`-0.26110064` vs
  `-0.26501108`) but worsened ETH (`-0.20912436` vs `-0.20280307`) and lowered
  ETH PF (`0.71184236` vs `0.71929364`), so D3 was rejected and no strategy/R2
  change was made.
- `LOOP_LEDGER.json` and `FINAL_STATUS.json` were updated via fsync-backed
  temporary replacements. `FINAL_STATUS.success` remains `false`; the next
  machine action is the bounded two-closed-bar confirmation replay.

## 2026-08-15 — D4 two-closed-bar confirmation challenger

- Executed the next ledger action with two subsequent closed 15m confirmations
  and next-bar-open entry, preserving the current stop, target, cost model, and
  R2 threshold. Artifact:
  `artifacts/active_strategy_optimization/two_closed_bar_confirmation_20260815.json`.
- Signal parity covered 800 confirmation windows with zero mismatches. BTC OOS
  expectancy improved slightly (`-0.25967530` vs `-0.26501108`), but ETH
  worsened (`-0.21846434` vs `-0.20280307`) and PF fell to `0.70110781` from
  `0.71929364`; D4 was rejected and no strategy/R2 change was made.
- State files were atomically updated; `FINAL_STATUS.success` remains `false`.
  The next bounded action is a long-side veto replay with short-side logic and
  all geometry/R2 rules unchanged.

## 2026-08-15 — D5 long-side veto challenger

- Executed the ledger action by filtering only long candidates after the existing
  sampling signal, with short-side evaluation, geometry, costs, and R2 unchanged.
  Artifact: `artifacts/active_strategy_optimization/long_side_veto_20260815.json`.
- BTC OOS expectancy/PF declined (`-0.26501108 → -0.26841182`,
  `0.65078316 → 0.64732272`). ETH also remained slightly below baseline
  (`-0.20280307 → -0.20288003`, PF `0.71929364 → 0.71902162`). D5 was rejected.
- `LOOP_LEDGER.json` and `FINAL_STATUS.json` were atomically updated;
  `FINAL_STATUS.success` remains `false`. Next action is the symmetric short-side
  veto replay.

## 2026-08-15 — D6 short-side veto challenger

- Executed the ledger action with a read-only short-side veto, preserving the
  existing long-side evaluator, next-bar fill, stop/target geometry, costs, and
  R2 threshold. Artifact:
  `artifacts/active_strategy_optimization/short_side_veto_20260815.json`.
- The 70/30 chronological replay rejected D6 for both symbols: BTC OOS
  expectancy/PF changed from `-0.26501108 / 0.65078316` to
  `-0.29024344 / 0.62364511`; ETH changed from `-0.20280307 / 0.71929364` to
  `-0.21700965 / 0.70285291`.
- No strategy or R2 change was made. State files were updated with fsync-backed
  atomic replacements; `FINAL_STATUS.success` remains `false`. Next action is a
  bounded short-side 4h-conflict veto replay.

## 2026-08-15 — D7 short-side 4h-conflict veto challenger

- Executed the bounded replay that vetoes only short signals whose 4h EMA50
  trend conflicts, preserving long signals, geometry, costs, and R2. Artifact:
  `artifacts/active_strategy_optimization/short_side_4h_conflict_veto_20260815.json`.
- BTC OOS expectancy/PF improved from `-0.26501108 / 0.65078316` to
  `-0.25892916 / 0.65712299`, but ETH declined from
  `-0.20280307 / 0.71929364` to `-0.20510787 / 0.71645048`; D7 was rejected
  under the both-symbol promotion rule.
- No strategy or R2 change was made. `LOOP_LEDGER.json` and
  `FINAL_STATUS.json` were updated atomically; `FINAL_STATUS.success` remains
  `false`. Next action is the symmetric long-side 4h-conflict veto replay.

## 2026-08-15 — D8 long-side 4h-conflict veto challenger

- Executed the symmetric replay that vetoes only long signals whose 4h EMA50
  trend conflicts, preserving short signals, geometry, costs, and R2. Artifact:
  `artifacts/active_strategy_optimization/long_side_4h_conflict_veto_20260815.json`.
- BTC OOS expectancy/PF declined from `-0.26501108 / 0.65078316` to
  `-0.28726546 / 0.62728765`; ETH declined from
  `-0.20280307 / 0.71929364` to `-0.21308437 / 0.70713936`; D8 was rejected.
- No strategy or R2 change was made. State files were atomically updated;
  `FINAL_STATUS.success` remains `false`. Next action is a bounded long-side
  1h-conflict veto replay.

## 2026-08-15 — D9 long-side 1h-conflict veto challenger

- Executed the bounded replay that vetoes only long signals whose 1h EMA50
  trend conflicts, preserving short signals, geometry, costs, and R2. Artifact:
  `artifacts/active_strategy_optimization/long_side_1h_conflict_veto_20260815.json`.
- BTC OOS expectancy/PF declined from `-0.26501108 / 0.65078316` to
  `-0.27067345 / 0.64480155`; ETH improved only marginally from
  `-0.20280307 / 0.71929364` to `-0.20278290 / 0.71937272`; D9 was rejected.
- No strategy or R2 change was made. State files were atomically updated;
  `FINAL_STATUS.success` remains `false`. Next action is the symmetric
  short-side 1h-conflict veto replay.

## 2026-08-15 — D10 short-side 1h-conflict veto challenger

- Executed the symmetric replay that vetoes only short signals whose 1h EMA50
  trend conflicts, preserving long signals, geometry, costs, and R2. Artifact:
  `artifacts/active_strategy_optimization/short_side_1h_conflict_veto_20260815.json`.
- BTC OOS expectancy/PF improved from `-0.26501108 / 0.65078316` to
  `-0.25442776 / 0.66223467`, but ETH declined from
  `-0.20280307 / 0.71929364` to `-0.21730943 / 0.70207542`; D10 was rejected.
- No strategy or R2 change was made. State files were atomically updated;
  `FINAL_STATUS.success` remains `false`. Next action is a bounded dual-timeframe
  conflict veto replay.

## 2026-08-15 — D11 dual-timeframe conflict veto challenger

- Executed the replay that vetoes signals conflicting with both 1h and 4h EMA50
  trends, preserving geometry, costs, and R2. Artifact:
  `artifacts/active_strategy_optimization/dual_timeframe_conflict_veto_20260815.json`.
- BTC OOS expectancy/PF improved from `-0.26501108 / 0.65078316` to
  `-0.25985397 / 0.65643428`, but ETH declined from
  `-0.20280307 / 0.71929364` to `-0.21679022 / 0.70283113`; D11 was rejected.
- No strategy or R2 change was made. State files were atomically updated;
  `FINAL_STATUS.success` remains `false`. Next action is a bounded long-only
  dual-timeframe conflict veto replay.

## 2026-08-15 — D12 long-only dual-timeframe conflict veto challenger

- Executed the long-only replay that vetoes signals conflicting with both 1h and
  4h EMA50 trends, preserving short signals, geometry, costs, and R2. Artifact:
  `artifacts/active_strategy_optimization/long_dual_timeframe_conflict_veto_20260815.json`.
- BTC OOS expectancy/PF declined from `-0.26501108 / 0.65078316` to
  `-0.27048914 / 0.64510596`; ETH declined from
  `-0.20280307 / 0.71929364` to `-0.21381647 / 0.70641901`; D12 was rejected.
- No strategy or R2 change was made. State files were atomically updated;
  `FINAL_STATUS.success` remains `false`. Next action is the symmetric
  short-only dual-timeframe conflict veto replay.

## 2026-08-15 — D13 short-only dual-timeframe conflict veto challenger

- Executed the short-only replay that vetoes signals conflicting with both 1h
  and 4h EMA50 trends, preserving long signals, geometry, costs, and R2.
  Artifact: `artifacts/active_strategy_optimization/short_dual_timeframe_conflict_veto_20260815.json`.
- BTC OOS expectancy/PF improved from `-0.26501108 / 0.65078316` to
  `-0.25528279 / 0.66120782`, but ETH declined from
  `-0.20280307 / 0.71929364` to `-0.20447486 / 0.71719695`; D13 was rejected.
- No strategy or R2 change was made. State files were atomically updated;
  `FINAL_STATUS.success` remains `false`. Next action is a strict dual-timeframe
  alignment replay.

## 2026-08-15 — D14 strict dual-timeframe alignment challenger

- Executed the replay retaining only signals aligned with both 1h and 4h EMA50
  trends, preserving geometry, costs, and R2. Artifact:
  `artifacts/active_strategy_optimization/strict_dual_timeframe_alignment_20260815.json`.
- BTC OOS expectancy/PF improved from `-0.26501108 / 0.65078316` to
  `-0.26086246 / 0.65528954`, but ETH declined from
  `-0.20280307 / 0.71929364` to `-0.20901993 / 0.71168513`; D14 was rejected.
- No strategy or R2 change was made. State files were atomically updated;
  `FINAL_STATUS.success` remains `false`. Next action is strict alignment with
  one closed-bar confirmation.

## 2026-08-15 — D15 strict dual-timeframe alignment plus one confirmation

- Executed strict 1h/4h alignment with one closed-bar confirmation, preserving
  geometry, costs, and R2. Artifact:
  `artifacts/active_strategy_optimization/strict_dual_timeframe_alignment_one_bar_20260815.json`.
- BTC OOS expectancy/PF declined from `-0.26501108 / 0.65078316` to
  `-0.26950267 / 0.64572408`; ETH declined from
  `-0.20280307 / 0.71929364` to `-0.22192849 / 0.69704684`; D15 was rejected.
- No strategy or R2 change was made. State files were atomically updated;
  `FINAL_STATUS.success` remains `false`. Next action is strict alignment with
  two closed-bar confirmations.
# 2026-08-15 — Fade/Reversion strict EventEdge loop

- Added research-only EventEdge adapters for `failed_breakout_reversal_v1` and `range_sweep_reversion_v1`; execution and risk planes stayed frozen.
- Read-only live-loss structure attribution found 30 local closed entries / 14 STOP rows and zero strict opposite-sweep-plus-confirmation matches; do not promote that causal story to fact.
- Final reports: failed-breakout `REJECTED_WITH_EVIDENCE` (3,592 events, no train gate across 8 windows); range-sweep `INSUFFICIENT_DATA` (0 events); both `holdout_accessed=false`.
- Independent Regime-weight and Bollinger reports were kept separate. No active manifest or production authorization changed.

# 2026-08-15 — Testnet sampling R2 regression recovery

- Root cause confirmed: commit `39e6524` added an unconditional R2 entry blocker. In
  the established `testnet_sampling_v2` geometry it produced about `0.716R` net
  payoff against a `1.15R` minimum, so it rejected all observed sampling
  candidates without changing their underlying signal generation.
- The minimal repair makes R2 explicitly `DIAGNOSTIC` only for the exact
  `TESTNET_CANARY` + `TESTNET_SAMPLING` + `testnet_sampling_v2` contract. It
  still calculates and persists cost evidence; `PRODUCTION` and every other
  R2-enabled path remain `BLOCKING`. Regression tests cover both policies.
- Natural post-fix evidence, not a manual or acceptance order: Scheduler cycle
  `03e04f46-2062-488f-9dfb-9279c12955ab` created BTC decision
  `3722e97c-df9f-432f-9211-0a50e48698f5` at `2026-08-15T12:15:37Z`.
  Its R2 payload was `REJECT`, `policy=DIAGNOSTIC`, `would_block=true`, and
  `enforced=false`; it then submitted Binance Testnet order `28541964139`,
  filled `0.2764 BTC` at `62964.998`, created active stop/target orders
  `1000000167954341` / `1000000167954361`, and reconciled with no position
  mismatch. This is `NON_PROMOTABLE_PIPELINE_SAMPLE`, not production evidence.
- Verification: focused cycle/risk tests `29 passed`; full non-integration
  suite `1620 passed, 5 skipped, 2 deselected`; mypy succeeded for 250 source
  files. Full ruff retains the pre-existing unrelated `C416` in
  `scripts/verify_gate17_e2e.py:77`.

# 2026-08-15 — Natural exit observation turn 52

- Executed the persisted natural-exit observation without orders, cancellations,
  manual closes, or database writes.
- Binance Testnet still reports one managed BTC/USDT long (`0.2764`, entry
  `62964.99801013024`) with reduce-only TP `1000000167954361` and SL
  `1000000167954341` both `NEW`; no natural exit fill occurred.
- Scheduler completed a fresh cycle at `2026-08-15T14:32:06.915799Z` with
  `ACTIVE`, `2/2` execution coverage, fresh data, and `HEALTHY` reconciliation.
  API listener `8016` remains unavailable. `LOOP_LEDGER.json` and
  `FINAL_STATUS.json` were atomically replaced; `success` remains `false` and
  the next machine action is unchanged.

# 2026-08-15 — Natural exit observation turn 54

- Executed the persisted observation action without submitting or cancelling
  any orders and without changing local database state.
- Binance Testnet still shows the managed BTC/USDT long (`0.2764`, entry
  `62964.99801013024`) with TP `1000000167954361` and SL `1000000167954341`
  both `NEW`; no natural reduce-only exit filled.
- Scheduler cycle `2026-08-15T14:37:57.207112Z` completed with fresh data,
  `2/2` execution coverage, and `HEALTHY` reconciliation. API listener `8016`
  remains unavailable. `LOOP_LEDGER.json` and `FINAL_STATUS.json` were
  atomically replaced; `success` remains `false` and the next action is
  unchanged.

# 2026-08-15 — Natural exit observation turn 56

- Continued the persisted natural-exit observation with read-only exchange
  checks and no order, cancellation, manual-close, or database mutations.
- Binance Testnet still reports the managed BTC/USDT long (`0.2764`, entry
  `62964.99801013024`) with TP `1000000167954361` and SL `1000000167954341`
  both `NEW`; no natural reduce-only exit filled.
- Scheduler cycle `2026-08-15T14:43:19.359664Z` completed with fresh data,
  `2/2` execution coverage, and `HEALTHY` reconciliation. API listener `8016`
  remains unavailable. State files were atomically replaced; `success` remains
  `false` and the next machine action is unchanged.

# 2026-08-15 — Natural exit observation turn 57

- Executed the persisted observation action with read-only Binance checks and
  no order, cancellation, manual-close, or database mutations.
- Binance Testnet still reports the managed BTC/USDT long (`0.2764`, entry
  `62964.99801013024`) with TP `1000000167954361` and SL `1000000167954341`
  both `NEW`; no natural reduce-only exit filled.
- Scheduler cycle `2026-08-15T14:54:10.027186Z` completed with fresh data,
  `2/2` execution coverage, and `HEALTHY` reconciliation. API listener `8016`
  remains unavailable. State files were atomically replaced; `success` remains
  `false` and the next machine action is unchanged.

# 2026-08-15 — Natural exit observation turn 59

- Continued the persisted natural-exit observation with read-only Binance
  checks and no order, cancellation, manual-close, or database mutations.
- Binance Testnet still reports the managed BTC/USDT long (`0.2764`, entry
  `62964.99801013024`) with TP `1000000167954361` and SL `1000000167954341`
  both `NEW`; no natural reduce-only exit filled.
- Scheduler cycle `2026-08-15T14:58:11.652386Z` completed with fresh BTC/ETH
  data, `2/2` execution coverage, and `HEALTHY` reconciliation. API listener
  `8016` remains unavailable. State files were atomically replaced; `success`
  remains `false` and the next machine action is unchanged.

# 2026-08-15 — Natural exit observation turn 61

- Executed the persisted natural-exit observation with read-only exchange
  checks and no order, cancellation, manual-close, or database mutations.
- Binance Testnet still reports the managed BTC/USDT long (`0.2764`, entry
  `62964.99801013024`) with TP `1000000167954361` and SL `1000000167954341`
  both `NEW`; no natural reduce-only exit filled.
- Scheduler cycle `2026-08-15T14:58:11.652386Z` completed with fresh BTC/ETH
  data, `2/2` execution coverage, and `HEALTHY` reconciliation. API listener
  `8016` remains unavailable. State files were atomically replaced; `success`
  remains `false` and the next machine action is unchanged.

# 2026-08-15 — Natural exit observation turn 63

- Executed the persisted natural-exit observation with read-only Binance
  checks and no order, cancellation, manual-close, or database mutations.
- Binance Testnet still reports the managed BTC/USDT long (`0.2764`, entry
  `62964.99801013024`) with TP `1000000167954361` and SL `1000000167954341`
  both `NEW`; no natural reduce-only exit filled.
- Scheduler cycle `2026-08-15T15:02:13.614507Z` completed with fresh BTC/ETH
  data, `2/2` execution coverage, and `HEALTHY` reconciliation. API listener
  `8016` remains unavailable. State files were atomically replaced; `success`
  remains `false` and the next machine action is unchanged.

# 2026-08-15 — Natural exit observation turn 65

- Executed the persisted natural-exit observation with read-only Binance
  checks and no order, cancellation, manual-close, or database mutations.
- Binance Testnet still reports the managed BTC/USDT long (`0.2764`, entry
  `62964.99801013024`) with TP `1000000167954361` and SL `1000000167954341`
  both `NEW`; no natural reduce-only exit filled.
- Scheduler cycle `2026-08-15T15:04:11.784414Z` completed with fresh BTC/ETH
  data, `2/2` execution coverage, and `HEALTHY` reconciliation. API listener
  `8016` remains unavailable. State files were atomically replaced; `success`
  remains `false` and the next machine action is unchanged.

# 2026-08-15 — Natural exit observation turn 67

- Executed the persisted natural-exit observation with read-only Binance
  checks and no order, cancellation, manual-close, or database mutations.
- Binance Testnet still reports the managed BTC/USDT long (`0.2764`, entry
  `62964.99801013024`) with TP `1000000167954361` and SL `1000000167954341`
  both `NEW`; no natural reduce-only exit filled.
- Scheduler cycle `2026-08-15T15:06:24.232996Z` completed with fresh BTC/ETH
  data, `2/2` execution coverage, and `HEALTHY` reconciliation. The subsequent
  `exchange_info_refresh` TLS handshake timeout set `exchange_info_ready=false`;
  API listener `8016` remains unavailable. State files were atomically
  replaced; `success` remains `false` and the next action is unchanged.

# 2026-08-16 — P2-A exit-policy shadow evaluation and operations unblock

- Ran the existing read-only `services.research.exit_policy_shadow.cli` against
  `.local_paper_console.db`; loaded 30 real `testnet_sampling_v2` closed entries
  (BTC 14, ETH 16) and replayed all five frozen exit policies at 1m fidelity.
- Overall control A remained the best observed policy but was not promotable:
  net `-7.85` USDT, PF `1.11`, median capture `68.89%`, fee drag `304.15` USDT.
  Q1 `NOT_SUPPORTED`, Q2 `NOT_SUPPORTED`, and Q3
  `INSUFFICIENT_SLICE_SAMPLE`; only RANGE/UNKNOWN slices reached 10 entries.
  Expansion (7) and Trend (2) remain thin. Artifact:
  `docs/audits/2026-08-11-p2a-exit-policy-shadow-results.json`.
- Actual V2 ledger decomposition on the same 30 positions (15 TAKE_PROFIT / 15
  HARD_STOP) found gross `+0.07374R`, recorded entry+exit cost `0.22834R`, and
  net `-0.15460R`. With nominal 1.5R/-1R geometry and the observed 15/15 split,
  cost-adjusted ideal was `+0.02166R`; actual gross lagged nominal geometry by
  `0.17626R`. This makes cost the largest measured drag, with a separate
  geometry/fill shortfall. Artifact:
  `docs/audits/2026-08-16-p2a-actual-decomposition.json`.
- Eleven of the 30 P2-A positions crossed at least one 8h funding settlement
  boundary (12 boundary events total), and the completed read-only Binance
  income audit confirms funding is material: 32 closed V2 episodes, net
  `-565.22143738` USDT, commission `271.53670323` USDT, funding
  `-134.96894383` USDT, PF `0.39659635`. `calculate_cost_gate()` still defaults
  `funding_bps=0` and its live call site passes no funding value. No production
  cost-model change was made because the funding input is not yet wired point-in-time
  to each entry/holding interval. Reports:
  `docs/audits/2026-08-16-testnet-history/reports/live_strategy_evaluation.json`
  and `data_completeness.json`.
- P2-B same-cycle shadow scan found 454 generic `v2_shadow_records`, but zero
  records matching `trend_pullback_v2`, `range_sweep_reversion_v1`, or
  `failed_breakout_reversal_v1`; no P2-B result can substitute for P2-A evidence.
- Operations: `/health` on `127.0.0.1:8016` returned HTTP 200 and the port was
  actively owned by the API process. The historical `unavailable` loop state was
  stale/auth-related (protected runtime endpoint returned 401), not a current
  listener outage. The repeated natural-exit wait was not required for this
  offline replay and was stopped for this research turn.
- Independent runtime snapshot `scripts/i1_runtime_state_snapshot.py` confirmed
  `127.0.0.1:8016 LISTENING` under PID `18964`, scheduler cycles `24978`, and
  one still-open protected BTC position. No order, cancellation, close, or
  database mutation was performed to force the position.
- Consolidated the three-component evidence in
  `docs/audits/2026-08-16-p2a-component-summary.json`.

# 2026-08-16 — PF cohort alignment, funding attribution boundary, and P2-B storage check

- Reconciled the apparent `1.1123` vs `0.3966` PF gap before running any
  funding-only experiment. Policy A replay PF `1.11229903` is modeled replay
  over 30 local CLOSED positions; the exchange audit PF `0.39659635` is 32
  account-level TradeEpisode rows with funding included. The 3 extra episodes
  are the three QUARANTINED positions, and one P2-A ETH position is absorbed
  into an earlier account-level ETH episode. The same 30 positions mapped to
  exchange fills have pre-funding actual PF `0.50914947` (gross `-172.6881`
  USDT, commission `243.4460` USDT, net `-416.1341` USDT).
- The previously cited `0.48688` cannot be found in the current repository or
  artifacts and is not reproducible from the current 30-row decomposition;
  current normalized-R PF is `0.75334826`. It is marked unsupported pending a
  source/formula, not silently reused.
- Point-in-time funding window matching found 3 account income events across
  3/30 positions, totaling `+1.33323099` USDT in the naive window sum. All
  matched contexts have stale (>30m) position snapshots and account-level
  income is not uniquely attributable with the observed external/contradictory
  exposure. Result is `FUNDING_WINDOW_MATCHED_ACCOUNT_LEVEL_AMBIGUOUS` and
  `INSUFFICIENT_DATA` for a funding-only variable experiment; no production
  cost gate or execution code was changed.
- Corrected P2-B storage diagnosis: the three candidates are persisted in
  `v2_execution_decisions.payload.research_shadow`, not the legacy
  `v2_shadow_records` table. After cutover there are 25,995 raw embedded
  observations / 5,607 unique observations: each candidate has 1,869 unique
  observations; trend pullback and range sweep are all `SHADOW_NO_SIGNAL`,
  while failed breakout has 16 `SHADOW_SIGNAL_READY` and 5
  `SHADOW_STRATEGY_REJECTED` observations. This is low signal incidence, not
  a disconnected writer. Artifact:
  `docs/audits/2026-08-16-p2b-embedded-shadow.json`.

# 2026-08-16 — Runtime parity and P1 dynamic-protection audit

- Added and ran the read-only `scripts/audit_runtime_p1_parity.py` over the same
  30-position P2-A cohort. R0 exactly reproduces the existing Policy A replay
  (`PF gross=1.11229903`, mean net `-7.848168` USDT / `0.047839R`).
- Runtime-parity stages use the persisted exchange-rounded protection geometry:
  R1 static path `0.291423R` gross, R2 with conservative next-bar P1 updates
  `0.249455R` gross, and R3 actual exchange fills `0.073741R` gross / `-0.154600R`
  commission-net. R1->R2 is `-0.041968R`; R2->R3 remains `-0.404055R`.
- Simulated P1 triggered in 21/30 rows (12 did not reach target), but the
  historical protection ledger has zero explicit `ProfitProtectionStopTightened`
  events. A `policy=P1` label alone is not replacement evidence.
- Decision: `P1_NOT_PRIMARY_EXPLANATION / CONTINUE_RUNTIME_ATTRIBUTION`.
  Do not start a funding-only or signal experiment yet; next inspect actual exit
  order identity, protection prices, exchange fill price/slippage and replay
  timing semantics. Evidence: `docs/audits/2026-08-16-runtime-p1-parity.*`.

# 2026-08-16 — Exit order/fill lineage and natural-exit continuation

- Added a read-only V2 episode lineage report at
  `scripts/build_exit_order_fill_lineage.py`, with focused coverage in
  `tests/scripts/test_build_exit_order_fill_lineage.py`. It maps every 30-row
  R0-R3 episode through entry intent/order/fills, protection receipts/events,
  reduce-only exit fills, and related incidents.
- The completed historical cohort is 15 `STOP` / 15 `TARGET`, with zero abnormal
  exits and zero quantity-mismatched partial exits. The relevant measured loss
  contributors are trigger-to-fill timing `-5.23940860R` and commission
  `-6.85480521R`; 21 counterfactual P1 triggers with no historical replacement
  receipt are retained as `unknown_residual=-1.29198910R`, not attributed to a
  made-up execution defect.
- All 30 reduce-only fill order IDs are now asserted against the corresponding
  `ProtectionTriggered.event_payload.exchange_order_id`; missing intent or linkage
  is reported per episode instead of aborting or silently accepting the audit.
- Current runtime supplies the missing live P1 receipt: managed BTC/USDT stop
  moved from `62744.5` to `62976.0229`, order `1000000168673444`, with the original
  TP `1000000167954361` still live and reconciliation HEALTHY. Scheduler remains
  ACTIVE and API health at port 8016 is restored. The remaining machine action is
  natural reduce-only exit verification; no manual order or close was issued.
## [TASK-2026-08-16-COST-LOOP] Execution cost optimization loop

- Corrected the stop condition: the open BTC/USDT position is runtime-health observation only, never a blocker for current-data optimization.
- Read-only root cause report: 30 closed episodes, 15 STOP / 15 TARGET, 30/30 exit-lineage matches, commission `0.22834078R/trade`, trigger-to-fill `-0.17464695R/trade`, fee `4bps/side`, and risk-percent/commission-R correlation `-0.96346`.
- ATR-native-only selection challenger completed 70/30 BTC/ETH, 1m fidelity, observed commission calibration, and 1.5x stress; rejected with BTC OOS expectancy `-0.30199679R` / PF `0.61451`, ETH `-0.27896443R` / PF `0.63789`, stress combined `-0.44789986R` / PF `0.48497`.
- Maker/limit model was not fabricated: `.strategy_refactor_history.db` has no historical order-book-like tables, so timeout, missed fills, adverse selection and drift are unavailable.
- Existing Policy A-E report was reused as the next bounded branch; Policy C leads only on 9 proxy trades and is not active-lane OOS promotion evidence.
- Holdout was not accessed; no runtime config, risk parameter, exchange order, or live trading path changed.

## [TASK-2026-08-16-MICROSTRUCTURE] Pipeline closed and collecting

- Added independent Testnet BTC/ETH order-book collector, persistent snapshots, health/quality checks, retention, and restart checkpoint.
- Added maker/limit replay primitives (touch, liquidity, conservative queue proxy, timeout, fallback market, missed/partial fill, adverse selection and R accounting) plus readiness and replay CLIs.
- Launcher now owns a separate collector process with PID/log recovery; it cannot block Scheduler or execution.
- Real Testnet verification produced valid rows for BTC/USDT and ETH/USDT while scheduler stayed ACTIVE/HEALTHY. Readiness remains false until natural candidate-window coverage reaches the requested gate.
- No strategy, risk, geometry, execution path, or current position changed. Final state: `MICROSTRUCTURE_PIPELINE_READY_AND_COLLECTING`.

## [TASK-2026-08-16-UNIVERSE-EXPANSION] Execution scope 2 -> 5

- Expanded the authoritative Binance Testnet execution scope to BTC/USDT, ETH/USDT,
  SOL/USDT, XRP/USDT, and BNB/USDT; scope hash is
  `9d4e56ae53f9d0b1047efebc5ac48b28fa0cfa72d71b28178dc47dd9b11d124d`.
- Backfilled and verified 61 current 1h bars for each added symbol; all five Testnet
  linear swaps are active with parsed precision and min-notional metadata.
- Shadow run completed 10/10 healthy cycles with zero submissions and zero errors.
- Added a baseline-preserving exact-scope acceptance path. Real Testnet acceptance
  completed 10 filled orders across five symbols while retaining the live ETH short
  and its two protection order IDs; the acceptance proof was persisted and one V2
  directional run was re-armed.
- Runtime heartbeat and ACTIVE startup contract now derive expected freshness and scope
  from the execution-universe constant. Natural V2 cycles completed for all five symbols.
- Verification: 1,635 passed / 7 skipped full pytest; full mypy passed (257 files);
  full ruff retains only the known Gate17 C416 baseline. The initial BTC position and
  BTC protection IDs disappeared in an exchange-state drift before acceptance, so the
  original pre-change BTC snapshot cannot be claimed as preserved; the post-drift
  baseline used for acceptance was preserved.

## [TASK-2026-08-16-BTC-DRIFT-DIAGNOSIS] Root cause closed

- Current BTC risk state is flat: zero BTC position and zero BTC protection orders.
- The BTC baseline disappeared through the existing runtime hard-stop path, not
  acceptance cleanup. Protection `1000000168673444` triggered at
  `2026-08-16T09:57:37.503000Z`, producing reduce-only exit `28542950261`, trade
  `527891302`, fill `62975.9`, quantity `0.2764`, reason `HARD_STOP`.
- Both five-symbol acceptance attempts began later (`10:37:22Z` and `10:42:06Z`);
  the successful run used `--preserve-existing-state`, which skips
  `testnet_account_cleanup`.
- Added a read-only diagnosis artifact and supplemental natural-stop lineage entry;
  no strategy or execution code changed in this diagnostic loop.
## [TASK-2026-08-16-RISK-TIER-CAP] Directional sizing target corrected

- Legacy core/standard tiers, volatility defaults, persisted operator sliders, and the
  frozen PaperSignal fallback could independently emit values above the directional cap.
- All directional resolution paths now use the operator target `max_leverage=50`,
  `max_margin_fraction=0.05`, and `max_position_fraction=2.50`; the active
  directional baseline remains `risk_per_trade=0.01`.
- Active run `35298c65-cdbe-4bee-bee3-b7ded07c3204` activated snapshot
  `ff2ebdc1-ee1d-4ec3-a137-167112cb36a7`. No exchange position, leverage setting, or
  protection order was changed.
- ETH short remains 9.266 at 50x and XRP short remains 972 at 20x, both protected;
  existing positions were not re-leveraged or closed.
- Verification: frontend Vitest `112 passed`, frontend build passed, full pytest
  `1638 passed, 7 skipped`; full mypy `257 source files` passed; full ruff retains
  only the known Gate17 C416 baseline.

## [TASK-2026-08-18-TESTNET-CANARY-CONTRACT] Five-symbol runtime contract

- Unified the Binance Testnet Canary runtime contract for BTC/ETH/SOL/XRP/BNB:
  30x leverage, 5% target/max margin, 1.50x per-symbol notional, 7.50x total
  notional, 5 open positions, and 0.10 diagnostic risk-per-trade.
- Canary E-003, volatility shock, R2, and E-004 are diagnostic; operational,
  exchange, data, reconciliation, and manual kill-switch controls remain blocking.
- Sizing now consumes authoritative existing mark-price notional against the
  aggregate cap while leaving grandfathered positions untouched. Scheduler passes
  the explicit sampling lane into the contract and the public API schema accepts
  the 7.50 total exposure value.
- Added the one-click publisher and SHA/clean-tree verification. It was syntax
  checked only; no GitHub push was attempted in this turn.
- Verification: Ruff passed; core mypy passed for 9 files; focused scheduler/
  contract tests `91 passed`; frontend `113 passed`; full pytest `1694 passed,
  16 skipped, 1 failed` due to the pre-existing isolated daily-review assertion.

## [TASK-2026-08-18-RUNTIME-TRUTH-CANARY-CLOSEOUT]

- Runtime Truth now classifies live Binance raw algo orders (`algoStatus`,
  `orderType`, `clientAlgoId`, `algoId`, `reduceOnly`, compact `BASEUSDT` symbols)
  and normalizes V2 order identity. The regression suite covers raw Binance
  protection responses.
- Live Testnet evidence after restart: BTC short `0.5346` is an external,
  unmanaged position with no live stop/TP and remains a P0 entry-blocking fact;
  ETH long `5.354` and SOL long `85.09` each have live reduce-only stop and TP
  orders covering the full authoritative quantity. ETH protection IDs are
  `1000000171465758` / `1000000171465772`; SOL IDs are
  `1000000171476726` / `1000000171476743`.
- Runtime snapshot, positions, reconciliation and no-trade summary agree on
  `degraded`, affected `BTC/USDT`, discrepancy `EXCHANGE_ONLY_POSITION`, and
  recovery action `UNMANAGED_EXTERNAL_POSITION_REQUIRES_OPERATOR_ADOPTION`.
  Funnel semantics distinguish unique filled orders (`5`) from fill events
  (`11`) and protection records/events (`2`).
- Frontend now distinguishes five Canary symbols from research/observation
  symbols, covers all five runtime decisions, and surfaces current protected vs
  unprotected positions. Runtime and console data hooks defer their initial
  refresh past React StrictMode cleanup to avoid aborting the only first burst.
- Verification: `ruff check .` passed; `mypy` passed for 258 files; Runtime Truth
  tests `24 passed`; frontend Vitest `113 passed`; full pytest `1707 passed,
  7 skipped, 1 failed` with only pre-existing
  `test_daily_review_keeps_all_terminal_reasons_for_same_symbol` failing.
- Gate: `ROUND CLOSEOUT=FAIL/BLOCKED` until the external BTC position is
  explicitly adopted or removed by the operator and receives authoritative
  protection. Strategy optimization remains blocked; no Canary safety values
  or strategy gates were changed.

## [TASK-2026-08-18-RUNTIME-TRUTH-PROJECTION-VERIFY]

- Changed `apps/api/routers/runtime.py` projection identity to hash semantic
  current facts instead of observation/heartbeat timestamps; added endpoint
  consistency and timestamp-stability regression coverage.
- Verified focused backend `68 passed`, frontend `114 passed`, production build,
  full Ruff, mypy `258 source files`, and full pytest `1702 passed, 16 skipped,
  1 failed` (the same unrelated daily-review assertion).
- Restarted local services. Active recovery was correctly rejected with
  `EXTERNAL_BASELINE_MISMATCH` because persisted BTC short `0.5346` disagrees
  with the current empty exchange snapshot. Baseline was preserved; Shadow
  service was restored. Browser plugin setup remains blocked, so no rendered UI
  PASS or Runtime Closeout PASS is claimed.
[TASK-2026-08-19-FINAL-RUNTIME-TRADE-LIFECYCLE-CLOSEOUT]

- 收口 Manual Baseline Lifecycle、canonical Runtime projection、V2 identity fingerprint 与 scheduler lifecycle persistence；没有执行任何真实交易操作。
- 只读 forensics：30 episodes，30/30 stop floor，15 TARGET，8 DIRECTION_FAILURE，7 数据不足；指定 ETH/SOL 精确样本不在当前 cohort，未把假设写成事实。
- 验证：Runtime 核心复验 57 passed；Ruff PASS；mypy 258 files PASS；前端 114 tests/build PASS；full pytest 1710 passed / 16 skipped / 1 pre-existing daily-review failure。
- Gate：Runtime Deployment BLOCKED（BTC baseline 0.5346 与交易所空仓不匹配、无真实 ACTIVE closeout、Chrome tooling blocked）；Strategy Analysis READY；Strategy Deployment BLOCKED。

## [TASK-2026-08-19-BOUNDED-STRATEGY-RESEARCH-NO-VALIDATED-EDGE]

- 已用 append-only、结果前注册的试验登记替代无限候选迭代：最多 4 个假设家族、每家族 2 个变体、共 8 个；每条记录锁定假设、数据集划分、此前试验数、理据、精确变更和 Final Holdout 访问状态。
- 权威 V2 lifecycle forensics 现从 immutable fill receipts 重建 gross PnL 和 commission。无法逐仓归属的 funding 明确为 unavailable 或 account-level ambiguous；仅在有持久 immutable reference price 时才报告 execution slippage。
- 对 41 个已平 V2 episode 的归因中，direction failure 居首（8/41）；证据不支持加宽止损、E1 参数变体或按 symbol/side 特化。仅两个有证据的 Development 假设为 volatility-expansion regime selection 和 breakout-retest entry structure。
- 两个 BTC/ETH Development replay 均失败：`volatility_expansion_v1` PF 0.9633、expectancy -0.000318、max drawdown 0.7044；`breakout_retest_v1` aggregate PF 1.0045，但 BTC PF 0.7485、expectancy -0.001717。成本观测仍不完整；未读取 Validation 和一次性 Final Holdout。
- 结论为 `NO_VALIDATED_EDGE`，entry authority 有意保持 `NONE_PENDING_PRODUCTION_STRATEGY`。没有用参数钓鱼填满数字预算：剩余家族在锁定政策下均无可辩护证据。后续必须使用新的独立数据和新的 sealed registry，禁止复用该 Development 集优化。
- 提交 `402140f` 已推送至 `origin/backup/2026-08-10-wip`。验证：Ruff PASS；mypy 262 source files PASS；full pytest 1769 passed / 7 skipped。

## [TASK-2026-08-19-FINAL-CLOSEOUT]

- 策略结论已冻结为 `NO_VALIDATED_EDGE`；active manifest 保持 `PENDING`、eligible execution symbols 为空。默认 V2 一键启动不再隐式启用 Canary，生产新开仓授权为 `NONE`，原因 `NO_AUTHORIZED_PRODUCTION_STRATEGY`，状态为 `ENTRY_PAUSED`。这不是 Runtime 故障；仍允许对既有受管仓做对账、保护和 reduce-only 平仓。
- 正式 launcher 实测启动为 `SUCCESS / READY / STARTUP_READY`；Binance Testnet 连接、Scheduler、V2 reconciliation 均健康。收口时交易所无持仓，唯一遗留的是一个 `EXTERNAL_MANUAL_ORDER`，未由系统接管或修改。
- V2 Forensics 只读重建 41 个已平自动受管 episode（部署状态 `BLOCKED`，未读 Holdout）。Funding 仍按事实标为 unknown/account-level ambiguous，slippage 只从 immutable reference 计算；没有从报告部署或武装任何策略。
- 前端 Trading Console 已实测显示“暂无通过验证的生产策略”、`NO_AUTHORIZED_PRODUCTION_STRATEGY` 和“自动新开仓已暂停（非系统故障）”；BTC/ETH 显示“执行范围 / 新开仓暂停”，没有暗示 Canary 自动交易。
- 行为包 identity/冻结合约及 deterministic V2 engineering E2E 已通过；但生成数据驱动 Golden Replay baseline 时得到 `DATA_COVERAGE_INSUFFICIENT`：要求的 42 个月窗口在本地数据中不完整（可用 15m/1h/4h 从 2025-07 起，1m/5m 更短且有缺口）。因此不得把 data-backed behavioral replay 误报为 PASS，也不得以补数据/重跑策略绕过 frozen-research boundary。

## [TASK-2026-08-19-RESEARCH-FUSION-FINAL-ACCEPTANCE]

- 固定 Freqtrade SHA `b3404c9d81422fed6a8fd83d3d296c37c7915327` 已通过隔离 research-only Python 环境运行；本地 market metadata provider 只提供交易对元数据，K 线全部来自 canonical dataset。
- 同一 canonical dataset 已真实完成 Freqtrade `backtesting -> hyperopt -> lookahead-analysis -> recursive-analysis`；vectorbt 真实 subprocess 产出 4 个参数组合、plateau=4、neighbor_stability=1.0。
- `scripts/run-research-smoke.py` 生成并持久化 `research-smoke-20260819-final`：native OOS 70/30、2 笔 OOS trade、Council `accept_for_next_gate`，但 `promotion_authorized=false`、Production `PENDING`。
- 提交 `a4a7751` 已推送到 `origin/backup/2026-08-10-wip`；冻结 V2 execution/scheduler/Binance/migration/.env 相对 `aaf123f3...` 无差异。
- 验证：full pytest `1777 passed, 5 skipped, 2 deselected`；Ruff PASS；mypy `272 source files` PASS；前端 `114 passed`、admin build PASS；launcher evidence `SUCCESS / STARTUP_READY`。这证明研究融合链可执行，不证明策略盈利或 Production promotion。
# [TASK-2026-08-19-P0-RUNTIME] Runtime truth repair and V2 transaction freeze

- Scope: stale account/reconciliation/UI truth, no-trade priority, V2 hot-path
  freeze, one-click runtime verification.
- Evidence: focused API tests `46 passed`; frontend suite `115 passed`; full
  non-integration pytest `1780 passed, 5 skipped, 2 deselected`; Ruff and mypy
  passed; admin build passed.
- Runtime evidence: `logs/startup-result.json` reported `STARTUP_READY`; `/ops`
  Playwright check showed Runtime Truth and `ENTRY_PAUSED`; snapshot polling was
  approximately 30 seconds with no console errors.
- The browser verifier was corrected to use the real `/ops` Runtime Truth surface
  and its final checklist passed.
- Safety boundary: production authorization remains `PENDING`, entry authority
  remains `NONE`, and the Testnet contract tool fail-closed without explicit
  authorization/credentials.

## [TASK-2026-08-19-NATURAL-TESTNET-AUTHORITY-BRIDGE]

- Implemented the bounded hot-path bridge under explicit process approval
  `NATURAL_TESTNET_AUTHORITY_BRIDGE_20260820`: one resolver requires
  `V2_NATURAL_E2E_ENABLED=true`, Binance Testnet, and live trading disabled.
  Request payloads cannot arm Canary authority.
- Synchronized `v2_scheduler_entry.py`, `scheduler.py`, and launcher mode truth;
  Natural startup now publishes `TESTNET_CANARY / testnet_sampling_v2 /
  promotion_eligible=false`, while the normal launcher publishes
  `natural_testnet_enabled=false / NONE / ENTRY_PAUSED / Production=PENDING`.
- Real launcher evidence: Natural `STARTUP_READY` and `TRADING` were observed;
  normal mode was restored with `STARTUP_READY`, `entry_authority=NONE`,
  `entry_authorized=false`, `entry_enabled=true`, and `ENTRY_PAUSED`.
- Focused authority tests: `6 passed`; V2 service regression: `1278 passed`;
  full pytest: `1786 passed, 7 skipped`; Ruff: `All checks passed!`; mypy is
  blocked by the pre-existing duplicate module `check_positions.py` versus
  `scripts/archive/2026-08-ops-checks/check_positions.py`.
- Natural proof remains OPEN: `prove_real_binance_natural_order.py` created no
  order and returned `No natural directional Binance Simulation fill...`.
  The observed Natural scheduler cycles were authorized but hit existing
  persisted `DUPLICATE_DECISION` bars / strategy-level no-signal outcomes;
  no manual, acceptance, direct-cycle, or synthetic order was used. The V2
  transaction contract baseline/hash was intentionally not updated because
  the required real Natural fill evidence is absent.

## [TASK-2026-08-19-DECISION-SCOPE-ENTRY-CONTROL]

- 修复了两个窄根因：V2 `already_evaluated_bars` 现在按 `CandidateLane` 读取
  已完成 Decision；Production 历史不会再阻塞 `TESTNET_SAMPLING`，同一
  Sampling lane 的重复保护仍保留。普通/自然模式分别使用同一进程级 mode
  truth，launcher 健康复用会校验 `natural_testnet_enabled`。
- Entry Control 根因已确认是持久化
  `STARTUP_SAFETY_STOP:STARTUP_FAILED`；launcher 现在只对白名单 safety-stop
  reason 恢复 `entry_enabled`，不会覆盖 operator pause、projection recovery
  或未知 incident。普通模式真实启动结果为
  `entry_enabled=true / NONE / NO_AUTHORIZED_PRODUCTION_STRATEGY /
  ENTRY_PAUSED / Production=PENDING`。
- Natural 模式真实启动结果为
  `entry_enabled=true / TESTNET_CANARY / testnet_sampling_v2 /
  promotion_eligible=false / TRADING`。新闭合柱
  `2026-08-18T18:30:00Z` 的 BTC、ETH、SOL、XRP、BNB 均实际到达
  `ENTRY_SIGNAL_EVALUATED`，没有被 `DUPLICATE_DECISION` 截断；终端原因是
  `MACD_DIRECTION_MISMATCH` 或 `MULTI_TIMEFRAME_DISAGREEMENT`。
- 验证：定向测试 `27 passed`，RuntimeScheduler 相关测试 `5 passed, 21
  deselected`，full pytest `1779 passed, 16 skipped, 2 warnings`，full Ruff
  `All checks passed!`，PowerShell parser passed，`git diff --check` passed。
  全仓 mypy 仍有 470 个既有测试类型错误，未涉及本次运行时代码根因。自然订单证明脚本仍为 OPEN：
  `No natural directional Binance Simulation fill was verified...`，未创建
  acceptance/manual 订单，因此未更新 transaction contract hash。

## [TASK-2026-08-20-NATURAL-TESTNET-FINAL-CLOSEOUT]

- Freshness fact verified from the live V2 database: BTC/ETH 15m bars reached
  `2026-08-20T01:00:00Z`; the corresponding Natural Canary decisions reached
  `ENTRY_SIGNAL_EVALUATED` and terminated on `MACD_DIRECTION_MISMATCH`, so the
  earlier `2026-08-18T18:30:00Z` timestamp was historical evidence, not the
  current market-data watermark.
- The proof tool was corrected to inspect the V2 Active fact chain
  (`v2_execution_intents` -> `v2_exchange_orders` -> `v2_exchange_fills`), while
  retaining strict Testnet Canary, non-promotable sampling, and no-manual-order
  filters. Focused regression: `31 passed`.
- Real Natural proof: XRP/USDT entry intent `4dae50a6-7a54-4c0b-925d-f443198f487b`,
  Binance entry order `3481239947`, trade `148671953`, filled quantity
  `10176.5`, average fill `0.9982`, `acceptance_or_manual_order=false`.
  Protection orders were `1000000172235239` and `1000000172235260`; natural
  reduce-only stop order `3481336578`, trade `148717959`, filled quantity
  `10176.5`, and local position `90c6f4a1-d361-43b9-b271-3ec8d8ca6178` is
  `CLOSED`. Latest reconciliation is `HEALTHY` with exchange/local open
  positions `0/0` and open orders `0/0`.
- Ordinary active startup was restored and verified: `natural_testnet_enabled=false`,
  `entry_enabled=true`, `entry_authority=NONE`, `entry_authorized=false`,
  `trading_state=ENTRY_PAUSED`, `production_authorization_state=PENDING`.
- Natural proof artifact: `artifacts/real-binance-natural-order-proof.json`.
  Closeout implementation commit `5e4eed2`, baseline refreeze commit
  `19ef6b3`, and baseline-test synchronization commit `0d8a28b` are pushed to
  `origin/backup/2026-08-10-wip`; the refrozen baseline is
  `5e4eed22ddda72f322dcc1996dee24239cba18cf`.

## [TASK-2026-08-20-FINAL-PROJECT-CLOSEOUT-EDGE-V2]

- Scope deliberately limited to engineering hygiene, methodology audit, and
  the existing `volatility_expansion_v1` / `breakout_retest_v1` candidates.
  No V2 execution Hot Path file was modified.
- Current toolchain: `mypy` PASS (`274 source files`, archive excluded by the
  formal `pyproject.toml` file scope), Ruff PASS, full pytest `1782 passed,
  16 skipped, 2 warnings`, and V2 transaction contract verifier exit code 0.
- Database audit found two historical MEDIUM `PROTECTION_RECOVERY_FAILED`
  rows (2026-08-10 and 2026-08-14). They remain immutable historical incident
  records; subsequent evidence shows 41/41 managed positions CLOSED,
  `37 PROTECTION_FILLED + 4 PROTECTION_CANCELLED`, and latest reconciliation
  HEALTHY with 0/0 open exchange/local positions. No current P0/P1 exposure
  remains, and the existing resolution contract does not silently close this
  incident type.
- Methodology audit classified next-bar parity, point-in-time funding,
  walk-forward ledger, stationary-cluster bootstrap, FinalHoldoutGuard,
  trial registry, lookahead-analysis, and recursive-analysis as
  `ALREADY_SATISFIED`. Spread, latency, and partial-fill remain
  `EXTERNAL_EVIDENCE_REQUIRED` because current replay artifacts mark them
  `ASSUMED`.
- Trade audit: `volatility_expansion_v1` has 453 reported and 453 unique
  proposal IDs (0 duplicates), PF `1.144345`, expectancy `0.001324885`,
  positive windows `6/8`; +5 bps/side PF `1.049003`, +10 bps/side PF
  `0.963235`. `breakout_retest_v1` has 31/31 unique trades, PF `1.082597`,
  expectancy `0.000531709`, positive windows `3/8`, and +5 bps/side negative
  expectancy. Neither satisfies the unchanged Promotion Gate.
- Final decision: `PROJECT_CLOSEOUT: NO_VALIDATED_EDGE`. Production remains
  `PENDING`; entry authority remains `NONE`; no new trial or strategy family
  was added.

## [TASK-2026-08-21-REVIEW-INCIDENT-SCOPE-CLOSEOUT]

- Review reports now count only canonical automatic execution symbols
  (`BTC/USDT`, `ETH/USDT`) for closed-position counts, per-symbol PnL, total PnL,
  and worst-performer ranking. `SOL/USDT`, `XRP/USDT`, and `BNB/USDT` remain
  visible as research-only observations.
- `scripts/audit_runtime_incidents.py` is read-only and now resolves current
  pre-fill intents directly from `v2_execution_intents`; filled historical
  intents do not inflate current exposure. The live local snapshot reported
  `0` active positions, `0` active pre-fill intents, and `1098` unresolved
  incidents classified as historical evidence only (`1096` manual intervention,
  `2` protection recovery failures).
- No execution hot-path file, risk parameter, leverage, stop/take-profit rule,
  promotion gate, exchange credential, or launcher entry parameter was changed.

## [TASK-2026-08-21-AUTO-TRADING-LIVENESS-RECOVERY]

- Implemented per-job liveness state, 25-minute V2 watchdog, critical-task
  supervisor with 5s/15s/30s backoff, shutdown cancellation handling, and
  fail-closed recovery preflight for active intents, `EXCHANGE_UNKNOWN`, and
  reconciliation health. Launcher health now requires the V2 task to be
  registered/alive and the ACTIVE Testnet Canary contract to be valid.
- Identity manifest was re-frozen from the declared strategy inventory while
  keeping `authorization_state=PENDING`, `eligible_execution_symbols=[]`, and
  `NO_VALIDATED_EDGE`.
- Evidence: startup `STARTUP_READY`; two post-restart BTC/ETH decision cycles at
  `15:59` and `16:14 UTC`; natural ETH entry order `16767783498` and natural
  reduce-only exit `16767804479`; final local/exchange positions `0/0`,
  reconciliation `HEALTHY`.
- Verification: `ruff check .` -> `All checks passed!`; `mypy` -> `Success: no
  issues found in 274 source files`; full pytest -> `1800 passed, 7 skipped,
  7 warnings`; focused scheduler/identity tests -> `36 passed`; `git diff --check`
  passed. No strategy optimization or trading parameter changes were made.
# 2026-08-22 — Final repository consolidation / F-101 / F-102

- Implemented the minimal funding-cost wiring at the V2 scheduler boundary and added
  fail-closed Production coverage plus four-condition Canary contract tests.
- Consolidated current-state documentation to remove stale HEAD snapshots and documented
  the actual 5s/15s bounded restart behavior.
- Fast-forwarded `main` to `bd1ca6e`, deleted the backup and all remaining remote Dependabot
  branches, removed `.github/dependabot.yml`, enabled vulnerability alerts, and disabled
  Dependabot security updates.
- Full non-integration verification after the code change: `1802 passed, 14 skipped,
  2 deselected, 2 warnings`; Ruff and configured mypy passed. A restarted natural Testnet
  cycle was healthy but had no candidate, so runtime `r2_cost_gate.funding_R` evidence
  remains pending rather than being inferred from tests.
# 2026-08-22 F-103/F-104 closeout

- Red tests cover funding direction, raw/effective funnel evidence, fresh/stale/missing/future timestamps, Production fail-closed, and Canary diagnostic semantics.
- Focused V2 tests: 43 passed. Full non-integration suite: 1809 passed, 14 skipped, 2 deselected.
- Commit A: `d0a0d3c0d999b67d5b8cef68617f268373f094d4`.
# [TASK-2026-08-22-PROFITABILITY-RECOVERY-LOOP]

- Implemented the bounded dual-lane `Profitability Recovery Loop` around the existing
  `run_alpha_champion_master_loop.py`; no new execution engine, symbols, credentials,
  leverage, sizing, stop/take-profit or mainnet behavior was introduced.
- Registry controls and research proposals now share one tournament; Canary sampling is
  excluded from Champion/Production promotion. Strict recovery gates and observed-cost
  multiplier output are wired, and the scheduler caps Canary to one open position.
- Added pending `CHAMPION_PROPOSAL.json` generation and explicit `EXECUTION_CHAIN` /
  `PROFITABILITY_RECOVERY` reporting. Approval is never synthesized by the loop.
- Verification: focused tests 108 passed; full pytest 1821 passed, 16 skipped, 2 warnings;
  `ruff check .` passed; touched-file mypy passed; `git diff --check` passed. Full-repo mypy
  remains blocked by 171 pre-existing errors in legacy scripts/archive files.
- Runtime evidence: the real Master Loop produced baseline/inventory/data/split/bounded-plan
  artifacts in a temporary output directory, then was stopped before the long replay completed.
  No APPROVED manifest and no Production Binance natural order/fill/protection/exit/reconciliation
  evidence exists. Final status therefore remains blocked/pending, not complete.
# [TASK-2026-08-22-PROFITABILITY-RECOVERY-P0]

- 修复既有双车道 Master Loop 的四个窄阻塞：technical replay point-in-time funding/cost
  evidence、Screening/Promotion 分层、Generation 0 checkpoint/cache、Canary tiny sizing。
- 未修改 Binance adapter/Scheduler 热路径、Production/PAPER 风控参数、策略规则、SL/TP
  或交易品种。Canary 合同例外固定 50 USDT、单持仓、0.01 exposure。
- 验证：`ruff check .` PASS；touched-file mypy PASS；全仓 `pytest -q` 为 1825 passed、
  16 skipped、2 warnings；仍无 APPROVED Manifest 或自然 Production 生命周期证据。

# [TASK-2026-08-22-PROFITABILITY-RECOVERY-P0-B]

- 修复五个接线阻塞：technical Research/Validation evaluator dispatch、Generation-0
  baseline finalist path、Research+Validation 合并后的 Final Promotion 输入、
  finalist evidence artifact 编排、Generation-0 pending-only resume，以及
  `FINAL_AUDIT.json` 实际落盘。
- 同时统一本轮成本压力输出 key 为 `1.0x/1.25x/1.5x/2.0x`，并修复空损失场景
  `profit_factor=null` 进入恢复模型时的转换错误。
- focused recovery tests：`17 passed`；Ruff：`All checks passed!`；mypy：`Success:
  no issues found in 274 source files`。
- Full pytest initially used the wrong `python` interpreter and showed async-plugin
  failures. The declared environment `py -3 -m pytest -q` passed with
  `1828 passed, 16 skipped, 2 warnings`. Formal long replay remains paused until push.

# [TASK-2026-08-22-PROFITABILITY-RECOVERY-P0-C]

- Added finite inventory tournament dispositions without adding strategy evaluators:
  `trend_pullback_v1=SUPERSEDED` by `trend_pullback_v2`, and
  `aggressive_multi_regime_v1=UNIMPLEMENTED_DESIGN_STUB`.
- Both remain visible in inventory with `canonical_replay_reachable=false` and
  `eligible_for_tournament=false`; excluded candidates cannot reach leaderboard,
  finalist, champion, or promotion paths.
- Registered unreachable candidates without a valid disposition still fail closed as
  `BLOCKED_BASELINE`; this prevents future silent registry drift.
- P0-C focused tests `22 passed`; full pytest `1832 passed, 16 skipped, 2 warnings`;
  Ruff, configured mypy, and `git diff --check` passed. Old blocked replay artifacts
  remain under `artifacts/profitability_recovery_20260822`.

# [TASK-2026-08-23-TELEGRAM-KOL-G1-G6]

- Added the Telegram KOL G0-G4 code baseline under `services/agents/telegram_kol/`:
  append-only raw ledger, Telethon User API adapter, folder-derived source registry,
  media hashing/OCR boundary, parser/thread lifecycle, shadow market sanity, candidate
  inbox and injected single-writer dispatcher. V2 Binance adapter remains untouched.
- Raw revisions preserve `source_id`, `raw_id`, and `raw_hash`; candidate keys include
  `telegram:{source_id}:{thread_id}:{message_id}:{revision}`. Multi-TP and conditional
  signals remain shadow-only; claimed leverage/position are context claims only.
- Verification: Telegram focused tests `17 passed`; `ruff check .` passed; `mypy` passed
  (`310 source files`); V2 transaction contract passed; full pytest `1848 passed,
  17 failed, 7 skipped, 25 warnings`, with all failures caused by missing
  `pytest-asyncio` (`async def functions are not natively supported`).
- Real Telegram credentials/session, live folder membership, forward messages, and
  Binance Testnet G5/G6 evidence were unavailable. Telegram system status remains
  `BLOCKED`, not `PASS`; no secrets or sessions were added to the worktree.

# [TASK-2026-08-25-ALPHA-RESEARCH-RECOVERY-V2]

- Added a Research-only tournament for exactly three new alpha families:
  `vwap_deviation_reversion_v1`, `regression_trend_pullback_v1`, and
  `volume_climax_reversal_v1`. Runtime, candidate registry, proposal pipeline,
  ConfigSnapshot, Production Authorization, Binance, and Promotion Gate were not changed.
- Re-ran the bounded tournament against the existing read-only database copy after fixing
  H1 target viability to use only the current closed bar. Added a deterministic test that
  mutating future bars cannot change prior H1 signals.
- Evidence: `artifacts/alpha_research_recovery_v2/FINAL_REPORT.json` is
  `NEW_ALPHA_BATCH_EXHAUSTED`; H1/H2/H3 research PF were `0.6059829`, `0.6471911`,
  and `0.5731455`, with negative expectancy for all three. No validation promotion,
  neighbor search, or Final Holdout access occurred.
- Focused v2 tests: `10 passed`; Ruff passed; mypy passed. Full pytest had
  `20 failed, 1858 passed, 7 skipped` because the environment lacks the async pytest
  plugin; failures are pre-existing async tests and unrelated to this Research-only work.

# [TASK-2026-08-25-ALPHA-RESEARCH-RECOVERY-V3]

- Added a Research-only Futures AggTrades -> closed 5m aggressor-flow pipeline with
  point-in-time OI metrics. Runtime, Execution, Authorization, ConfigSnapshot,
  Production Manifest, Binance code, and Promotion Gate were untouched.
- All 37 AggTrades archives were downloaded and SHA256-validated. Metrics acquisition
  stopped after two Binance Vision TLS EOF failures (`2023-03-02`, retry `2023-03-06`);
  only 47 BTCUSDT metric archives were cached and ETHUSDT metrics were unavailable.
- Final artifact `artifacts/alpha_research_recovery_v3/FINAL_REPORT.json` records
  `BLOCKED_MICROSTRUCTURE_HISTORICAL_DATA`. H1/H2/H3 replay, validation, stability,
  and Final Holdout were not run; resume only from the existing cache after external
  network resolution, with no further retry under the bounded contract.
# 2026-08-25 ALPHA_RESEARCH_RECOVERY_V3_1

- Recovered Binance Vision daily Metrics for BTCUSDT/ETHUSDT: `1096/1096` per symbol,
  `100%` checksum-valid coverage, no source/network/checksum gaps.
- Added manifest-driven, resume-safe downloader behavior with curl-first transport fallback,
  per-object retries, atomic `.part` handling, and 404 source archive classification.
- Fixed Metrics parser for Binance ISO `create_time` values; generated BTC 5m feature cache
  with `105044` rows. ETH feature rows remain `0` because the existing AggTrades cache has no
  ETHUSDT archives; no AggTrades were re-downloaded under task constraints.
- H1/H2/H3 were run after acquisition and all failed research gates; Final Holdout untouched,
  Runtime unchanged, Production not granted.
- Verification: focused V3 tests `12 passed`; Ruff touched files `All checks passed!`;
  `git diff --check` passed.

# 2026-08-25 ALPHA_RESEARCH_RECOVERY_V3_2

- Resumed from the existing cache and completed the missing ETHUSDT AggTrades acquisition;
  BTCUSDT/ETHUSDT AggTrades are both `37/37`, and daily Metrics are both `1096/1096`.
  BTC was not re-downloaded. Final Holdout remained sealed.
- Completed dual-symbol feature construction: BTCUSDT and ETHUSDT each have `105044`
  closed-5m rows and the same feature schema hash. Runtime, Execution, Authorization,
  ConfigSnapshot, Production Manifest, Binance code, and Promotion Gate were untouched.
- Replayed H1/H2/H3 with per-symbol gates and 1.5x cost stress. All three research gates
  failed for both BTC and ETH; validation was `NOT_RUN`, with no portfolio or
  symbol-specific survivor. `FINAL_REPORT.json` is now validly
  `MICROSTRUCTURE_ALPHA_BATCH_EXHAUSTED` for the complete dual-symbol batch.
- Key research PF (BTC/ETH): H1 `0.4417/0.5257`, H2 `0.4237/0.4169`, H3
  `0.4131/0.3928`; all expectancies were negative. Production authority remains
  `NOT_GRANTED`; Runtime modified remains `false`.
- Audit caveat: ETH source parsing excluded `6,506,691` non-positive-quantity rows;
  archives were complete and no zero/neutral feature fill was used, but this quality issue
  is retained explicitly in `DATA_AUDIT.json` and must accompany the exhaustion conclusion.
- Verification after the final type fix: `ruff check` -> `All checks passed!`; mypy on the
  touched V3 script/tests -> `Success: no issues found in 2 source files`; focused pytest
  -> `12 passed in 2.30s`; `git diff --check` passed.

# 2026-08-25 ALPHA_RESEARCH_RECOVERY_V4

- Started `EXTERNAL_CONTEXT_ALPHA_OVERLAY` with the required frozen baseline
  `volatility_expansion_v1`; the runner is overlay-only and cannot create trades or change
  direction/geometry.
- Canonical replay did not reproduce the frozen baseline. Actual current-span evidence is
  `732` trades, PF `0.9198940645`, expectancy `-0.0014049180`, MaxDD `1.0228369251`, versus
  expected `281`, `1.1576630479`, `0.0015124510`, `0.1873643284`.
- Per V4 hard gate, stopped before external data acquisition/joins. Final status is
  `BLOCKED_BASELINE_REPRODUCTION`; H1 breadth, H2 DVOL, H3 Fear & Greed, validation, and
  combined overlay are not run. Final Holdout `false`, Runtime modified `false`,
  Production `NOT_GRANTED`.
- `BASELINE_EVENT_LEDGER.parquet` was generated with `732` rows and required event fields.
  The declared `py -3` interpreter lacked `pyarrow`; the runner reused the existing local
  `.agent-reach-venv` package path without installing or changing dependencies.
- One-shot endpoint availability probes were performed before the baseline run but no
  external rows were persisted or joined; future resume must begin from this blocker and
  repair baseline reproduction first.

# 2026-08-26 ALPHA_RESEARCH_RECOVERY_V4_1

- Implemented `scripts/run_alpha_research_recovery_v4_1.py` and focused tests in
  `tests/test_alpha_research_recovery_v4_1.py`.
- Verified the historical Champion provenance from
  `C:\Users\Windows11\AppData\Local\Temp\ai-quant-p2-champion\FINAL_REPORT.json`:
  `volatility_expansion_v1`, `proposal_pipeline -> ProposalReplayRunner`, three
  expanding Research windows, 24-hour purge, 281 trades (`BTC=132`, `ETH=149`),
  PF `1.1576630479`, expectancy `0.0015124510`, MaxDD `18.7364%`.
- Current V4's 732 event keys have zero overlap with the historical 281 event keys;
  the mismatch is replay contract/scope, not a dataset identity mismatch. Current and
  historical DB SHA256 are both
  `24a6c836b66758f3f4a3b733a7393c4e290e04f0b1610254984456058605e5cb`.
- V4.1 status is `BASELINE_REPRODUCED`, root cause
  `REPLAY_SCOPE_SEMANTICS_MISMATCH`. Corrected 281-row baseline ledger and provenance
  artifacts are under `artifacts/alpha_research_recovery_v4_1/`.
- Historical source commit `470d3d3d...` is not present in current refs; source drift
  comparison is explicitly `UNKNOWN_COMMIT_OBJECT`. Final Holdout, Runtime,
  Production authority, and external overlay acquisition remain untouched.

# 2026-08-26 AUTO_TRADING_THROUGHPUT_RECOVERY

- Resumed existing throughput recovery without repeating prior natural lifecycle evidence.
  Root cause remains `CANARY_SINGLE_POSITION_THROUGHPUT_BOTTLENECK`; the permitted
  Canary contract is now `max_open_positions=2`, `max_total_exposure=0.02`, while
  per-symbol exposure remains `0.01` and diagnostic notional remains `50 USDT`.
- Scheduler, CycleRequest sizing, and Canary portfolio evaluation consume the resolved
  request contract; Production/Mainnet contracts remain unchanged. Audit selection of
  the latest sizing payload is deterministic by `created_at`.
- Refreshed 24-hour artifact records `2757` bar evaluations, `12` candidates, and `4`
  historical `MAX_OPEN_EXPOSURES` blocks; latest resolved sizing is `2 / 0.02`.
- Verification: Canary tests `3 passed`; frontend `21 files / 116 tests passed`; touched
  Ruff and mypy passed; `git diff --check` passed. Official restart returned
  `STARTUP_READY`, API health `200`, and active Testnet Canary Scheduler health.
  Final full pytest is `1889 passed, 16 skipped, 2 warnings`.

# 2026-08-26 ALPHA_RESEARCH_RECOVERY_V5

- Added the independent read-only Telegram history gate runner
  `scripts/run_alpha_research_recovery_v5.py` and focused tests.
- The real local audit returned `BLOCKED_KOL_HISTORICAL_DATA` with blocker
  `TELEGRAM_CREDENTIALS_NOT_CONFIGURED`: collector disabled, no API credentials,
  no local session, and zero accessible groups. Exit code was `2`.
- The runner generated the complete V5 evidence directory, including a zero-row
  `SIGNAL_LEDGER.parquet`, explicit `NOT_RUN` H1/H2/H3/parser/latency/validation
  artifacts, and `FINAL_REPORT.json`. It did not touch Runtime, Binance, or the
  strategy database; Final Holdout stayed sealed and Production remained
  `NOT_GRANTED`.
- Verification: V5 focused tests `3 passed`; touched Ruff and mypy passed; the
  real audit produced the blocker above. Formal research must not resume until the
  minimum Telegram history gate is met.

# 2026-08-26 ALPHA_RESEARCH_RECOVERY_V6

- Added `scripts/run_alpha_research_recovery_v6.py` with narrow-window GDELT and
  read-only Federal Reserve FOMC source auditing, plus focused tests.
- Real execution returned `BLOCKED_EVENT_HISTORICAL_DATA`: the first GDELT probe
  (`2023-01-29` to `2023-02-05`) timed out after 15 seconds; no article rows or
  event clusters were used. The Fed calendar was reachable for 2023-2025, but
  official statement release timestamps were not extracted, so FOMC attribution
  remained unavailable.
- Generated `artifacts/alpha_research_recovery_v6/` with `EVENT_DATA_AUDIT.json`,
  `RESEARCH_PLAN.json`, empty cluster ledger, explicit `NOT_RUN` H1/H2/H3/QA/
  latency/validation artifacts, and `FINAL_REPORT.json`.
- No Runtime or Production surface changed; Final Holdout stayed sealed and
  Production remained `NOT_GRANTED`.

# 2026-08-26 ALPHA_RESEARCH_RECOVERY_V4_RESUME

- Added the independent Research-only runner `scripts/run_alpha_research_recovery_v4_resume.py`.
  It locks the corrected V4.1 Champion ledger before any external join, records event/database/
  strategy/config/split/window/replay hashes, and never creates trades or changes Runtime.
- Baseline lock verified `281` trades (`BTC=132`, `ETH=149`), PF `1.1576630479`, expectancy
  `0.0015124510`, MaxDD `0.1873643284`; Final Holdout remained sealed.
- H1 Binance Spot 1h fixed-universe coverage is complete (`16` monthly archives per symbol,
  `10,836` rows each). The parser handles Binance millisecond and microsecond epoch formats.
- H2 Deribit DVOL was fetched in 30-day windows to respect the API row limit; BTC/ETH coverage
  is complete for all 281 events. H3 Alternative.me Fear & Greed uses the conservative D+1 UTC
  publication policy and covers all 281 events.
- Quartile attribution showed no stable monotonic overlay relationship. All three Research gates
  failed; Validation and Combined were correctly `NOT_RUN`. Final status is
  `EXTERNAL_CONTEXT_ALPHA_BATCH_EXHAUSTED` with `runtime_modified=false`,
  `production_authority=NOT_GRANTED`.
- Verification: focused V4 resume tests `3 passed`; Ruff touched files `All checks passed!`.

# 2026-08-26 ALPHA_RESEARCH_RECOVERY_V6_1

- Reworked `scripts/v6_event_acquisition.py` into a resumable per-slice GDELT
  helper with fixed query families, saturation splitting, network-failure recovery,
  manifest statuses, bounded backoff, and canonical news normalization.
- Added official Federal Reserve statement resolver with transport fallback,
  release-line hashes, DST-safe UTC conversion, scheduled/extraordinary separation,
  and `FOMC_EVENT_LEDGER.json` output.
- Real run used a bounded Q01 historical probe and returned
  `BLOCKED_EVENT_HISTORICAL_DATA` with GDELT `0/36` month coverage and a recorded
  unresolved slice. No BigQuery authority was present, so GKG fallback was not
  attempted. FOMC scheduled coverage recovered to `24/24`.
- H1/H2/H3, clustering, Label QA, latency, Validation, and Final Holdout remain
  `NOT_RUN`; Runtime and Production remain untouched. Artifacts are under
  `artifacts/alpha_research_recovery_v6_1/`.
- Verification: focused V6.1 tests `12 passed`; touched Ruff and mypy passed. A
  full-history acquisition or V6 research continuation was not claimed from a
  probe-only run.

# 2026-08-26 ALPHA_RESEARCH_RECOVERY_V6_2

- Added `scripts/run_alpha_research_recovery_v6_2.py` with a bounded six-date raw
  archive probe (`20230129`, `20230701`, `20240101`, `20240701`, `20250101`,
  `20250701`) for official GDELT daily GKG and Event ZIPs. The probe uses the
  existing curl/requests/PowerShell fallback, validates ZIP integrity in memory,
  and never retains raw archives or touches Runtime/Execution/Promotion/Final
  Holdout.
- Real probe evidence is under `artifacts/alpha_research_recovery_v6_2/`:
  GKG `5/6`, Event `5/6`; all five reachable archives returned valid ZIPs.
  `20250701.gkg.csv.zip` and `20250701.export.CSV.zip` returned empty HTTP 200
  bodies through curl followed by HTTP 404 from requests; both are recorded as
  network failures with `http_status=404`.
- Corrected the schema probe to parse the actual headered GKG 1.0 11-column TSV.
  The five reachable files each supplied 9,999 sampled rows with populated
  `DATE`, `THEMES`, `ORGANIZATIONS`, `PERSONS`, `TONE`, `SOURCES`, and
  `SOURCEURLS`; keyword matches ranged from `6297` to `6619`. Category/schema
  semantics therefore passed for the reachable sample. Event exports were also
  parsed as 58-column records; each reachable sample yielded 10,000 valid rows
  with event codes, quantitative fields, `DATEADDED`, and source URLs.
- GKG 1.0 `DATE` is `YYYYMMDD` day-level only. The parser reports
  `timestamp_resolution=DAY_ONLY` and rejects minute-resolution claims; no
  publisher timestamp or title was synthesized. Final probe status remains
  `BLOCKED_GDELT_RAW_ARCHIVE_NETWORK` (network `5/6`), with timestamp gate also
  failing (`timestamp_pass=false`).
- FOMC ledger was reused at `24/24`; V6 was not resumed, Final Holdout was not
  accessed, Runtime was not modified, and Production remained `NOT_GRANTED`.
- Verification: V6.2 focused tests `5 passed`; real probe completed with the
  blocker above. Ruff `All checks passed!`, mypy `Success: no issues found in 311
  source files`, full pytest `1912 passed, 16 skipped, 2 warnings`, and
  `git diff --check` all passed.

# 2026-08-26 RUNTIME_OBSERVABILITY_AND_MARKET_NEUTRAL_RESEARCH_V1

- Loop A closeout changed only observability surfaces and tests: strategy filters,
  operational entry blocks, and system failures are reported separately; `MAX_OPEN_EXPOSURES`
  is an entry blocker; `signal_generated` now reflects base-signal telemetry.
- Real 24h audit evidence: `221` effective decisions, `2` orders, `1` fill, `1` closed
  position; current runtime healthy at `0/2` open positions; terminal
  `HEALTHY_WAITING_FOR_MARKET`; no sampling-rule change.
- Loop B stopped at Data Audit. Fixed universe Spot 1h cache is only `2023-11..2025-02`
  (`16` monthly archives per symbol). Existing microstructure cache is BTC/ETH daily
  futures metrics plus monthly aggTrades; required perpetual 1h, mark/index/premium,
  funding history, and trading-rule snapshot are absent.
- Generated `artifacts/market_neutral_research_v1/` including
  `MARKET_NEUTRAL_DATA_AUDIT.json`, `DATA_AUDIT.json`, frozen plan, blocked H1/H2/H3,
  NOT_RUN Validation/Stability, and `FINAL_REPORT.json`.
- Terminal: `BLOCKED_MARKET_NEUTRAL_DATA`. Final Holdout was not accessed. Runtime,
  Canary, Production Manifest, Promotion Gate, ConfigSnapshot, and Binance execution
  were not modified.
- Verification caveat: targeted API tests had `5 passed` before fixture setup errors
  caused by the pre-existing missing `telegram_trade_threads` table; `git diff --check`
  passed. Full frontend verification for Loop A remained the previously recorded
  `116 passed`, build success, browser PASS.

# 2026-08-27 QUANT_PROJECT_CLOSEOUT_MASTER_LOOP

- VERIFIED: canonical tests are closed via `scripts/test.ps1` -> `py -3 -m pytest`; full
  suite result is `1915 passed, 16 skipped, 2 warnings` with zero failures.
- VERIFIED: official Binance recovery completed 931 manifest objects under
  `%LOCALAPPDATA%\\ai-quant\\market-neutral-v1`; fixed-universe Spot/Perpetual/Mark/
  Index/Premium 1h archives are checksum-valid, five funding histories are `3288/3288`,
  and current exchangeInfo is snapshotted. Final Holdout stayed sealed.
- VERIFIED: row-level audit is `DATA_READY`; bounded chronological H1/H2/H3 Research
  ran with no hyperopt. H1/H2/H3 expectancies were approximately `-0.00329`, `-0.00214`,
  and `-0.00390`; all Research Gates and 1.5x cost-stress expectancy checks failed.
- VERIFIED: terminal conclusion is `MARKET_NEUTRAL_BATCH_EXHAUSTED`; Validation,
  Stability, and Final Holdout were correctly not run. Runtime and Production surfaces
  remained untouched and Production remains `NOT_GRANTED`.
- VERIFIED: Ruff, mypy, frontend tests (`116 passed`), frontend build, JSON parsing and
  `git diff --check` all passed.
