# Decisions Log

## ADR-068: Binance Simulation reconciliation must be exchange-truth and protection-complete

- Date: 2026-07-17
- Status: accepted
- Context: The first real sampling cycle exposed three unsafe edges: acceptance/final state ignored Binance Futures Algo orders; a transient empty position response closed the local SOL position while Binance still held it; and a ReduceOnly `-2022` response was treated as proof of a flat position even while BTC remained open. A stale ETH ReduceOnly take-profit also survived without an exchange position.
- Decision: Include open Algo conditional orders in preflight, final state, account reconciliation, and orphan cleanup. Require the exchange position to remain absent across two scheduler cycles before closing a local position, recover exchange-only positions into the local PaperRun, and require every exchange position to have both native ReduceOnly STOP_MARKET and TAKE_PROFIT_MARKET protections; missing protection is re-armed from the persisted entry plan or immediately closed ReduceOnly. Treat `-2022` as flat only after a fresh exchange position check. Read leverage from native position-risk data or the notional/margin ratio when the standardized CCXT field is zero.
- Consequences: Binance Simulation is never considered flat or safe from a single incomplete response. Local state can recover after restart, stale conditional orders are cancelled, and protection gaps fail closed. These controls apply only to the authorized Simulation observation lane; mainnet remains disabled and the mature strategy remains OOS-gated.

## ADR-067: Exact-Top3 acceptance authorizes only the real-signal observation lane for Binance Simulation

- Date: 2026-07-17
- Status: accepted
- Context: The scheduler correctly monitored BTC/ETH/SOL but the status API and acceptance repository still hard-coded 20, while the named fixed-Top20 catalog had already been reduced to 10. Both scheduled runs were forced `paper_only`, so prior Binance probe orders proved only infrastructure and could never establish continuous real-signal auto execution. `verify_config.py` also accepted stale link-verification/reconciliation orders as current proof.
- Decision: Define the automatic Binance Simulation execution scope as exactly BTC/ETH/SOL with a stable scope hash. A completed open/protect/close acceptance for that exact set arms only `signal_observation_technical` as `binance_simulation_first`; the lane uses real technical signals but remains `strategy_performance_eligible=false`. Keep `auto_paper_mature_templates` local and OOS-evidence-gated. Align effective Top3 tiers to the approved Paper baseline (40x/35%), use market entries for sampling, require exchange-native reduce-only stop/take-profit protection, and never create a local filled position from a non-filled gateway response.
- Consequences: Trading readiness now evaluates 3/3 market-data coverage and exact-scope acceptance instead of a literal 20. Bootstrap preserves verified observation authorization across restarts. Current-order verification requires the active build/scope, observation lane, strategy-trade provenance, Binance order ID, and age <=24h. Mainnet remains disabled and observation results cannot promote a strategy without the normal Validation Layer.

## ADR-066: Evidence-only Top3 runtime supersedes legacy Top20/threshold-sampling automation

- Date: 2026-07-16
- Status: accepted
- Context: The repository contained conflicting runtime reports and a live Paper bootstrap that could preserve legacy Top20/Top10 scopes and gateway mirroring even though no signal-conditioned OOS evidence existed. The current objective is to establish whether the implemented signal families have positive expectancy before optimizing fill rate.
- Decision: Make `CURRENT_STATE.md`, current code, and the active runtime database the only current facts; archive legacy reports and one-off diagnostics. Restrict scheduled research to `auto_paper_mature_templates` and local-only `signal_observation_technical` over BTC/ETH/SOL. Keep the aggressive Paper risk profile unchanged, but force both automatic lanes to local Paper-only while evidence is absent. Require candidate-, symbol-, and rules-hash-matched artifact v2 OOS evidence for the main lane; missing, stale, corrupt, or ineligible evidence returns `validated_edge_stats_missing_or_stale`. Use a fixed 2R exit and shared cost model for the three candidate replays, with 30 OOS trades and canonical validation thresholds as hard admission gates. Do not lower thresholds to manufacture orders.
- Consequences: A 365-day replay with insufficient local history correctly generates an immutable rejection report and no active manifest. The scheduler continues sampling/diagnosing Top3 without sending gateway orders, while Gatekeeper, stop-loss, Binance-first accounting, and review boundaries remain unchanged. Any future promotion must be backed by a newly generated eligible manifest and a fresh bootstrap verification.

## ADR-065: AGGRESSIVE Paper testing thresholds to maximize trade sampling (MetaLabel 42%, risk 3.5%, exposure 80%)

- Date: 2026-07-15
- Status: accepted
- Context: After initial conservative optimization (MetaLabel 50% → 46%), user explicitly requested more aggressive settings with clear directive: "不行，在放宽，现在是在模拟盘测试，不开单怎么能行呢，以开单为核心目标，必须优化到正期望，所有路径都要试" ("Must loosen further, this is Paper testing, must generate orders, optimize to positive expectancy, try all paths"). User emphasized this is **Testnet/Paper environment** where the primary goal is generating sufficient trade samples to measure real edge statistics, not proving profitability at these settings. Previous analysis showed all three strategy shapes had negative post-cost expectancy (Directional OOS -0.23%, Single Carry unable to cover costs, Cross-sectional 17.4% win rate), but insufficient sample size (0 orders/day) prevented meaningful statistical analysis or strategy iteration.
- Decision: **AGGRESSIVE Paper/Testnet-only settings** to maximize order flow for data collection:
  1. **MetaLabel threshold**: `0.50 → 0.42` (both directional and swing). This is BELOW breakeven (42% × 2R = 0.84 expectancy) but acceptable for Paper sampling phase. Goal: collect 100+ real trades to measure observed vs. predicted win rate, then recalibrate based on real data.
  2. **Position sizing**: `risk_per_trade: 0.025 → 0.035` (3.5% vs 2.5%), `max_portfolio_initial_risk_fraction: 0.15 → 0.20` (20% vs 15%).
  3. **Exposure limits**: `max_symbol_exposure: 0.20 → 0.25` (25%), `max_total_exposure: 0.60 → 0.80` (80%), `max_position_fraction: 0.20 → 0.25` (directional).
  4. **RiskProfile (medium_risk_profile)**: `single_trade_risk_limit: 0.025 → 0.035`, `max_open_positions: 6 → 8`, `max_leverage: 25 → 30`, `daily_loss_limit: 0.06 → 0.10`, `weekly_loss_limit: 0.10 → 0.15`, `drawdown_limit: 0.15 → 0.20`, `hard_stop_drawdown_limit: 0.22 → 0.30`, `consecutive_loss_limit: 6 → 8`.
  5. **Swing strategy**: Enabled for auto-trading (was `NOT_STARTED`), with matching aggressive thresholds.
  6. **Preserved core safety**: Forced stop-loss, `net_edge_after_cost` gate (not disabled, but more candidates will pass due to lower win-rate requirement), correlation limits, Martingale detection, data-freshness checks.
- Consequences: **Expected to generate 3-10x more order candidates** vs. 50% threshold (estimated 5-15 orders/day across 20 symbols × 2 strategies). This will produce actionable statistical samples within 7-14 days to answer: (1) Do any of the three strategy shapes have real positive edge under these market conditions? (2) What is the observed win rate vs. MetaLabel's prediction? (3) Which signal combinations contribute positive vs. negative edge? **These are Testnet-only settings** — any future live/mainnet promotion MUST re-tighten thresholds and re-validate with OOS evidence per AGENTS.md non-negotiables. The honest state: we are trading sample size for expected profitability during this measurement phase; a negative P&L outcome over 100+ trades with 42% threshold would be strong evidence that the current signal ensemble lacks positive edge under realistic costs, which is itself a valuable finding that prevents burning real capital. Monitoring: daily rejection-reason distribution, cumulative P&L, observed win rate vs. 42% prediction; evaluation checkpoint 2026-07-22 (7 days).

## ADR-064: Paper notional must be capped to max_position_fraction / max_symbol_exposure; account equity must sync from exchange snapshots

- Date: 2026-07-14
- Status: accepted
- Context: Real order history showed ~70% of rejections were `max_symbol_exposure_exceeded` / `max_total_exposure_exceeded` with `open_positions=0` and `symbol_exposure=0`, yet `requested_notional/account_equity` exactly `0.80`. Root cause was the leverage-sized branch of `_requested_notional` (`risk_budget * leverage`) with no fraction cap. Separately, sizing often fell back to a hard-coded `$10,000` equity seed disconnected from Testnet balance.
- Decision: (1) Cap all risk_per_trade sizing paths to `asset_risk_tiers.max_position_fraction` (or strategy fraction), further capped by `execution_profile.max_symbol_exposure` when set. (2) Resolve equity via `account_equity.py` preferring live gateway / `exchange_account_snapshots` / paper metrics over bootstrap seed; sync at the start of each Paper cycle when mirror mode is armed. (3) Keep `net_edge_after_cost` fail-closed — do not relax cost thresholds to manufacture fills (ADR-063).
- Consequences: Gatekeeper should no longer reject empty-book opens solely because sizing asked for 80% equity. Position sizes become consistent with the medium risk profile (20% symbol / 60% total). Profitability still depends on signal edge; engineering reliability no longer blocks sampling that edge.


