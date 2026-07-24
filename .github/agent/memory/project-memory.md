# Project Memory

## One-click launcher path matcher (2026-07-24)

- `scripts/launch-paper-console.ps1` `Test-ProjectListener` must recognize vite under the current repo path (`AI--main` / `frontend/.../admin`), not only the legacy folder name `量化项目`. Wrong match skips the "already running" fast path and forces ~50s DB re-prep on every click.
- `一键启动.cmd` must remain a plain ASCII batch wrapper. This host's `cmd.exe` parses a UTF-8 Chinese batch before its `chcp` command takes effect and does not execute UTF-16LE batches. Keep localized text in the delegated PowerShell launcher or other Unicode-aware surfaces; do not restore `taskkill /IM python.exe`.

## One-click launcher encoding and hang fix (2026-07-24)

- The `cmd.exe` launcher uses ASCII-only output and delegates to `scripts/launch-paper-console.ps1`. Do not add UTF-8/UTF-16 Chinese comments or `echo` content to the batch file; the active code page cannot make parsing reliable on this host.
- Do **not** `taskkill /IM python.exe` from the launcher — it mis-kills Cursor/Agent Python and can stall at `[1/3]` with no console output. Process cleanup belongs in `scripts/launch-paper-console.ps1` (pid file + port).
- Success banner API URL is `http://127.0.0.1:8016` (matches `launch-paper-console.ps1` default), not 8000.

## Portable runtime ledger for cross-device review (2026-07-22)

- ADR-073: export last 30 days of orders/positions/decisions/risk/account snapshots from `.local_paper_console.db` into `docs/evidence/runtime-ledger/current/` (`ledger.sqlite.gz` + `manifest.json` + `SUMMARY.md`), redacting secret-like JSON keys.
- Operator cadence is manual: `agent-python -m scripts.export_runtime_ledger` then commit. Other devices `git pull` → `agent-python -m scripts.import_runtime_ledger` → analyze with `--database-url sqlite:///.local_runtime_ledger.db`.
- Hot console DB and `.env` remain gitignored; shared Postgres is explicitly out of scope for this slice.

## Execution runtime compatibility refactor (2026-07-19)

- `PaperRuntimeService` keeps its constructor, `run_cycle()`, and `get_runtime_status()` contract while composing `PaperCycleOrchestrator`, `PaperExchangeExecutionService`, and `PaperOrderLifecycleService`.
- Local fills, position snapshots, realized PnL, and estimated costs now live in `paper_order_lifecycle.py`; gateway request adaptation and expired entry-limit cancellation now live in `paper_exchange_execution.py`. ReduceOnly close requests preserve the position direction so the gateway performs the sole reversal.
- Verification: backend `478 passed, 2 skipped, 2 deselected`; full mypy passed; frontend `37 passed`; production build passed; active-manifest evidence passed. Repo-wide Ruff remains red on unrelated pre-existing lint/format debt, while touched execution files pass targeted Ruff and Mypy.
- Binance Simulation acceptance is pending: preflight confirmed mainnet disabled and Testnet connectivity, but found 2 existing positions. The zero-position/zero-open-order acceptance precondition was not met, so no external state was changed. `verify_config.py` reported `14/19` because current PaperRun isolation/Top3/provenance checks are unsatisfied; scheduler, data freshness, schema, and Testnet connectivity passed.

## Binance Simulation reduce-only exit root cause and operator pause (2026-07-18)

- The repeated Binance `-2022 ReduceOnly Order is rejected` failures were caused by a double side reversal in `PaperRuntimeService._gateway_order_request`: it changed a long close to `short`, then `BinanceUsdtPerpetualGateway` correctly applied its own close-side mapping and sent `BUY reduceOnly` against the long position. The symmetric short-close path incorrectly sent `SELL reduceOnly`.
- The runtime now preserves the position direction until the gateway applies the single reversal. Regression coverage verifies long and short close requests retain the original position side.
- Exchange proof: local close `42bbef8e-c576-4ad4-8b68-4e30bd8a35a7` submitted SOL short close `BUY MARKET reduceOnly`, Binance order `3280641713` filled at 74.65, and the SOL position is flat. This is the first post-fix real Simulation close evidence; prior green configuration checks alone were insufficient.
- Automatic Binance Simulation execution is intentionally paused (`BINANCE_AUTO_EXECUTE=false`, runtime state `blocked_auto_execute_disabled`) pending operator review. BTC long 0.0261 and ETH short 0.451 remain protected by one reduce-only limit take-profit and one reduce-only STOP_MARKET each; mainnet remains disabled.
- The Trading console now labels reconciled reduce-only exchange orders as `交易所保护止盈（限价）` or `交易所保护止损（市价）`, rather than the misleading `手动模拟单`.

## Execution safety, evidence governance, and MAE/MFE diagnosis (2026-07-18)

- Binance Simulation exit ordering now cancels and confirms native protection orders before a market ReduceOnly exit. If cancellation or the exit is uncertain, local state remains open, the exchange is reconciled, and protection is re-armed rather than repeatedly submitting `-2022` failures.
- Entry limits now rest at the signal price for one 15m signal period and expire without a market fallback; protection brackets are submitted only after a confirmed entry fill. Stoploss, risk, and opposite-signal exits remain market exits.
- `ReplayTrade` now records MAE/MFE in R, bars to MFE, and bars held. The first BTC/ETH diagnostic replay produced 716 trades: winning MFE median 2R; only 45/186 take-profit exits exceeded 2R; both trailing and early-entry gates are false, so `trend_momentum_v1` remains unchanged.
- The active manifest is now committed under `docs/evidence/active-manifests/`; CI validates that its candidate, rules hash, eligible symbols, and report ID appear in `CURRENT_STATE.md`. Local artifacts remain non-promotional working evidence.
- The Top10 is an offline technical research universe only. Automatic execution still requires per-symbol OOS evidence plus Simulation acceptance.

## Binance Simulation sampling and exchange-truth reconciliation (2026-07-17)

- Exact Top3 acceptance run `da7edfd9-c1d4-4b04-8b66-02fe82e4af89` completed 6/6 open/close fills at 40x for BTC/ETH/SOL with native STOP_MARKET and TAKE_PROFIT_MARKET ReduceOnly protections and final zero positions/orders.
- Real `signal_observation` orders were then created on the current build/scope: BTC `22305428148` and SOL `3246292050`; both are market entries, 40x, 2.5%/2R protection, and excluded from strategy performance. `verify_config.py` is green 19/19 only because these are current real sampling orders.
- Reconciliation now scans Binance Algo orders, requires missing positions across two scheduler cycles, recovers exchange-only positions locally, cancels orphan protections, re-arms missing Stop/TP, and refuses to interpret ReduceOnly `-2022` as flat without a fresh exchange-flat check. Leverage display falls back to native position-risk or notional/margin ratio.

## Top3 Binance Simulation real-signal sampling armed (2026-07-17)

- Root cause of "all green but no Binance strategy order": active scheduler coverage was Top3 while API/acceptance required literal 20; both scheduled lanes were forced local Paper; the verification script counted old link/reconciliation gateway rows.
- Runtime contract: `auto_paper_mature_templates` stays Paper-only and requires eligible OOS artifact v2. `signal_observation_technical` alone may execute on Binance Simulation after exact BTC/ETH/SOL acceptance, remains non-authoritative, and never counts toward strategy performance.
- Acceptance evidence: run `4e08f295-edb4-463f-850c-5409dd064921`, BTC/ETH/SOL open+close, 6 filled orders at 40x, protection algo refs for all three, final zero positions and zero open orders. Runtime status became `ready`, coverage 3/3.
- Effective sampling profile is synchronized to 5% risk budget, 40x leverage ceiling, 35% symbol cap, 90% total exposure, 25% portfolio initial-risk cap, fixed 2.5% stop and 2R take-profit. Simulation-only; mainnet remains disabled.
- Verification now rejects stale or wrong-provenance gateway evidence and local positions cannot be filled before a Binance fill acknowledgement.