## ADR-063: Net-edge gate must use a real historical-trade-conditioned edge estimate, not a raw-bar-return proxy; ExitLadder default reverted to already-proven-better fixed-2R; no strategy shape is force-armed without real positive edge
- Date: 2026-07-14
- Status: accepted
- Context: User reported the desk stuck loading after a restart (root cause: an orphan API process outside the standard launcher never got `POSTGRES_URL` set, silently falling back to the docker-compose default `...@timescaledb:5432/...` which cannot resolve on bare metal) and zero Binance orders all day, then explicitly asked to find a real way to make automation both fire orders and have a genuine chance of profiting — not by loosening thresholds to force fills. Investigation found two separate issues: (1) `AUTO_PAPER_TECHNICAL_RULES.takeprofit_rules` still defaulted to the ExitLadder partial-exit config even though a prior session's own real replay (`docs/audits/2026-07-12-exitladder-replay-comparison.md`) had already shown it strictly worse than fixed-2R on every metric (net expectancy -0.000866 vs +0.002185, PF 0.8817 vs 1.1308, max DD 143.6% vs 52.2%) — a regression nobody had gone back to fix. (2) The `net_edge_after_cost` gate's win_rate/average_win/average_loss inputs came from `decision_pipeline.py::_meta_label_samples()`, which is the raw close-to-close return of the last ~47 bars in the ensemble's fused direction — a value with no dependency on whether the actual signal combination ever historically fired, i.e. a noise proxy dressed up as an edge measurement.
- Decision: (1) Reverted `AUTO_PAPER_TECHNICAL_RULES.takeprofit_rules` to `{"risk_reward": 2.0}` (fixed full-close 2R, matching the exact config the prior real-data comparison proved better) with an inline comment citing the audit numbers so a future session does not silently reintroduce ExitLadder as the default without re-running the comparison. (2) Added `services/execution/signal_edge_stats.py` (fail-closed artifact loader, same pattern as the existing `meta_label_model.py::load_active_model`) and `scripts/compute_signal_edge_stats.py` (offline script reusing the already-existing `TechnicalStrategyValidationService` historical replay engine — the same one that produced the Top20 baseline comparison and the ExitLadder comparison — to compute a real trade-conditioned win_rate/average_win/average_loss, gated on a minimum of 30 real historical trade samples before writing an artifact). `decision_pipeline.py::_edge_stats_for_gate()` prefers this real artifact when a fresh one exists for the strategy key, falling back to the raw-bar-return proxy otherwise — this is a pure correctness fix to what the gate measures, independent of whatever direction the resulting number points. (3) Before writing any code, personally re-verified with real data (not existing audits alone) that none of directional-ensemble, single-symbol funding carry, or cross-sectional funding carry currently clear real transaction costs: pulled real Binance **mainnet** public funding-rate history (read-only, no auth) since the platform's own persisted funding history is sourced from `demo-fapi.binance.com` (Testnet/Demo) where 8/20 Top20 symbols sit at a constant `0.0001` floor rate with zero real dispersion, unusable for backtesting; real mainnet data over 60 days showed single-symbol funding at 0.3-1bps/8h (nowhere near a 4-16bps round-trip cost) and cross-sectional basket carry at -46.7% cumulative over 298 real settlement windows (17.4% win rate) once entry/exit costs and directional price risk on the 6 simultaneous legs are counted. Per the user's explicit choice after being shown this evidence, did NOT build a full cross-sectional backtest engine, did NOT relax any cost/edge threshold, and did NOT force-arm any strategy shape into the live auto-cycle scheduler — per AGENTS.md non-negotiables 1/2/6, "the user wants it to open orders" is not evidence a strategy has positive edge, and manufacturing fills on a signal already shown edge-negative would directly cause the "开单亏得比赚的多" outcome the user was trying to avoid.
- Consequences: The directional lane's exit mechanics are back to the only configuration with real positive-net-expectancy evidence on this entry signal. The net-edge gate's rejection/acceptance decisions will use real historical trade outcomes once an operator runs `compute_signal_edge_stats.py` against production history and refreshes it periodically (max_age_days=30, matching the MetaLabel model's staleness policy) — until that artifact exists, the gate transparently keeps using the pre-existing proxy, so this change alone does not create new fills. No new strategy lane was promoted or auto-armed. The honest state remains: under the current market regime and this repo's current cost assumptions, no implemented strategy shape has been shown to clear round-trip costs with real data — a future session revisiting this must not treat "the user wants more fills" as license to skip re-verifying edge with real data again.

## ADR-062: Fee-cost assumptions must track real exchange rates; free reasoning-model providers need `reasoning: {exclude: true}`

- Date: 2026-07-13
- Status: accepted
- Context: Two live rounds of auto Paper trading produced very few fills. Diagnosis found the dominant live cause was `net_edge_after_cost_negative` rejecting nearly all directional/carry candidates, because `bootstrap.py`'s fee assumptions (10bps/18bps one-way core/standard, 8bps carry) were 2-4x real Binance USDM regular-user rates (maker 2bps / taker 5bps). Independently, a second LLM-chain failure mode in the same class as ADR-059 was found during live re-verification: OpenRouter's free reasoning models (`nvidia/nemotron-3-*`) can spend the entire `max_tokens` budget on hidden reasoning tokens, then dump the incomplete reasoning trace verbatim into `content` on `finish_reason="length"` — a non-JSON response indistinguishable from a genuine model failure, and the existing `FallbackChainStructuredLLMRuntime` only caught `LLMProviderUnavailable`, so this single-candidate failure aborted the entire 4-candidate fallback chain instead of trying the rest.
- Decision: (1) Recalibrate `AUTO_PAPER_STRATEGY_RULES`/`AUTO_PAPER_TECHNICAL_RULES` fee/slippage assumptions in `bootstrap.py` to 5bps core/standard fee, 1bps/3bps slippage, matching real Binance USDM rates — this changes what counts as cost-covered, not whether the `net_edge_after_cost <= 0` rejection rule itself applies. (2) `OpenAICompatibleStructuredLLMRuntime.generate_structured()` sets `reasoning: {exclude: True}` for the `openrouter` provider label (verified 0/4 truncation failures vs. 2/4 without it on a heavy real-signal payload). (3) `FallbackChainStructuredLLMRuntime.generate_structured()` now also catches `ValueError` (malformed/non-JSON response) alongside `LLMProviderUnavailable`, so one bad candidate response tries the remaining candidates instead of failing the whole chain. (4) `AGENTS.md` gained an explicit "current Paper-validation-phase risk baseline" section documenting the 15% portfolio-risk cap (ADR-058) and the recalibrated fee assumptions as the *current* baseline, not a permanent loosening — a future live/mainnet decision must re-derive its own thresholds rather than inherit these.
- Consequences: Directional/carry candidates with a genuinely positive edge after realistic costs should now clear the net-edge gate that was previously rejecting them on an inflated cost assumption. LLM veto calls against OpenRouter free reasoning models should no longer intermittently fail with "llm response is not valid JSON" from budget-exhausted reasoning traces. A same-class failure mode (a free-tier provider or model quietly changing behavior) should be assumed to recur periodically and re-verified with live credentials, not just unit tests, per ADR-059's existing guidance.

## ADR-061: Cross-sectional funding-rate carry is a new strategy shape gated behind its own OOS evidence, not folded into the existing single-symbol carry lane

- Date: 2026-07-13
- Status: accepted
- Context: The existing `carry` lane (`services/execution/paper_signal.py::_carry_decision`) evaluates funding-rate arbitrage one symbol at a time against a fixed threshold. The user's LLM-integration remediation proposal's Section 一 asked for a genuinely different risk shape: rank the whole scanned universe by funding rate every cycle and go short the highest payers / long the lowest, which is directionally hedged across the basket by construction rather than by an explicit spot/perp hedge leg. Per AGENTS.md non-negotiables 1/2/6, no strategy may reach live Paper scanning without backtest → OOS evidence, and this is a brand-new, previously-unimplemented strategy shape with zero such evidence.
- Decision: Implement it as a fully separate `strategy_lane == "cross_sectional_carry"` (new `services/execution/cross_sectional.py::compute_funding_rank_snapshot()`, `paper_signal.py::_is_cross_sectional_strategy()`/`_cross_sectional_decision()`, `paper_runtime.py` rank-snapshot computation + `rank_dropout` close), not a variant flag on the existing carry lane. Register it via `bootstrap_cross_sectional_carry_strategy()` as a disabled research strategy (`paper_status=NOT_STARTED`), the same pattern already used for `operator_experience_4h_15m_v1` — visible and versioned in the Strategy Library, but never scanned by the auto-cycle scheduler until a dedicated OOS replay (not yet built) clears the validation gate.
- Consequences: The new strategy shape cannot accidentally go live via the existing `AUTO_PAPER_STRATEGY_RULES` bootstrap path. A future session must build a time-step-driven replay engine (existing `technical_replay.py` is per-symbol and structurally incompatible with a cross-sectional rank that depends on seeing all symbols at once) before this can be promoted, and that promotion must go through the same audit-evidence bar as every other strategy, not a shortcut because the code already exists.

## ADR-060: MetaLabel training script must fail closed on the OOS-AUC gate, and feature extraction must be a single function shared by training and inference

- Date: 2026-07-13
- Status: accepted
- Context: `SignalEnsembleService.create_meta_label`'s rule-based win-rate heuristic was explicitly flagged in the user's roadmap as due for a real trained-model upgrade. The obvious failure mode for any such upgrade is silently deploying an undertrained/overfit model, or computing training features differently from inference features (a common source of train/serve skew).
- Decision: `services/strategy_library/meta_label_model.py::extract_features()` is the single deterministic feature-extraction function called identically by `scripts/train_meta_label_model.py` (training, reconstructed from persisted `ohlcv_bars`) and `services/execution/decision_pipeline.py` (live inference). The training script splits strictly by time — OOS is always the most recent slice, never shuffled — and only writes a model artifact + `active.json` pointer when OOS AUC clears `0.55` and sample count clears `200`; otherwise it exits nonzero and writes nothing, leaving `create_meta_label` on the unconditional rule-based fallback (`load_active_model()` returns `None` on any missing/stale/corrupt artifact, by design, never raising). Model parameters come only from this offline script, never from an LLM call, preserving AGENTS.md's "AI is a researcher, not a discretionary trader" boundary.
- Consequences: Running the script against the real live-Paper SQLite database on both 15m (6956 samples, OOS AUC 0.5435) and 4h (2145 samples, OOS AUC 0.4720) timeframes correctly rejected on both — no model artifact exists on disk, and `create_meta_label` is still using the rule-based path in production. This is treated as the correct, honest outcome given only ~1 week of live Paper history and the already-documented weak/negative backtest edge, not a bug to route around; the script should be re-run periodically as more Paper history accumulates.

## ADR-059: LLM decision-veto chain must fail loud (structured logs + diagnostic endpoint), not fail silent

- Date: 2026-07-13
- Status: accepted
- Context: `decision_veto_agent`/`pre_execution_veto_llm` was silently never reaching a real provider — no log line, no exception, no diagnostic signal anywhere. Root cause was a combination of a missing env-var alias (`ANTHROPIC_API_KEY` not accepted alongside `CLAUDE_API_KEY`), a fallback runtime (`UnavailableLLMRuntime`) that logged nothing when constructed, and a blanket `except Exception` in `_execute_llm_veto` that collapsed every failure mode into one indistinguishable outcome. Independently, a since-retired pair of OpenRouter free-model IDs hardcoded in `_OPENROUTER_SEED_MODELS` would have caused the exact same "looks configured, produces nothing usable" symptom even after the above fixes, and was only caught by end-to-end verification with real free-tier credentials rather than by code review alone.
- Decision: (1) `shared/config.py::claude_api_key` accepts both `CLAUDE_API_KEY` and `ANTHROPIC_API_KEY` via `AliasChoices`. (2) `llm_factory.py::build_configured_llm_runtime()` logs both the no-provider-configured path and the successfully-configured path. (3) `service.py::_execute_llm_veto` splits exception handling into `TimeoutError` / `RuntimeError` (including `LLMProviderUnavailable`) / generic `Exception`, recording a distinct failure reason per branch instead of one generic message. (4) `GET /agents/llm-status` exposes the effective provider chain state for on-demand inspection without needing to read logs. (5) `bootstrap.py::_ensure_auto_paper_run`'s `preserved_keys` now includes `"llm_veto_enabled"` so a manually-disabled veto survives bootstrap restarts instead of silently re-enabling. (6) Free-tier seed model lists (`_OPENROUTER_SEED_MODELS`) must be periodically re-verified against the live provider catalog — a hardcoded seed can go stale and silently break the entire chain the same way a missing API key does.
- Consequences: Any future "the LLM veto isn't running" report should be diagnosable in under a minute via `GET /agents/llm-status` plus structured logs, rather than requiring a full code trace. GitHub Models remains unvalidated in this project pending a token permission-scope fix (PAT lacks the "Models" read scope) — user explicitly chose to defer this and accept OpenRouter-only validation for now; this is a credential/account configuration gap, not a code gap, and should not be re-attempted without either a new token or explicit user instruction to revisit it.

## ADR-058: Fixed Top20 directional automation uses four timeframe responsibilities and deterministic execution authority

- Date: 2026-07-12
- Status: accepted
- Context: The scheduler was running but the default directional lane had stale 1h input while only 1m data was continuously refreshed. Existing positions could also be skipped when their entry bar had already been processed. The user confirmed a fixed Top20 universe, aggressive core-symbol leverage, cost-after-profit requirements, and an LLM role that enhances research without becoming an untestable discretionary trader. This entry was originally mis-numbered ADR-023 (colliding with the 2026-07-03 grounding ADR) and mis-ordered near the 2026-07-02 entries; renumbered and relocated here, and its "5% aggregate initial risk" figure corrected to match the position-sizing fix that followed it (`max_portfolio_initial_risk_fraction` is 0.15 in `services/execution/bootstrap.py`, not 0.05).
- Decision: Keep fixed Top20 as the universe. Directional entries require closed `15m` signals aligned with a `1h` state check and `4h` trend check; `1m` exclusively manages protections. BTC/ETH/SOL cap at 20x, all other Top20 symbols at 10x, while quantity remains capped by 2% stop risk and 15% aggregate initial risk. Hard drawdown at 20% force-closes and locks the PaperRun; 5% daily loss blocks new entries. Apply 10bps/side core and 18bps/side standard costs. LLM/RAG outputs become auditable advisory evidence; only already-persisted high/critical RiskEvents veto entry.
- Consequences: A model timeout, budget limit, or invalid response may not silently relax deterministic risk controls, but also may not independently suppress an otherwise valid rule-based Paper entry. Testnet and live execution remain disabled until the defined 90-day/100-trade, cost-stressed Paper evidence gate is met.

## ADR-057: Exit-ladder remainder trailing stop math is a single shared pure function

- Date: 2026-07-13
- Status: accepted
- Context: `services/execution/paper_runtime.py::_apply_trailing_ratchet` (generic `ProtectiveLevels`-based, used for both plain `trail_after_r` stops and post-ladder remainders) and `services/validation/technical_replay.py::_advance_open_position`'s remainder-trail block independently hand-rolled the identical LONG/SHORT ratchet-to-breakeven condition against two different data structures (`ProtectiveLevels` vs. manually reconstructed `ExitLadderState`/`_OpenPosition`). This meant a future change to the ratchet math (e.g. trailing beyond breakeven) would have to be made correctly in two places or backtest/live behavior would silently diverge.
- Decision: Extract `next_trailed_stop_price()` into `services/execution/exit_ladder.py` (already the shared dependency-free pure-function module both call sites import). It takes side/entry/current-stop/initial-distance/trail_after_r/bar-high/bar-low and returns the new stop price or `None`. Both `paper_runtime.py::_apply_trailing_ratchet` and `technical_replay.py::_advance_open_position` now call this function instead of inlining the LONG/SHORT branches.
- Consequences: Live paper execution and the technical-replay backtest engine are now guaranteed to ratchet remainder-trail stops identically by construction, not by manual code review. `tests/services/test_exit_ladder.py` gained direct unit coverage for the extracted function (long/short trigger, not-yet-triggered, no-improvement, zero-distance cases) independent of either call site's surrounding state machinery.

## ADR-056: Operator leverage/exposure sliders rescale the existing asset risk tier table instead of writing a stale copy

- Date: 2026-07-13
- Status: accepted
- Context: `AutoTradingSettings.asset_risk_tiers` has a Pydantic `default_factory`, so it is never empty. `PaperSignalGenerator._requested_leverage`/`_requested_notional` unconditionally prefer `asset_risk_tiers` over `position_rules.max_leverage` whenever the field is truthy, which is always. The admin UI has no per-tier editor and only edits `max_leverage` (feeding the dead `position_rules.max_leverage` path), so every `auto-settings` save echoed back whatever stale tier table the client last loaded, making the leverage slider silently no-op. The user confirmed via AskUserQuestion that the fix should keep the core/standard/vol_* tier structure (see ADR-055) rather than collapsing it to a single flat leverage value.
- Decision: Add `scale_asset_risk_tiers()` to `services/execution/risk_tiers.py` with a `TIER_SCALE_RATIOS` table (core anchors directly to the slider value; vol_low/standard/vol_mid/vol_high scale down proportionally, preserving the ADR-055 risk ordering). `update_paper_run_auto_settings` (`apps/api/routers/runs.py`) now calls this against the run's *existing* tier table (preserving any ATR%-derived vol_* symbol assignments) instead of persisting the client-echoed payload verbatim. The admin frontend's `DEFAULT_AUTO_SETTINGS` tier defaults were corrected to match the backend, and its static (and factually wrong) tier-preview text was replaced with a live calculation mirroring the same scaling ratios.
- Consequences: The leverage/max_symbol_exposure sliders now actually drive the values `PaperSignalGenerator` reads at order time. Weekly volatility-tier symbol assignments (ADR-055) survive a settings save unchanged; only the numeric leverage/max_position_fraction per tier is rescaled. A future per-tier editor UI can still be added without touching this scaling contract.

## ADR-055: ATR% volatility asset risk tiers with core/standard fallback
- Date: 2026-07-12
- Status: accepted
- Context: Fixed Top20 used only core (BTC/ETH/SOL) vs standard for leverage/position caps, so PEPE and LINK shared the same standard bucket despite very different volatility.
- Decision: Weekly Celery task `refresh_volatility_asset_risk_tiers` scores Top20 30-day daily ATR%, splits into `vol_low`/`vol_mid`/`vol_high` terciles, and writes them into running PaperRun `execution_profile.asset_risk_tiers` (defaults: 15x/12%, 8x/6%, 4x/3%). `resolve_asset_risk_tier` prefers vol_* when those symbol lists are non-empty; otherwise keeps legacy core/standard. Meta stored at `volatility_tier_meta`.
- Consequences: High-vol names get tighter caps without deleting the legacy two-tier fallback. Does not change strategy admission or mainnet policy.

## ADR-054: Acceptance verification and Binance-sim arming must survive task noise and bootstrap
- Date: 2026-07-12
- Status: accepted
- Context: Auto execution stayed `blocked_testnet_acceptance_not_verified` even after a completed Top20 acceptance (20 symbols / 40 fills / flat). Trading-status scanned only `list_tasks(limit=50)`, so newer AgentTasks hid acceptance. Bootstrap also merged profiles with `cost_gate_verified=False`, wiping prior arming. Gateway reconcile raised on CCXT's fetchOpenOrders-without-symbol warning and never cleared local ghosts; portfolio initial-risk 5% conflicted with 5×2% position budget.
- Decision: Add `AgentTaskRepository.has_verified_testnet_acceptance()` filtered by `task_type=testnet_acceptance`. Preserve `cost_gate_verified` / `mirror_to_gateway` / `execution_mode` / `testnet_acceptance_verified_at` across bootstrap. Make `BinanceUsdtPerpetualGateway.reconcile` suppress/survive open-order scan failures and still return positions. Raise default `max_portfolio_initial_risk_fraction` to `0.10` to align with medium profile `max_open_positions=5` at 2% stop-risk. Operator repair script: `scripts/repair_binance_sim_auto_arm.py`.
- Consequences: Local Paper and Binance Demo can stay flat-synced when exchange is flat; automatic cycles can reach the gateway again after acceptance. Does not promote OOS-failed strategies or enable mainnet.

## ADR-053: ExitLadder multi-level paper exits with fail-closed Binance protection refresh
- Date: 2026-07-12
- Status: accepted
- Context: Single-tier `partial_close_fraction` could not express the operator exit path (batch TP → break-even → lock → trail remainder). Exchange partial closes also risk leaving stale STOP/TP quantity after reduceOnly.
- Decision: Add `services/execution/exit_ladder.py` state machine stored under `paper_metrics_summary.exit_ladder`. Default ladder in `AUTO_PAPER_TECHNICAL_RULES`: 1.0R close 40% then stop→entry; 1.5R close 30% then stop→L1 trigger; remainder uses `remainder_trail_after_r=2.5` with fixed take cleared. Legacy single partial path remains when `exit_ladder` is absent. Binance sim partials use reduceOnly market size from order context; after each ladder fill, cancel prior algo refs and re-arm STOP for remaining quantity (`refresh_protection_orders`). Refresh failure rejects the cycle action (fail-closed). Correlation: same-side peer corr>0.7 discounts notional/stop-risk by `1-corr`; ≥2 high-corr peers reject with `correlated_exposure_limit_exceeded`. Gateway reconcile runs every paper cycle when mirror is armed and is independent of entry `cycle_key`.
- Consequences: Improves Execution mechanical correctness without admitting strategies. Top20 OOS gates remain failed; auto-enable and mainnet stay off. Evidence: `docs/audits/2026-07-12-exitladder-binance-sim-boundary.md`.

## ADR-052: Technical SignalEnsemble uses regime filter then entry trigger
- Date: 2026-07-12
- Status: accepted
- Context: Prompt 1 showed the current 4h/1h/15m policy did not beat a 1h baseline under shared fixed exits. One structural cause is that direction and entry triggers still share one flat weighted vote, so trend confirmation and precise entries dilute each other.
- Decision: Keep the eight technical generators unchanged. Add fusion method `layered_regime_entry` used by DecisionPipeline: direction layer requires `dow_trend`, `ema_trend`, and `adx` to agree or else `allowed_direction=none`; entry layer votes (`macd`, `rsi`, `price_action*`, plus market intelligence) only count when aligned with that direction; `vwap` and `bollinger` vote only in the range case (`none`). Default API fusion remains `weighted_vote` for backward compatibility. No automatic promotion from this change alone.
- Consequences: Offline Top20 prescreen must be re-run after the layering lands. Later ExitLadder / Binance-sim work still waits on that re-validation gate.

## ADR-051: Top20 technical validation compares the current automatic policy against a one-hour baseline without execution mutation
- Date: 2026-07-12
- Status: accepted
- Context: The requested redesign proposed validating `operator_experience_4h_15m_v1` before automatic use. The repository already has two different concepts: ADR-046 keeps that named strategy disabled as a research candidate, while ADR-023 defines the current automatic directional policy as `4h trend -> 1h state -> 15m entry -> 1m protection`. Treating the named research record as the automatic candidate would create overlapping strategy authority and would not answer whether the actual current main policy improves on the former one-hour behavior.
- Decision: Keep all automatic Paper/Testnet settings unchanged. The validation slice compares a reconstructed one-hour directional baseline with a read-only reconstruction of the current automatic directional policy. It reuses closed historical bars, DecisionPipeline signal logic, and existing risk-price calculation, deducts round-trip fees and slippage, records OOS windows, and writes only an audit report. Missing data or any threshold failure is non-promotable. The legacy named operator-experience strategy remains disabled and outside this comparison.
- Consequences: The result can supply evidence for a later admission review but cannot itself alter a Strategy, PaperRun, risk profile, execution profile, or gateway. Any subsequent change to live automatic behavior requires a separate explicit decision after the existing Paper evidence gate.
- Outcome: The fixed Top20 replay covering 2026-04-26 through 2026-07-12 found candidate OOS net expectancy `-0.000005` versus baseline `0.001473`, with Profit Factor `1.1036` and maximum drawdown `72.98%`. All prescreen gates failed, so no automatic configuration or strategy eligibility was changed. Evidence: `docs/audits/2026-07-12-top20-technical-validation.md`.

## ADR-050: Strategy Playbook is a code-backed snapshot with separately persisted, audited roadmap state
- Date: 2026-07-12
- Status: accepted
- Context: The Strategy Library UI exposed only CRUD assets while the detailed operator documentation was static Markdown and already diverged on MetaLabel/leverage defaults and open-source license metadata. A static frontend transcription would drift again, while making documentation an executable trading configuration would incorrectly couple operator explanation to execution authority.
- Decision: Publish a typed Playbook through a dedicated read API, deriving scoped numeric values from current Python defaults and external projects from the structured source manifest. Label every response with source documents, verification date, commit, and a non-realtime disclaimer. Persist only optimization-roadmap status/note/operator/audit history through an authenticated PATCH endpoint; roadmap state never changes Strategy, Validation, Risk, or Execution configuration. Treat GPL/AGPL/LGPL as distilled research only, unknown licenses as metadata only, and require rule conversion even for permissive licenses.
- Consequences: Operators can understand and track the current research system without reading code, and updates survive refresh with an audit trail. Code/config changes still require a new Playbook verification pass. The UI cannot claim real-time code reflection or use a roadmap checkbox to promote a strategy or bypass validation.

## ADR-049: Strategy cold-start and multi-timeframe confirmation fail closed before bounded Top20 Testnet acceptance
- Date: 2026-07-12
- Status: accepted
- Context: Existing technical and Testnet paths were broadly implemented, but MetaLabel could authorize a bet from only a few positive samples, explicit 4h/15m confirmation treated unavailable 4h evidence as a pass, default PaperRun semantics still implied BTC/ETH or dynamic quote-volume selection, and the Top20 acceptance result lacked per-symbol phase evidence.
- Decision: Require at least 20 MetaLabel samples; fail explicit multi-timeframe confirmation closed when confirmation data/signals are absent; default automatic Paper orchestration to the fixed operator Top20; cap acceptance at 120 USDT per symbol; persist/return per-symbol stages, order/protection refs, compensation outcome, and failure class. Keep Mainnet disabled and require a sanitized read-only preflight before external orders. Keep Celery as the active scheduler and document Temporal only as a future deterministic migration boundary.
- Consequences: Cold-start strategies and incomplete multi-timeframe evidence can no longer open positions. The fixed Top20 contract is explicit, and a real Futures Testnet run proved 40 fills with final zero exposure. Spot carry remains blocked until separate Spot Testnet credentials exist; Docker/Celery long-soak evidence remains outstanding.

## ADR-048: Testnet acceptance is isolated from strategy admission, and funding carry requires two real exchange legs
- Date: 2026-07-11
- Status: accepted
- Context: The operator needs a deterministic Binance Mock/Testnet execution proof across the fixed Top20, more assertive but bounded asset-specific sizing, a true funding-rate carry round trip, and a complete operator workbench. The existing automatic strategy lane could not be repurposed for forced acceptance orders without confusing exchange-connectivity proof with Validation Layer evidence, and the earlier funding lane did not itself prove a Spot/Futures hedge.
- Decision: Add two explicit, Testnet-only execution workflows. The Futures acceptance run requires Testnet, live disabled, a configured proxy and clean preflight state; it processes the fixed Top20 sequentially with per-symbol idempotency, core `10x/15%` and standard `5x/6%` tiers, mandatory protection, reduce-only closing, compensation, and final flat reconciliation. Add a separate Spot gateway and carry state machine for Spot long plus equal-notional Futures short, with independent Spot credentials and leg-level compensation. Neither workflow changes strategy backtest/paper admission status. Present both workflows and their evidence in a fixed-height trading record workspace while removing order-book ingestion from the frontend.
- Consequences: The platform can prove exchange mechanics without fabricating strategy quality or bypassing Validation -> Execution -> Review. Mainnet remains disabled, acceptance fails closed on pre-existing account state, and official Binance screenshots remain an external acceptance artifact. The current implementation is locally verified, but external completion is blocked until the HTTP proxy listens on `127.0.0.1:7890` and Spot Testnet credentials are configured.

## ADR-047: Operator-armed Mock execution preserves fail-closed defaults and uses one fixed Top20 runtime contract
- Date: 2026-07-11
- Status: accepted
- Context: The frontend had loaded newer Top20/automatic-trading code while a July 9 API process kept serving the older contract. Two automatic PaperRuns scanned candidates but could not submit Binance Mock orders because global auto execution and run-level gateway mirroring were disabled. A three-symbol frontend fallback obscured the real monitored universe, order reconciliation appeared BTC-only, and an unbounded DEBUG log retained sensitive Binance request material.
- Decision: Keep automatic exchange submission opt-in by default, but honor this operator's explicit local `.env` choice to arm Binance Mock/Testnet (`BINANCE_AUTO_EXECUTE=true`, `BINANCE_USE_TESTNET=true`, `LIVE_TRADING_ENABLED=false`) without startup scripts rewriting it. Use the server-owned fixed Top20 list and `binance_simulation_first` for the two admitted automatic runs; pause the legacy duplicate run while preserving history. Submit to Binance Mock first and persist local fills, positions, protection references, and `gateway_order_id` only after gateway success. Expose build/arming/universe state, reconcile every Top20 symbol, remove synthetic frontend fallbacks, and redact/rotate operational logs.
- Consequences: Credentials alone still cannot enable exchange execution, mainnet remains disabled, and Strategy -> Validation -> Gatekeeper -> Risk remains mandatory. No qualifying signal legitimately produces no order but now has visible per-symbol reasons. Initial universe metadata can be `unknown` while asynchronous `exchangeInfo` refresh is pending; the execution gateway independently validates tradability before submission. Optional source failures remain task-level evidence rather than a false global scheduler outage, while core execution/data/risk/review task failures still set scheduler health. Existing credentials remain a residual risk because they appeared in deleted local logs and should be rotated by the operator.

## ADR-046: Fixed Top20 automation uses Binance simulation-first sync and typed operator risk settings
- Date: 2026-07-10
- Status: accepted
- Context: The operator observed that automatic trading was not opening orders and was not reliably scanning the intended top 20 coins. The previous automatic lane also overfit operator experience (`4h + 15m`) into default strategy behavior, while frontend controls did not expose enough real-time risk and order-sync settings.
- Decision: Use a fixed operator Top20 universe for automatic Paper/Testnet cycles, with explicit Binance USD-M exchange-symbol mapping and runtime `exchangeInfo` tradability checks. Keep `4h_direction_15m_entry` only as disabled research strategy `operator_experience_4h_15m_v1`; default automation uses mature-template lanes that must pass Strategy -> Backtest/OOS -> PaperRun -> Binance simulation -> Review. Automatic gateway mode is `binance_simulation_first`: submit to Binance Testnet/Demo first and create local fills/positions/protection refs only after exchange success. Expose typed auto-trading settings in the Trading console and persist changes into PaperRun execution profile plus RiskProfile audit context.
- Consequences: The automatic engine scans the intended universe and fails closed for untradable symbols or gateway errors. Operators can adjust leverage, sizing, max positions, max symbols, stoploss/takeprofit, LLM veto, and Market Intelligence toggles without editing code. No mainnet/live trading is enabled by this decision.

## ADR-045: Automatic Paper cycles never imply Binance/Testnet execution consent
- Date: 2026-07-09
- Status: accepted
- Context: A local Paper/Testnet run left far BTCUSDT conditional protection orders on Binance because auto bootstrap enabled `mirror_to_gateway` whenever credentials existed, startup scripts forced `BINANCE_AUTO_EXECUTE=true`, and the gateway did not validate protection trigger distance before submitting exchange orders.
- Decision: Treat exchange submission as explicit operator opt-in only. Default automatic research cycles remain local Paper. Credentials alone do not enable `mirror_to_gateway`, bootstrap must not flip existing PaperRuns into exchange mirroring, and local startup/default environment files keep `BINANCE_AUTO_EXECUTE=false`. Binance protection triggers must be directionally valid and within a configured maximum distance (`GATEWAY_PROTECTION_MAX_DISTANCE_BPS`, default 800 bps) from the execution reference before entry submission.
- Consequences: The local console can still monitor live market data and run Paper cycles, but it will not create Binance/Testnet orders unless the operator deliberately enables both run-level mirroring and global auto execution. Far or stale stop/takeprofit triggers fail closed before any entry order is sent.

## ADR-044: Market Intelligence is a capped Strategy vote, not a new execution authority
- Date: 2026-07-09
- Status: accepted
- Context: The user approved upgrading the message-source area into a Market Intelligence factor system for Binance USDT-M Top20, with CoinGlass/CryptoQuant/DeFiLlama/news/macro evidence assisting long/short direction. The project already had RiskEvent, SignalEnsemble, MetaLabel, Decision Veto, and Gatekeeper boundaries, so adding a separate "AI market engine" with direct order authority would violate the six-layer architecture.
- Decision: Implement Market Intelligence inside the existing architecture. Data Layer owns `MarketEvent`, feature snapshots, provider status, and provider adapters. Strategy Layer consumes `MarketIntelligenceSignal` as a capped `market_intelligence` vote with `vote_weight <= 0.30`. It can join `SignalEnsemble` only after deterministic technical signals exist, so it cannot open a trade by itself. Active high/critical events keep the system in cooldown and disable the vote. CoinGlass/CryptoQuant are adapter-first and fail soft as `missing_credentials`; DeFiLlama is a credential-free provider status. Execution still flows through MetaLabel, Decision Veto, Gatekeeper, stoploss, RiskEvent, and Paper/Testnet only.
- Consequences: Important news/data can materially tilt the ensemble without creating an LLM/news-to-order shortcut. Missing paid-provider credentials are observable but non-fatal. Review and Ops surfaces can inspect provider status, cooldown, evidence, and vote contribution. Future real CoinGlass/CryptoQuant fetchers can fill the same contract without changing execution authority.

## ADR-043: Binance Testnet smoke must time-sync private calls, close reduce-only with inverse side, and leave the account flat
- Date: 2026-07-09
- Status: accepted
- Context: A real Binance Futures Testnet open/close verification exposed two operational risks that unit tests had not fully covered: private API calls can fail with timestamp drift unless ccxt time difference is loaded after URL mode selection, and close-only requests represent the current position side, so the exchange order side must be inverted.
- Decision: Configure the Binance gateway with `adjustForTimeDifference`, call `load_time_difference()` after demo/testnet URL configuration, and map close-only long positions to SELL reduce-only orders and close-only short positions to BUY reduce-only orders. Real smoke tests must verify the final account is flat and must write a sanitized local report without secrets.
- Consequences: Testnet automation is more robust against local clock drift and no longer risks adding to a position when trying to close it. Operator smoke tests can leave auditable Binance order IDs while preserving a zero-position end state.

## ADR-042: Technical lane trades only on explicit signals, Binance auto execution fails closed, and Strategy Library docs are first-priority RAG
- Date: 2026-07-09
- Status: accepted
- Context: The user asked to make the quant strategy, opening/closing logic, risk controls, Binance Testnet path, and RAG strategy library complete enough for iterative optimization. Earlier technical execution still had an unsafe candle fallback, frontend/test wording still mixed "local Paper first" with the now-selected Binance-first semantics, and RAG lookup did not prioritize the manually editable `策略库`.
- Decision: Remove the technical lane's no-signal fallback and require explicit Strategy Layer signals before any non-carry automatic order. Expand the default technical lane to 4h direction plus 15m entry with MACD/Dow/price-action/RSI/EMA/ADX/VWAP/Bollinger signals and conservative sizing defaults. Keep LLM/RAG as research, classification, veto, and review support only. For `BINANCE_AUTO_EXECUTE`, submit to Binance/Testnet first and only record the local fill after gateway success; gateway failure must reject the local order with auditable rejection codes. Make `策略库/*.md` the first RAG source, with distilled open-source assets as secondary context.
- Consequences: The platform is safer but more selective: missing or weak technical evidence produces no trade. Testnet automation no longer creates false local fills when the exchange path fails. Operator-curated strategy documents become the main RAG substrate, while GPL/AGPL projects such as ABU remain research-only and cannot be copied into runtime execution.

## ADR-041: `/metrics` is unauthenticated and intended for internal Prometheus scrape only
- Date: 2026-07-08
- Status: accepted
- Context: TASK-033 added runtime observability for the in-process scheduler and LiveFeedBus, but the admin API otherwise requires Bearer auth on all `/api/v1/*` routes.
- Decision: Expose `GET /metrics` without Bearer auth (added to `PUBLIC_PATHS`) using `prometheus-client` Gauges sourced from `runtime_scheduler_status()` and `live_feed_bus.status()`. Configure Prometheus in Docker Compose to scrape `api:8000`.
- Consequences: Metrics are suitable for private-network monitoring only. If the API is ever exposed publicly, network policy or reverse-proxy auth must gate `/metrics`; business APIs remain Bearer-protected.

## ADR-040: Paper runtime enforces protective exits, optional Testnet mirroring, and free-model LLM fallback
- Date: 2026-07-08
- Status: accepted
- Context: Paper signal generation already produced stoploss/takeprofit plans, strategy defaults already included `trail_after_r`, and the Binance USDT perpetual gateway already existed, but the automatic Paper runtime neither consumed protective levels nor mirrored automatic fills to Binance Testnet. LLM routing also failed closed unless Anthropic credentials were configured, despite the product needing low-cost/free model classification and veto while preserving fail-closed behavior.
- Decision: Enable protective stoploss/takeprofit checks directly in `PaperRuntimeService` for all existing Paper positions before signal/no-trade handling. Resolve protective levels from the latest filled non-close entry order, use intrabar high/low crossing, fill at trigger price, and prioritize stoploss when both levels are crossed. Consume `trail_after_r` by ratcheting stoploss to entry in `PaperRun.paper_metrics_summary`. Add optional per-PaperRun `execution_profile.mirror_to_gateway` so local Paper fills can be mirrored to the configured Binance Testnet gateway only after explicit operator enablement. Add an OpenAI-compatible structured LLM runtime plus fallback chain so Anthropic is tried first when configured, then OpenRouter free models, then GitHub Models using a dedicated `GITHUB_MODELS_TOKEN`.
- Consequences: Protective risk control now applies immediately to Paper runtime positions and writes Review evidence. Testnet mirroring is observable and recoverable but cannot roll back local Paper state. LLM unavailability or exhausted fallback candidates still fails closed through existing AgentTask handling; no Agent gains authority to emit orders or bypass Gatekeeper.

## ADR-039: Docker schedulers use Celery, local Paper keeps in-process, and third-party data refresh stays fail-soft
- Date: 2026-07-07
- Status: accepted
- Context: TASK-029 made local Paper operation practical with an in-process scheduler and a shared Binance WS feed bus, but Docker paper/live overlays could still inherit `.env.example`'s `RUNTIME_SCHEDULER_MODE=inprocess`, creating duplicate scheduling beside Celery Beat. The frontend also still had platform entries that were honest placeholders and did not visibly consume third-party-backed research/news/macro/ops data.
- Decision: Keep `RUNTIME_SCHEDULER_MODE=inprocess` for local one-click Paper only, and force `RUNTIME_SCHEDULER_MODE=celery` in paper/live Docker overlays for API, worker, and beat services. Add compose validation to reject missing paper/live scheduler overrides. Wire Binance WS reconnect exceptions into `LiveFeedBus` status. Replace Validation/Review/Research/Ops placeholders with real API-backed pages. Add `refresh=true` read-through to news and macro endpoints using existing third-party ingestion services, with external fetch failures returned as `refresh_error` instead of failing the page.
- Consequences: Local operators retain the Redis-free Paper console, while Docker deployments avoid duplicate periodic execution. Frontend pages now reflect the actual research loop inputs and third-party seams without adding a seventh layer or direct frontend-to-provider calls. News/macro refresh remains conservative and fail-soft, so third-party outages do not hide persisted data or bypass risk controls.

## ADR-038: Local Paper runtime uses in-process scheduling and a shared Binance WS feed bus
- Date: 2026-07-07
- Status: accepted
- Context: The Paper runtime logic and Celery Beat schedules existed, but the local one-click console only started FastAPI and Vite, so automatic cycles did not run without Redis/Celery. The market websocket endpoint also still polled REST per frontend websocket connection, while the Binance live collector was implemented but not connected to frontend push.
- Decision: Keep Celery as the production/multi-process scheduler, but add an in-process FastAPI scheduler for local Paper operation. It calls the same task bodies used by Celery and exposes safe status through `/api/v1/execution/trading-status`. Add an in-process `LiveFeedBus` so Binance WS collectors can publish closed candles once and websocket clients can subscribe to the shared stream. The initial exchange scope remains Binance-only; OKX/Bybit stay enum placeholders only and are removed from runtime configuration templates.
- Consequences: The one-click Paper console can now run automatic cycles without Redis/Celery and can use one shared Binance WS feed instead of per-tab REST polling. Operators can still switch to Celery mode for Docker/production. Multi-user/RBAC remains a product non-goal for this phase, and compose runtime smoke still requires a host with Docker on PATH.

## ADR-037: Paper console live market data must use Binance public REST fallback, not synthetic UI data
- Date: 2026-07-07
- Status: accepted
- Context: The exchange-style Paper console looked static because several panels still depended on persisted local data or frontend-synthesized order book/trade rows. The local runtime also did not have `ccxt` installed, so CCXT-only live endpoints failed with 500 instead of serving real public market data. The user explicitly required real exchange or TradingView-style data and a demonstrable Paper open/close flow.
- Decision: Keep the first production boundary as Paper/Testnet only, but make Binance public market data a direct Data Layer capability. Add standard-library Binance REST fallback for USD-M 24h ticker, klines, depth, trades, funding history, and premiumIndex so `ccxt` is optional for public reads. Read endpoints may refresh from Binance and persist OHLCV/funding into the existing repository, while frontend order book and recent trades must render backend payloads only. Manual trading remains behind Strategy/Validation evidence, stoploss, Gatekeeper, and Review; blank evidence is rejected at request validation.
- Consequences: The local one-click console can show real Binance public data even without CCXT installed and without exposing API keys. Public market reads are still not private account sync or mainnet trading. If Binance public REST is unreachable, endpoints return explicit empty/error-source states or persisted fallback rather than invented market rows.

## ADR-036: Open-source projects are stored as traceable distilled RAG assets before strategy extraction
- Date: 2026-07-06
- Status: accepted
- Context: The first open-source intake slice registered Freqtrade, Hummingbot, ABU, TradingAgents, and related repositories, but `fetch_remote=true` still only wrote local summary files. That made the system look RAG-ready while lacking real local source assets, commit traceability, asset hashes, and asset-driven extraction evidence.
- Decision: Add `ResearchSourceAsset` and make `OpenSourceStrategyLibrary` fetch only allowlisted GitHub files, distill them into local Markdown assets, write `asset_manifest.json`, and record failures explicitly. Keep storage as "distilled assets in repo" rather than full repository mirrors. Extend source manifests with license policies, allowlists, denylists, and extraction targets. Make `OpenSourceStrategyExtractor` require local `asset_refs` for rule/metric extraction and keep unknown-license or metadata-only projects as `research_note_only`.
- Consequences: The platform now has reproducible local RAG substrate for E-level open-source research sources without importing external runtime code or bypassing Validation/Execution/Review. GPL/AGPL materials remain distilled research references only. Vector database indexing, deep LLM reports, full repository mirrors, and live connector adoption remain future work.

## ADR-035: 7x24 Paper automation uses a Decision Pipeline inside Execution, fed by Strategy/Data/Agent seams
- Date: 2026-07-06
- Status: accepted
- Context: The Paper runtime had an autonomous cycle entrypoint, while technical signals, SignalEnsemble, MetaLabel, and Decision Veto Agent existed but were not connected to the real automatic open/close path. The next implementation plan required a Binance-only, Paper-only 7x24 loop with technical signals, message/macro inputs, LLM veto, and frontend decision visibility, without unlocking live execution or adding a seventh architecture layer.
- Decision: Keep orchestration inside the existing Execution Layer by adding `DecisionPipeline` as the upstream order-decision component for non-arbitrage Paper signals. The pipeline consumes Strategy Layer technical signals and ensemble/meta-label services, Data Layer market/news/macro/risk context, and Agent Layer Decision Veto tasks, then emits an `ExecutionOrderRequest` that still must pass `ExecutionGatekeeperService`. Funding-threshold arbitrage stays deterministic and bypasses technical ensemble fusion. Celery Beat schedules the Paper loop plus heartbeat/risk/review/notification/news/macro/social tasks. LLM veto has a configurable daily budget, default 200 calls/day, and over-budget/timeout paths fail closed with auditable AgentTask/notification evidence. Admin UI reads the persisted runtime trace rather than inventing a separate frontend decision model.
- Consequences: The 7x24 Paper loop now has an auditable assembly line while preserving the required `Strategy -> Validation -> Execution -> Review` chain and Binance/Paper safety boundary. Live trading, OKX/Bybit, multi-tenant auth, trained ML meta-labeling, and Chan theory remain out of scope. Docker-level runtime verification remains host-dependent when Docker is not on PATH.

## ADR-034: P0 runtime hygiene treats templates, local databases, and workstation paths as non-runtime inputs
- Date: 2026-07-05
- Status: accepted
- Context: The P0 audit found three operational risks that could mislead future work: `.dev_ai_quant.db` was tracked in Git, compose runtime services used `.env.example` as their actual env file, and several user-facing Markdown links plus Research Agent alpha scanning still pointed at this workstation's absolute paths. The same audit also found that single-tenant auth used ordinary string comparison and allowed the default token outside local development.
- Decision: Keep `.env.example` as a template only and require compose runtime services to read `.env`; enforce this in `scripts/compose_validate.py` and CI. Remove runtime database artifacts from tracking and add repository hygiene tests that reject tracked `.db` / `.sqlite` / `.env` / private-key-like files. Make user-facing Markdown links repository-relative. Remove the Research Agent's workstation-specific alpha fallback so alpha intake must receive `alpha_root` or `WORLDQUANT_ALPHA_LOCAL_PATH`. Keep auth single-tenant for now, but use constant-time token comparison and reject `dev-admin-token` outside development/test environments.
- Consequences: P0 hardening reduces accidental secret/runtime-artifact exposure and makes the repo portable across machines without changing the six-layer architecture or adding RBAC. Operators must create a real `.env` for compose runtime. Future P1 work should preserve these guards before adding 7x24 scheduling, broader frontend coverage, or B/C/D data source ingestion.

## ADR-033: Autonomous paper monitoring stays inside the existing Execution Layer as repeatable cycles, not a direct always-on bot
- Date: 2026-07-04
- Status: accepted
- Context: The user asked to keep filling missing functionality by adapting patterns from TradingAgents, Freqtrade, Hummingbot, Lean, vn.py, and related projects, with the practical near-term goal of moving the repo toward automatic market monitoring and simulated trading. The repo already had validation admission, paper step generation, and execution gatekeeping, but it still lacked a self-owned runtime slice that could repeatedly scan a candidate universe and maintain paper positions without bypassing the six-layer chain.
- Decision: Add `PaperRuntimeService` and expose it through `/api/v1/execution/paper-runs/{paper_run_id}/auto-cycle` plus `/runtime-status`, with a matching Celery task entrypoint `services.execution.tasks.run_paper_runtime_cycle`. Keep the runtime inside the existing Execution Layer rather than inventing a seventh orchestration layer. Use Binance Top20 fallback candidates with BTC/ETH pinned first, keep all generated paper orders behind `ExecutionGatekeeperService`, derive current open positions from per-symbol latest `PositionSnapshot`, and handle opposite signals conservatively by closing the existing paper position before any later re-entry.
- Consequences: The platform now has a test-covered autonomous paper cycle that can be scheduled repeatedly, observed, and extended toward testnet/live later without changing the core admission chain. This improves practical automation while remaining honest that the repo still does not prove full 7x24 production autonomy, long-lived scheduler supervision, or exchange-grade recovery loops yet.

## ADR-031: Tranche 1 uses single-tenant Bearer auth plus channel-group notification dispatch
- Date: 2026-07-04
- Status: accepted
- Context: The approved remaining-platform roadmap put `Tranche 1` on the critical path before deeper validation, live execution, or LLM agents. The repo still exposed every `/api/v1/*` route without authentication, and notification outbox only recorded intent without a real outbound delivery loop.
- Decision: Add a single-tenant static Bearer token gate for `/api/v1/*` with `/health` and `/api/v1/health` explicitly public, using `ADMIN_API_TOKEN` instead of introducing users, sessions, or RBAC. Upgrade notification outbox to a channel-group dispatcher that persists `delivery_channels`, retry timing, attempt history, and adapter results, with first-batch `telegram` and `webhook` adapters plus a shared API/Celery dispatch path.
- Consequences: The admin/API surface now has a minimal but real security baseline that matches the current single-operator deployment model, and Ops/Review/Risk notifications can move from stored intent to actual outbound delivery with retry auditability. Multi-user auth, RBAC, and Email delivery remain future tranches rather than being overbuilt into Phase 1.

## ADR-032: Promotion evidence is mandatory for Paper/Live, and live/runtime + LLM stay behind explicit self-owned boundaries
- Date: 2026-07-04
- Status: accepted
- Context: The remaining-platform roadmap explicitly required Tranche 2/3/4 to tighten promotion gates, add a self-owned execution runtime over a Binance-first gateway abstraction, and online-ize agents through a unified LLM boundary without letting any agent emit direct trade instructions. The repo had partial contracts and services, but key public routes and runtime seams still behaved like placeholders or legacy pass-throughs.
- Decision: Make `ValidationAdmissionService` the single promotion-evidence arbiter for both Paper and Live. `/api/v1/execution/paper-runs` and `/api/v1/execution/live-runs` now require hypothesis, benchmark/control, OOS, and pod-risk evidence rather than raw backtest pass alone; validation report APIs resolve the linked hypothesis before computing `promotion_gate`; live runtime APIs are exposed only through the existing Execution Layer over a self-owned `ExchangeGateway` seam; and Agent Layer online calls go through a structured Anthropic runtime that enforces JSON-only outputs plus per-agent provider/model mapping. Binance USDT perpetual is the first real gateway target, with `NullExchangeGateway` remaining the safe default when credentials are absent.
- Consequences: The platform now rejects legacy/under-evidenced strategies before Paper or Live promotion, and live operations can be audited through account snapshots, gateway order lifecycle, reconciliation records, and decision memory without bypassing the six-layer chain. Online LLM classification/veto is now possible when credentials are configured, but any schema failure or timeout still fails closed and no agent gains direct order authority.

## ADR-030: Notification outbox is persisted intent/state, not an external adapter
- Date: 2026-07-04
- Status: accepted
- Context: ADR-026 introduced notification outbox visibility, but the implementation still derived pending items from active high-severity risk events at read time. That made resolved events disappear from audit retrieval and gave Review/Ops no durable state for adapter attempts, manual operator notes, or later delivery-status reconciliation.
- Decision: Add a relational `notification_outbox` table, `NotificationRepository`, and persisted `/api/v1/notifications/outbox` APIs for list/filter, manual create, and delivery-result update. High/critical `RiskEvent` creation writes an idempotent `risk:{risk_event_id}` pending notification intent; low/mid events remain visible as risk events but do not auto-enqueue notifications. Keep actual Telegram/email/webhook delivery adapters out of scope.
- Consequences: Ops/Review/Risk now share a durable notification audit trail even after risk events are resolved, while the platform avoids introducing credentials, side effects, or a new notification-agent subsystem in this tranche. Real outbound adapters remain a separate P1 follow-up.

## ADR-029: Research-side alpha rejections reuse FailureRecord instead of a separate memory store
- Date: 2026-07-04
- Status: accepted
- Context: TASK-017 made local alpha intake preserve unsupported/evaluator rejection evidence, but the evidence was still mainly attached to `StrategyIdea` metadata/rationale. The platform requirement says Review Layer is a first-class module and failure knowledge should be reusable by Research/Strategy agents, so research-side rejections need the same persisted review path as execution rejections.
- Decision: Extend `FailureRecord` to reference either `strategy_id` or `idea_id`; persist `StrategyIdea.intake_metadata`; have Research Agent `scan_local_alpha` write `subjective_to_drop` alpha ideas into Review as `alpha_evaluator_reject`; and add `/api/v1/failures` filters for `strategy_id`, `idea_id`, and `failure_type`.
- Consequences: Alpha evaluator failures can be retrieved and clustered without reparsing free text, while the platform avoids adding a separate TradingAgents-style memory subsystem in this tranche. Strategy-level failure writeback remains unchanged for execution and validation failures.

## ADR-028: Risk admission and executable alpha intake must persist structured rejection evidence
- Date: 2026-07-03
- Status: accepted
- Context: The approved Phase 1 tranche required making the Risk Engine auditable at admission time and repairing the WorldQuant adapter so it can execute only a supported crypto-native subset. The previous seams either lacked runtime risk context (`ExecutionGatekeeperService`) or used placeholder alpha generation/evaluation that could silently drift away from later Review/Strategy reuse.
- Decision: Add `ExecutionRiskState` as a required runtime admission snapshot for direct execution requests and synthesize it during `paper-runs/{id}/step`. Persist `rejection_codes` and `evaluated_risk_state` on every `OrderExecution`, and route gatekeeper rejections into the existing Review writeback loop through `FailureRecord`. On the research side, keep WorldQuant as methodology-only, implement a strict executable subset (`ts_rank`, `ts_zscore`, `group_neutralize`, recursive arithmetic/function evaluation), use explicit crypto group alias migration, and tag unsupported or evaluator-rejected local alphas as `subjective_to_drop` with structured intake evidence instead of placeholder fallback signals.
- Consequences: Execution and Review now share a reusable rejection memory for risk failures, and local alpha intake can be clustered/reviewed later without re-parsing raw text. Direct order callers can no longer omit runtime risk context. Unsupported stock fundamentals remain research-only until manually ported to crypto-native inputs.

## ADR-027: Open-source strategy projects enter as E-level research assets, not executable shortcuts
- Date: 2026-07-03
- Status: accepted
- Context: The platform needs to absorb useful crypto strategy shapes, research frameworks, and LLM research/review workflows from Freqtrade, Jesse, Hummingbot, Lean, vn.py, ABU, TradingAgents, Vibe-Trading, Qbot, Superalgos, and related projects without turning into an ungoverned bot-code aggregator.
- Decision: Add `research_source/open_source_strategy_library` as a Data Layer E-level research intake module. Sources are registered as `StrategySourceManifest`, summarized into local RAG assets, and converted only into `StrategyIdea` / `StrategyDraft` seeds. External runtime code is not imported into execution services. GPL/AGPL sources are research references only. Paper stepping generates candidate `ExecutionOrderRequest` objects and always routes them through the existing `ExecutionGatekeeperService`.
- Consequences: The project can systematically mine mature open-source strategy knowledge while preserving the required chain: `StrategyIdea -> StrategyDraft -> Strategy -> BacktestRun -> PaperRun -> PaperOrder -> Gatekeeper`. Grid/market-making remains Paper-only in this tranche; live connector adoption or external framework runtime integration requires a separate risk/design decision.

## ADR-026: Remediation restores confidence through existing six-layer seams
- Date: 2026-07-03
- Status: accepted
- Context: The remediation plan required restoring engineering trust, adding real validation closure, and improving Paper/ops visibility while preserving the six-layer architecture and keeping AI out of direct order generation.
- Decision: Fix local quality gates first, then implement the first validation/ops slices inside existing layers: carry walk-forward/OOS/stress diagnostics in Validation, exchange capability registry in Data/Execution boundary, notification outbox in Ops/Review/Risk, and deterministic Decision Veto/Review executors in Agent Layer. Do not introduce a seventh layer, new dependencies, live order placement, or LLM-driven orders.
- Consequences: The platform now has better evidence for whether a carry strategy can approach Paper admission, and operational gaps are visible through explicit endpoints/outbox items instead of hidden TODO echoes. Full Deflated Sharpe, real notification adapters, Docker runtime verification, and live execution remain future work.

## ADR-025: Binance public market ingestion fills the Paper console before live execution
- Date: 2026-07-03
- Status: accepted
- Context: The Paper console already queried `/api/v1/market/*`, but the underlying `ohlcv_bars` and `market_extras` tables had no real collector writing Binance data, causing explicit empty states. The user asked to implement the Data Layer first tranche without expanding into live orders, account sync, alerts, or LLM veto.
- Decision: Implement Binance public market data ingestion only: CCXT REST OHLCV/funding backfill, idempotent writes keyed by market-data timestamps, and Binance WS collector seams that persist closed Kline candles/funding updates. Keep frontend polling existing read APIs and add only a Vite API proxy for development.
- Consequences: The research loop gains real A-level market data for validation and Paper console visibility. Live execution autonomy, account reconciliation, notifications, news/social data, and frontend push remain separate future tranches with stricter risk controls.

## ADR-023: Phase 1 grounding prioritizes real metrics and defers WorldQuant alpha semantics
- Date: 2026-07-03
- Status: accepted
- Context: The repo had documented services/data and carry validation as implemented, but the concrete risks were missing data-layer source control coverage, hardcoded carry metrics/costs, incomplete ensemble service behavior, and technical-rule gaps. The user also explicitly instructed that WorldQuant alpha work should be deferred for now.
- Decision: Restore the data layer and fix ignore rules first; move LLM deps to an optional extra; replace carry placeholder metrics with calculated net metrics and cost breakdown; implement deterministic SignalEnsemble/MetaLabel service/API and only MACD + Dow technical rules in this phase. Do not implement WorldQuant alpha semantic evaluator in this task.
- Consequences: Carry samples with negative net expectancy are rejected rather than marked conditional. WorldQuant remains a research intake source, not executable factor logic. Docker/Git operations remain manual follow-up items in this workspace because Docker is not installed and the folder is not a Git repository.

## ADR-024: Paper console ships before live exchange autonomy
- Date: 2026-07-03
- Status: accepted
- Context: The user asked for a frontend that shows real-exchange Klines, orders, positions, funding carry state, and user-facing operations. The backend still lacks real WebSocket ingestion, exchange account sync, and live order execution.
- Decision: Implement a Paper-first, Binance-first console over persisted data and current repository APIs. Add read aggregation endpoints and small status-control endpoints, but do not add real live order placement/cancel or account sync in this tranche.
- Consequences: The frontend is now a usable operational surface for persisted/paper data and explicit empty/error states. Real-time Binance streams and live trading controls remain a separate next phase with stricter risk and connector work.

## ADR-001: 研究报告作为主架构真源

- Date: 2026-06-28
- Status: accepted
- Context: 用户明确要求严格按研究报告实现整体架构与闭环。
- Decision: 将 `AI_Quant_Research_Platform_完整报告.docx` 作为主真源，并在 `AGENTS.md` 中固化为仓库级约束。
- Consequences: 后续实现不能擅自简化为单策略工具或以 WorldQuant 为主架构中心。

## ADR-002: 先做治理层，再做实现层

- Date: 2026-06-28
- Status: accepted
- Context: 当前工作区基本空白，直接写业务代码容易偏离报告。
- Decision: 先建立 `AGENTS.md`、项目记忆、项目元数据与基础配置，再继续仓库骨架和代码实现。
- Consequences: 后续每轮开发都以同一套记忆与约束为准，降低架构漂移。

## ADR-003: 第一阶段主市场为 BTC/USDT 永续，但模型支持多市场

- Date: 2026-06-28
- Status: accepted
- Context: 报告要求先从加密单品种深耕，但未来扩展到多资产。
- Decision: 领域模型从第一天起设计为多市场兼容，当前主流程围绕 `BTC/USDT perpetual`。
- Consequences: 不允许把市场、交易所、标的硬编码成不可扩展结构。

## ADR-004: 平台总设计包作为仓库内第二层实施母文档

- Date: 2026-06-28
- Status: accepted
- Context: 当前仓库已有治理层与骨架层，但缺少能直接指导后续子设计与实现的母文档。
- Decision: 新增 `docs/architecture/platform-master-design.md` 及 3 个附录，作为研究报告之下、子设计文档之上的总设计包。
- Consequences: 后续领域模型、接口、数据接入、Agent 编排、执行/风控/复盘设计都必须引用此母文档，不得各自重新定义平台边界。

## ADR-005: 领域模型围绕研究闭环而非单纯策略代码或订单系统建模

- Date: 2026-06-28
- Status: accepted
- Context: 下一版项目完善需要明确统一领域模型、接口簇和任务编排骨架。
- Decision: 领域与接口设计包以 `StrategyIdea -> StrategyDraft -> Strategy -> BacktestRun -> PaperRun -> LiveRun -> ReviewReport -> FailureRecord` 为主链路，确保研究入口、验证运行、执行事实、风险事件和复盘知识都是一等对象。
- Consequences: 后续 FastAPI、任务流、数据库和 Agent 编排都应围绕这条主链路组织，不得退化为“只管策略代码”和“只管订单执行”的局部系统。

## ADR-006: 前期准备阶段先补齐完整设计包，再进入业务实现

- Date: 2026-06-28
- Status: accepted
- Context: 用户要求把剩余文件和内容尽量全部开始进行，完成项目的前期准备工作，且要求足够详细具体。
- Decision: 在实现业务代码前，先补齐数据与接入、Agent 与任务编排、执行/风控/复盘、产品规格、功能清单、路线图、环境配置与交付清单等设计资产。
- Consequences: 仓库在进入开发实现前将具备较完整的项目启动包，减少后续反复返工和架构漂移。

## ADR-007: 用 TimescaleDB 替换 PostgreSQL，并拆分存储归属

- Date: 2026-06-29
- Status: accepted
- Context: v2.0 PDF 要求时序数据（K线/funding/OI）用 TimescaleDB 超表，查询性能提升 10-100 倍。
- Decision: docker-compose 的 `postgres` 服务改为钉版 `timescale/timescaledb:2.17.2-pg16`。存储归属拆分：`infra/timescale/init.sql` 拥有时序/事件表（ohlcv_bars/market_extras/risk_events/macro_events），Alembic 拥有关系表（strategies 等），任何表不得两边创建。Order Book 只进 Redis 不落库。
- Consequences: 数据库连接串改用 `postgresql+psycopg://`；init.sql 必须幂等。

## ADR-008: 数据契约优先，统一在 shared/models 定义

- Date: 2026-06-29
- Status: accepted
- Context: PDF 原则二要求所有框架输出在进入 services 前转换为内部统一 Pydantic 模型；domain 文档与 PDF 存在命名漂移。
- Decision: 新建 `shared/models/` 作为唯一跨层契约源，任何 service 不得各自定义。命名裁决：`BacktestReport`（引擎指标载荷）= 领域对象 `BacktestRun.metrics_summary`，两名并存；`RiskEvent` 采用 domain 超集（event_type/severity/affected_scope/resolution_status），PDF `level`→`severity`，DB 表存子集投影。
- Consequences: SQLAlchemy ORM 视为存储细节而非契约，须与 `StrategyContract` 双向映射。

## ADR-009: WorldQuant 本地 alpha 库移植方法论而非搬运表达式

- Date: 2026-06-29
- Status: accepted
- Context: 本地 `Desktop/alpha` 是成熟的 WorldQuant **美股**挖掘流水线（~67万表达式），基于基本面字段，不能直接套用到 BTC/USDT 永续。
- Decision: `research_source/worldquant_adapter/` 只移植算子词表与因子构造方法（纯 pandas/numpy），重新生成加密因子；不导入美股原始表达式。`.env` 用 `WORLDQUANT_ALPHA_LOCAL_PATH` 引用本地路径，不上传 Brain session。
- Consequences: 符合 AGENTS.md 不可谈判项 #5（WorldQuant 是来源非主干）；该模块不被 apps/api import。

## ADR-019: 本地控制台读缓存，自动调度进程独立

- Date: 2026-07-12
- Status: accepted
- Context: Windows 本地环境中，将 Binance 外部请求放在 FastAPI 读取接口会使交易台多个请求超时；内嵌调度也会让 API 监听后失去响应。
- Decision: API 通过 `--local-console` 仅读取已落库的市场、订单、仓位和账户快照；`run-local-paper-scheduler.py` 独立执行自动 Paper 周期与行情刷新，并以 `scheduler.pid` 提供状态。默认 API 端口改为 `8016`。
- Consequences: 币安无网络或代理不可用时，交易台仍显示本地记录与明确中文降级状态；外部账户同步必须是按需操作，不能阻塞总览。

## ADR-010: 保留 setuptools 构建后端，依赖锁定用 uv；指标库用 pandas-ta

- Date: 2026-06-29
- Status: accepted
- Context: 需要可复现依赖，但不想迁移构建后端；TA-Lib 需系统 C 库，Windows/slim 容器安装困难。
- Decision: 保留 PEP621 + setuptools，仅引入 `uv lock`（uv 直读 pyproject）生成 `uv.lock`；指标库用纯 Python 的 pandas-ta，TA-Lib 仅注释可选。WorldQuant 算子移植为纯 pandas/numpy，不在 Phase0 关键路径依赖 TA-Lib。
- Consequences: `make lock/sync` 需安装 uv；首次锁定需在装有 uv 的环境执行。

## ADR-011: 资金费率/基差套利定为 Phase 1 第一个落地策略

- Date: 2026-07-02
- Status: accepted
- Context: 用户基于外部量化建议反馈，指出方向性技术策略确定性低，应先用波动率无关/方向无关的资金费率套利把 Validation Layer 全流程（回测→样本外→模拟盘）跑稳，再叠加更冒险的策略。
- Decision: `appendix-b-feature-phasing.md` P1 明确排序：资金费率/基差套利策略优先落地并验收，验收通过后才进入技术策略框架化（缠论/道氏等）与信号融合接入。
- Consequences: P1 任务拆解与验收标准需围绕这一策略先行设计；技术策略框架化的启动时间点依赖前一项验收结果，不并行抢跑。

## ADR-012: 信号融合与二级仓位判定归属 Strategy Layer 子模块，不新增第7层

- Date: 2026-07-02
- Status: accepted
- Context: 用户反馈建议引入"信号融合"与"meta-labeling 二级过滤"（一级信号只回答方向，二级轻量模型用三重界限法判定是否下注/下多大仓位）。需要决定是否新增独立架构层。
- Decision: 新增 `SignalEnsemble`（信号融合结果）与 `MetaLabel`（二级仓位判定）两个领域对象，归属 Strategy Layer 下的子模块（`services/strategy_library/ensemble/`），六层架构本身不变。主链路调整为 `...StrategyCodeArtifact -> SignalEnsemble -> MetaLabel -> BacktestRun...`。
- Consequences: 回测评估对象从"单策略信号"变为"融合后的候选交易"；`Strategy` ORM 表结构不受影响，新对象走独立契约（`shared/models/signal.py`），不污染 `strategies` 表。

## ADR-013: LLM 只承担一票否决与复盘生成，否决时机限定在信号融合之后、执行之前

- Date: 2026-07-02
- Status: accepted
- Context: 用户反馈强调 LLM 在杠杆环境下直接输出买卖指令的幻觉代价过大，应改为"否决与复盘"角色，且新闻/消息面数据应作为过滤器而非开单触发器。这与既有 AGENTS.md"AI 是研究员不是交易员"原则一致，但缺少具体机制。
- Decision: 新增 `Decision Veto Agent`（`agent-and-orchestration-design.md` 2.11），输入 `SignalEnsemble`/`MetaLabel`/近期 `RiskEvent`，只输出 `veto: bool + veto_reason`，不得输出方向/仓位/价格；否决为 true 的信号不得进入 `ExecutionSignal`。新闻分级触发规则写入 `execution-risk-review-design.md` §2.2a：高严重度暂停开仓 N 分钟（可配置）+ 止损收紧，中/低严重度仅记录不触发。
- Consequences: Execution Layer 前置检查新增两条：信号必须已完成二级仓位判定（`bet_taken`）且未被否决（`veto=false`），才能生成 `ExecutionSignal`。

## ADR-014: 验证方法论与成本建模纳入 BacktestRun/BacktestReport 契约

- Date: 2026-07-02
- Status: accepted
- Context: 用户反馈指出回测若不做 walk-forward、不做多重检验偏差校正（Deflated Sharpe）、不覆盖极端压力场景、不建模真实成本（手续费/滑点/资金费率净收支），回测结果是自我欺骗。现有 `BacktestRun` 只有字段占位，没有方法论文档。
- Decision: 新增子设计文档 `docs/architecture/validation-methodology.md`，定义 walk-forward 滚动窗口规则、Deflated Sharpe 校正要求（配套 `trials_count`/`deflated_sharpe` 字段）、压力测试场景库（LUNA崩盘/312/交易所宕机/极端插针）、三项成本建模口径（maker/taker费率、订单簿深度滑点、资金费率净收支，统一用 `total_cost_bps`）。`BacktestRun`（domain doc）与 `BacktestReport`（`shared/models/backtest.py`）契约同步补充对应字段，均为可选字段，不破坏现有测试。
- Consequences: 模拟盘准入判断口径调整为优先看 `deflated_sharpe` 而非原始 Sharpe，且收益指标必须是扣除成本后的净值；具体计算公式与滑点估算算法留给 Phase 1 实现，Phase 0 只固定契约与方法论边界。

## ADR-015: LLM 只承担否决/分类/复盘角色，永不直接输出交易指令——写入独立方案文档并覆盖全部 Agent

- Date: 2026-07-02
- Status: accepted
- Context: 用户要求为"接入 LLM API 辅助完成功能、整体分析"单独产出设计方案（只做设计不写代码）。需要把 ADR-013 已确定的 Decision Veto Agent 边界，扩展到覆盖 News/Twitter/Telegram/Research/Review 等全部会调用 LLM 的 Agent，避免各 Agent 各自发明使用边界。
- Decision: 新增 `docs/architecture/llm-integration-plan.md`，逐 Agent 定义允许用途与禁止清单（核心禁止项：任何 Agent 都不得输出方向/价格/仓位；News/Twitter/Telegram 的分类结果必须经过既有 `RiskEvent` 分级流程才能影响执行，不得直接触发下单）；四段式 Prompt 模板（SYSTEM/CONTEXT/TASK/OUTPUT SCHEMA）；LLM 输出必须过 Pydantic 校验才能落地，不接受宽松部分解析；LangChain/LlamaIndex 的 RAG 范围限定为 Research Agent 的 E 级检索，明确排除任何执行路径 Agent。
- Consequences: `CLAUDE_MODEL` 建议改为按 Agent 类型可配置而非单一全局环境变量（P1 任务）；LLM 不可用时禁止自动放宽风控阈值或静默降级为本地规则引擎而不打标签，与"风控优先于收益"直接挂钩。

## ADR-016: 24 小时运行的可靠性分三层监督，故障响应为"自动降级、人工恢复"，仅两类例外可自动恢复

- Date: 2026-07-02
- Status: accepted
- Context: 用户要求为 7x24 自动实时交易单独产出运行方案。现有设计文档未定义进程崩溃/连接静默断连/数据缺口等运行时故障的检测与响应机制，通知/告警层此前在多处文档中被标记为缺口但从未定义触发规则。
- Decision: 新增 `docs/architecture/24x7-operations-plan.md`，分进程层/连接层/数据层三层监督；连接层采用应用层心跳（不依赖 socket 状态）+ 指数退避带抖动重连；故障响应对齐 `execution-risk-review-design.md` 已有的风控结果枚举（`pause_strategy`/`pause_account`/`hard_stop`），默认"自动降级、人工恢复"，仅"高严重度新闻事件计时结束"与"数据中断已恢复且新鲜度确认通过"两种客观可验证场景允许自动恢复；定义"只允许平仓"模式的具体行为边界（不得撤销已有止损）；定义通知告警三级触发规则。
- Consequences: 该文档成为风控措施与保障方案（ADR-018）裁决具体熔断时长/阈值时的运行时行为基础，两文档不重复定义触发动作分类，只有取值和最终裁决在风控文档。

## ADR-017: 外部数据源接入的具体 API/SDK/频率选型独立成文，不并入抽象的数据接入设计

- Date: 2026-07-02
- Status: accepted
- Context: `data-and-ingestion-design.md` 有意保持抽象，不点名具体供应商与轮询频率。用户要求的开发前方案包需要"外部数据源、信息源的接入"具体到可执行程度，且原文档的抽象定位不应被破坏（避免未来供应商变更导致要重写架构文档）。
- Decision: 新增 `docs/architecture/external-data-source-integration-plan.md`，逐级（A/B/C/D/E）给出具体 API/SDK 选型（CCXT/ForexFactory RSS/Trading Economics/金十+CoinDesk+TheBlock+SEC EDGAR RSS/Twitter API v2+Telegram Bot API/GitHub+arXiv API）与拉取频率原则；`IngestionJob.source_family`/`job_type`/`schedule_mode`/`job_status` 建议枚举取值；原始数据 P0/P1 阶段落文件存储、P2 迁移对象存储的路径规划；明确 Reddit（PRAW）/YouTube Data API 是当前真实缺口，定为 P2；澄清 Telegram 三重角色（D级采集/E级采集可共用 Token，运维出站告警必须用不同 Token）。
- Consequences: 供应商/SDK/频率属于本文件而非 `data-and-ingestion-design.md` 的维护范围，未来更换数据供应商只需修订本文件；`IngestionJob` 尚未代码化，枚举取值是 P1 实现时的建议值而非已落地约束。

## ADR-018: 风控措施与保障方案裁决四项此前留白的风险容忍度选择

- Date: 2026-07-02
- Status: accepted
- Context: `llm-integration-plan.md` §3.2、`execution-risk-review-design.md` §2.2a、`technical-architecture-plan.md` §8.3、`domain-and-interfaces-design.md` §3.13 均把具体取值/选择显式留白，交给风控方案文档裁决。用户要求的开发前方案包必须包含"风控措施和保障"维度，且这些留白必须在进入开发前有明确答案，不能带着未裁决的分支进入实现阶段。
- Decision: 新增 `docs/architecture/risk-control-and-safeguards-plan.md`：(1) LLM 否决超时选"超时即否决"（选项 A），理由是选项 B 在市场剧烈波动时可能恰好放行风险最高的信号，与风控优先级原则冲突；(2) `paper`/`live` 环境交易所 Key 启动期强制自检提现权限，若检测到提现权限则拒绝启动而非仅告警，`dev`/`test` 环境按"是否涉及真实资金"为界例外；(3) 给出 `RiskProfile` 全字段针对 BTC/USDT 永续第一阶段的具体默认阈值表（单笔1%/单品种20%/总仓位60%/最大杠杆3倍/当日3%/单周8%/回撤10%触发收紧/回撤20%硬停止），明确扩展市场不可直接复用；(4) 具体化熔断触发条件表，且明确"自动降级、人工恢复"为默认原则，仅两类客观可验证场景例外允许自动恢复（与 ADR-016 一致）。
- Consequences: 后续如需修改这四项裁决的具体数值/选择，应作为对本文件的修订，不得绕开本文件直接改动被引用的源文档；`RiskProfile` 阈值表是 P1 代码化时的默认配置，运行时仍需可配置，不是硬编码常量。

## ADR-019: 用单独索引文档固定真源层级、产品文档分层与 Phase 语义

- Date: 2026-07-02
- Status: accepted
- Context: 仓库在第二轮设计补强后已同时存在主报告、母文档、专项方案、旧版产品文档与新版 PRD/模块清单。若继续只靠 README 或记忆文件口头说明，会再次出现“哪份文档说了算”“Phase 0 和 P0/P1 是否同义”的理解漂移。
- Decision: 新增 `docs/architecture/design-source-index.md` 作为开发入口索引，固定真源层级为“研究报告 > AGENTS.md > 母文档 > 第二轮专项方案 > 产品细化文档 > README/记忆索引”；明确 `product-spec.md` + `feature-catalog.md` 是上层定位与总表，`prd.md` + `module-feature-catalog.md` 是开发验收真源；明确当前仓库整体仍处于 `Phase 0`（平台骨架 + 统一模型 + 设计冻结），而 `appendix-b-feature-phasing.md` 中的 `P0/P1/P2` 只是实现 tranche 标签。
- Consequences: 后续任何新增文档都必须先放入该索引的层级体系，再决定它是否有裁决权；README、`report-alignment.md`、记忆文件只能同步入口和现状，不得绕过该索引重新发明真源链。
## ADR-020: The first executable slice expands into Binance Top20 ingestion plus BTC/ETH-first paper preparation
- Date: 2026-07-02
- Status: accepted
- Context: After the design convergence pass, the user confirmed that the first implementation tranche should no longer stop strictly before paper trading. The tranche must now cover Binance top-universe ingestion metadata and a simulated paper-run preparation path, while still keeping live execution, WebSocket streaming, and LLM veto outside this code drop.
- Decision: Keep the first real exchange integration Binance-only, but widen the scope to include persisted `IngestionJob`, `BacktestRun`, and `PaperRun` objects; a default Binance `Top20` quote-volume universe fallback for ingestion jobs; and a paper-run orchestration default that always prioritizes `BTC/USDT` and `ETH/USDT` as the first simulated symbols. `GateDecision` is extended to explicit `accepted` / `conditional` / `rejected_with_reason` statuses so the paper-preparation boundary can be expressed without overloading a boolean.
- Consequences: The API and repository implementation must cover `StrategyIdea -> StrategyDraft -> Strategy -> StrategyVersion -> BacktestRun -> GateDecision -> PaperRun`; Celery gets first real ingestion/backtest/paper task entrypoints; Alembic must manage all relational tables for this slice; and later Binance live collectors can replace the fallback universe source without changing the public contract shape.

## ADR-021: Persisted carry backtests execute through an application service over timeseries repositories
- Date: 2026-07-02
- Status: accepted
- Context: After the first persisted slice landed, the remaining gap was that backtests could only be created from already-built payloads. The project still lacked a real application flow that reads persisted `ohlcv_bars` / `market_extras`, evaluates data quality, runs carry validation, and writes the resulting `BacktestRun`.
- Decision: Add a dedicated `CarryBacktestApplicationService` and `CarryBacktestRequest` contract. The service reads Binance spot/perp/funding history from a `DataRepository`, uses settlement-window cadence for the carry lane's gap check, runs the existing carry backtest engine, enriches `validation_methodology` with persisted data-quality evidence, and persists the run through `ValidationRepository`. Expose the flow through `/backtests/carry` and a matching `enqueue_carry_backtest` Celery task rather than overloading generic CRUD endpoints.
- Consequences: The verified backtest chain now includes persisted market data as an explicit dependency. Future Freqtrade/VectorBT adapters can reuse the same application boundary, and later live exchange collectors can feed the same repository contract without changing the API shape.

## ADR-022: The first executable P1 expansion uses versioned `/api/v1` envelopes plus persisted gatekeeper seams
- Date: 2026-07-02
- Status: accepted
- Context: After TASK-010, the repository still mixed old unversioned skeleton routes, direct final-object submission patterns, and in-memory `risk/review/runs` seams. This made it too easy to bypass validation and impossible to reason about a single public API style.
- Decision: Move the public surface to `/api/v1`, standardize list responses to `items + total`, standardize error payloads to `error_code/message/detail`, and convert asynchronous-style creation paths (`backtests`, `optimizations`, `ingestion jobs`, `agent tasks`, `paper runs`) to `TaskSubmission` acknowledgements. Persist `RiskProfile`, `RiskEvent`, `ReviewReport`, `FailureRecord`, `AgentTask`, `LiveRun`, `OrderExecution`, and `PositionSnapshot`; add an `ExecutionGatekeeperService` that blocks orders or paper admission when stoploss, validation, freshness, veto, or active high-severity risk-event checks fail.
- Consequences: The research loop is now auditable across Strategy -> Validation -> Risk/Execution -> Review without relying on in-memory dictionaries. Future Celery/Llive wiring can extend the same contracts instead of replacing them, and frontend/admin can target a stable versioned API surface.