## One-click startup unblocked after hedge migration mismatch (2026-07-16)

- Symptom: `一键启动` died at local DB prep — alembic `0008→0009` tried `ADD COLUMN hedge_group_id` but the column already existed on `.local_paper_console.db`.
- Cause: schema ahead of `alembic_version` (hedge fields + index present, stamp still `0008`); non-idempotent `0009` migration.
- Fix: idempotent `migrations/versions/0009_add_position_snapshot_hedge_fields.py`; local DB prepared to `0009`. Re-run launcher.

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

## Evidence-Based Runtime Convergence (2026-07-16)

- Current truth supersedes the earlier Top20/Top10 runtime notes in this file: only `auto_paper_mature_templates` and local-only `signal_observation_technical` are scheduled, and both scan `BTC/USDT`, `ETH/USDT`, and `SOL/USDT`.
- The main lane is evidence-only. It requires a fresh symbol-scoped artifact with matching candidate/rules hash and rejects missing or stale evidence as `validated_edge_stats_missing_or_stale`. Observation may retain the raw-bar proxy solely for diagnostics and never mirrors to Binance or counts toward strategy performance.
- The 2026-07-16 three-candidate replay used local data, a 365-day window, a chronological 70/30 split, three walk-forward windows, fixed 2R exits, and shared costs. It produced zero OOS trades for every candidate/symbol, so no active manifest exists and no automatic strategy trade is authorized.
- Scheduler/runtime verification after a full restart/bootstrap: `verify_runtime_config_sync` passed, `verify_config.py` returned `GREEN: 19/19`, Top3 data completeness passed, and the seven-day funnel audit reported 3,496 decisions. Mainnet remains disabled; the aggressive Paper risk profile is retained for sampling only.

## Paper Execution And Desk Truth Repair (2026-07-18)

- Paper execution is now constrained to BTC/ETH at the operator-selected 5% risk, 40x leverage, 35% per-symbol, 90% total-exposure profile. `paper-btc-eth-sampling-v1` is persisted into the PaperRun profile and takes precedence over stale strategy-rule sizing values.
- Binance orders expose a server-normalized UTC timestamp; the desk renders all times in Asia/Shanghai and keeps exchange notional, margin, and leverage fields rather than dropping them during mapping.
- The decision-trace API returns a sanitized rejection funnel and recent gateway evidence. It separates signal, OOS evidence, risk, gateway, and exchange rejections so exchange open orders cannot hide local failed submissions.
- Mainnet remains disabled. `BINANCE_AUTO_EXECUTE=false` remains required until a zero-position BTC/ETH acceptance round trip proves entry, protection, close, and reconciliation.

## Trading Correctness Contracts And Versioned Configuration (2026-07-20)

- Automatic execution remains disabled and the existing aggressive Paper sampling values were not changed.
- The shared model now contains immutable, Decimal-based market, signal, portfolio, risk, intent, normalized-order, execution-report, config-snapshot and market-rules contracts with explicit units and controlled enums.
- `trading_config_snapshots` is the versioned runtime configuration fact store. Paper runs carry active/pending snapshot IDs and hashes; writes use optimistic `base_config_hash`, and pending values activate only at a declared cycle boundary.
- `decision_events` is append-only, uses uppercase `BlockCode`, redacts credential-bearing payload fields and derives an idempotent key for one open intent per strategy/version/config/symbol/timeframe/closed candle.
- `CandleValidator`, `OrderNormalizer` and `ExecutionStateMachine` establish fail-closed seams for exchange-time candle closure, one-way/hedge order mapping, and duplicate/out-of-order exchange events. Legacy runtime paths still need to be migrated through these seams before this program is complete.

## Deployment-Coupled Trading Incident Repair (2026-07-22)

- The July 21 11:03 and 18:38 dense BTC/ETH round trips were infrastructure acceptance traffic, not a high-frequency strategy. The old acceptance CLI could submit external Simulation orders without an explicit authorization gate and generated a new idempotency key every invocation.
- Historical order silence must not be equated with scheduler silence: the ledger contains continuous decisions dominated by ensemble discard, insufficient technical signals, LLM veto, and multi-timeframe disagreement. Older July 18/19 data does contain genuine 7–8 hour decision gaps.
- Migration `0011` makes `scheduler_leases` the cross-process leadership fact and `scheduler_cycles(job_name, scheduled_for)` the unique execution-slot fact. Orders also have a unique strategy/symbol/timeframe/candle/intent identity plus explicit deployment/process/origin provenance.
- Paper runtime no longer runs immediately on process startup. Testnet acceptance CLI/API require explicit external-order authorization and a non-empty operator reason before constructing the gateway.
- The accelerated two-instance 24-hour test passed 96/96 slots with zero duplicate winners. A real hourly, read-only 24-hour observation is active in Codex automation 24 and writes `artifacts/live-24h/` snapshots; it must not call any exchange-mutating endpoint.
- Final local runtime state at handoff: API `/health` is OK, scheduler heartbeat is fresh, mainnet remains disabled, and the final process start produced no immediate Paper cycle. No risk, leverage, position, stop, take-profit, or net-edge threshold changed.

## Strategy Liveness And Shadow Ablation Baseline (2026-07-22)

- The Review layer now emits a sequential funnel using the actual runtime order: base signal -> multi-timeframe -> ensemble -> meta-label -> LLM -> Gatekeeper -> TradeIntent. The seven-day baseline contains 765 entry evaluations; ensemble discarded 241/402 arrivals (59.95%), while LLM vetoed only 4/102 (3.92%).
- The most recent 24-hour slice at implementation time contained 77 entry evaluations: base signal removed 20, MTF removed 10, ensemble removed 29/47 (61.70%), LLM veto removed 0, and only 3 of 18 post-LLM candidates became new intents. Nine were risk/exposure rejects and six reached no new intent because of position/execution state.
- The two operator trades at 2026-07-21 20:14/20:20 Asia/Shanghai both had same-direction signals followed by `ensemble_discarded`. BTC was a confirmed fill but its later MFE (0.63%) was smaller than MAE (0.83%) and no theoretical stop/target was persisted; ETH remained `gateway_status=new` with no fill price. Both are therefore `INSUFFICIENT_EVIDENCE`, not proven false negatives.
- After excluding 829 non-directional funding/carry decisions from the denominator, the seven-day A-E shadow recall audit covered 729 directional evaluations and found candidates A=91, B(no LLM hard veto)=95, C(confidence-weighted ensemble)=328, D=91 with 217 unknown, E=332 with 220 unknown. These are candidate-recall counts only; no profitability, 1R/2R, expectancy, or drawdown conclusion is authorized yet.
- Codex automation `24` is retargeted to the active task and performs hourly read-only scheduler, funnel, and A-E audits. Production strategy logic and all risk/leverage/position/stop/take-profit/cost/net-edge settings remain unchanged.

## Read-only trading-chain audit (2026-07-23)

- Past-24h directional execution evidence contains 17 identical gateway failures: `market_rules_snapshot is required for TradeIntent execution`. The fail-closed check was introduced in `9745089`, while the Paper auto path attaches a TradeIntent without supplying the required market-rules snapshot. Scheduler health therefore did not imply an exchange order acknowledgement.
- Scheduler slot uniqueness remains 103/103 with zero duplicate slots, but one claimed cycle ran across roughly 15.5 hours. Code would leave a shielded runner active after lease-renewal failure; no saved lease-loss event uniquely links that mechanism to this long cycle, so the causal evidence is medium rather than conclusive.
- A manually opened ETH short was reconciled into the automatic Paper run and then associated with protection by `run_id + symbol`, without verified direction/origin/exchange-position identity. Exchange read-only history confirms a reduce-only ETHUSDT BUY market fill of 15.144 at 1933.59000 on 2026-07-23 09:29:28.611 Asia/Shanghai. Local timeline attribution is polluted, so this was not established as a valid strategy exit.
- `decision_events` has zero rows; stage conservation is unobservable. The audit verdict is PARTIAL and the only next experiment is a BTC/ETH synthetic intent through risk, normalization, a non-mutating adapter mock, acknowledgement, and reconciliation.

## Paper execution contract and position identity repair (2026-07-23)

- Added migration `0012_position_identity_fencing_events.py` for `position_records`, `protection_records`, order/snapshot identity links, fencing tokens, and execution event fields. No legacy position was backfilled or auto-adopted.
- Manual Testnet and automatic Paper gateway paths share `OrderExecutionContextBuilder`; CCXT market metadata is required for gateway-capable orders and metadata/provider failures become `MARKET_RULES_UNAVAILABLE` while Gateway remains fail-closed.
- Automatic reconciliation requires managed PositionRecord identity and quarantines side/entry/quantity mismatches as `UNMANAGED_EXTERNAL_POSITION`; invalid protection geometry cannot issue reduce-only exits, and hedge snapshots preserve both sides.
- Scheduler fencing tokens are monotonic and lease release checks owner plus token. Six lower-case execution lifecycle events coexist with the historical uppercase `ORDER_SUBMITTED` value.
- Focused execution verification passed. Natural Demo strategy-order proof remains unavailable because a blocking `consecutive_loss_limit_breached` risk event is active and current exchange positions are external reconciliation facts. Status remains `PARTIAL`.
- Managed strategy positions now survive runtime restarts only when the persisted PositionRecord, linked scheduler-origin entry order, exchange fill id, run/strategy, side, quantity, and entry price all match. Runtime-session membership is not identity; manual/external records remain unmanaged and cannot be adopted implicitly.
- HEDGE recovery is fail-closed when both LONG and SHORT are open for one symbol; neither side is auto-protected or auto-closed until identity is unambiguous. Exchange position update time is required for restart recovery.

## Exchange-First Binance Simulation Runtime (2026-07-24)

- The automated directional execution universe is exactly `BTC/USDT` and `ETH/USDT`; Binance USDT-M Testnet/Simulation is the authoritative order, fill, position, and realized-PnL source.
- SQLite/Paper records are post-exchange projections, attribution/audit records, and recovery caches. A local accepted order or position is never proof that Binance executed a trade.
- Safe startup now defaults the directional lane to `binance_simulation_first` only under Testnet credentials and mainnet-off settings, and re-arms a retained stale `paper_only` run from an existing exact BTC/ETH acceptance proof.
- Runtime readiness must verify both exact-scope acceptance and that the actual running directional `PaperRun.execution_profile` is armed (`execution_mode`, gateway flag, cost gate, symbols, and scope hash). The blocker CLI and status API now expose `directional_run_not_armed` instead of reporting a false ready state.
- Confirmed Binance average fill price and filled quantity are authoritative for local open/close projection. A submitted/open exchange order remains locally flat; a filled exchange quantity is never resized by local minimum-notional logic.

## 2026-07-24 Directional throughput runtime facts

- A green `execution_ready` state does not imply a directional candidate exists. The supplied runtime ledger showed the dominant pre-Gatekeeper blockers were `technical_signals_insufficient` and strict `multi_timeframe_disagreement`.
- `operator_heuristic_v2_relaxed` now truly implements its documented policy: the 15m entry direction must agree with at least one of the configured 1h/4h higher timeframes. The earlier implementation changed only the ensemble quorum while a preceding strict MTF gate still rejected it.
- The Testnet-only fallback is enabled only for an armed `binance_simulation_first` directional run and is tagged `decision_variant=simulation_sampling_fallback` / `testnet_sampling_mode=true`. It never runs on mainnet or local-only Paper.
- Bootstrap stages packaged active-manifest rules into the immutable ConfigSnapshot for the next cycle, preventing stale database strategy rules from silently overriding a deployed candidate.
- `py -3 -m scripts.verify_directional_exchange_first` is the deterministic offline proof command. It uses real indicator evaluation and the real orchestration/Gatekeeper/context path with a strict fake Binance fill; it performs no network or exchange mutation.
