# Directional Candidate Throughput Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the armed BTC/ETH Binance Simulation directional lane produce auditable exchange-bound candidates when the validated primary strategy is silent, without changing fixed position, leverage, stop-loss, take-profit, portfolio-risk, or mainnet guards.

**Architecture:** Keep `trend_momentum_v1` as the validated primary decision. Add a Testnet-only sampling fallback based on the existing `operator_heuristic_v2_relaxed` candidate, implement its documented entry-plus-one-higher-timeframe confirmation semantics, and tag every fallback decision separately. Synchronize the runtime ConfigSnapshot to the selected manifest at bootstrap so the running strategy cannot silently use stale persisted rules. Prove the complete production path with deterministic BTC long and ETH short market fixtures and a strict Binance-like fake gateway that returns exchange order IDs and fills.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy, Pydantic, pytest, CCXT-compatible gateway interfaces.

## Global Constraints

- Binance USDT-M Testnet / Simulation remains the execution authority; local SQLite is a post-fill projection.
- Automatic execution symbols remain exactly `BTC/USDT` and `ETH/USDT`.
- Do not change fixed position, leverage, stop-loss, take-profit, cost, net-edge threshold values, or mainnet guards.
- The fallback is allowed only for an armed `binance_simulation_first` run with `BINANCE_USE_TESTNET=true`, `LIVE_TRADING_ENABLED=false`, and `BINANCE_AUTO_EXECUTE=true`.
- Primary and fallback results must be attributable by candidate ID and decision variant.
- No acceptance/canary order may count as strategy proof.

---

### Task 1: Lock runtime rules to the active manifest

**Files:**
- Modify: `services/execution/bootstrap.py`
- Test: `tests/services/test_paper_bootstrap.py`

- [ ] Add a failing test where the active ConfigSnapshot contains stale strategy rules and bootstrap stages the current manifest rules while preserving exchange authorization and fixed execution settings.
- [ ] Run the focused test and confirm it fails for stale rules remaining active/pending.
- [ ] Implement minimal runtime snapshot synchronization using the existing immutable ConfigSnapshot repository.
- [ ] Verify the focused bootstrap tests pass.

### Task 2: Implement the documented relaxed multi-timeframe policy

**Files:**
- Modify: `services/execution/decision_pipeline.py`
- Modify: `services/strategy_library/candidates/registry.py`
- Test: `tests/services/test_decision_pipeline_runtime.py`

- [ ] Add failing tests proving `operator_heuristic_v2_relaxed` admits an entry when the 15m trigger agrees with either 1h or 4h, and rejects when neither higher timeframe agrees.
- [ ] Run tests and confirm strict current behavior fails the first case.
- [ ] Add explicit `mtf_confirmation_mode="entry_plus_one_higher"` to the existing v2 candidate and implement it without changing strict candidates.
- [ ] Verify strict and relaxed policy tests pass.

### Task 3: Add a Testnet-only directional sampling fallback

**Files:**
- Modify: `services/execution/paper_signal.py`
- Modify: `services/execution/decision_pipeline.py`
- Modify: `services/execution/net_edge.py`
- Modify: `services/execution/bootstrap.py`
- Test: `tests/services/test_decision_pipeline_runtime.py`
- Test: `tests/services/test_execution_gatekeeper.py`

- [ ] Add failing tests showing an armed directional run uses the primary candidate first, then evaluates the existing relaxed candidate only for candidate-starvation reasons.
- [ ] Add failing tests proving fallback cannot run in local-only Paper or mainnet mode.
- [ ] Add failing tests proving Testnet sampling candidates remain tagged and bypass only the pre-validation edge requirement, not stop-loss, position, leverage, portfolio, or daily-loss gates.
- [ ] Implement the minimal fallback and trace tags.
- [ ] Verify focused decision and Gatekeeper tests pass.

### Task 4: Prove candidate-to-exchange-to-local projection

**Files:**
- Create: `tests/integration/test_directional_exchange_first_e2e.py`
- Create: `scripts/verify_directional_exchange_first.py`

- [ ] Write deterministic BTC-long and ETH-short fixtures using production `PaperCycleOrchestrator`, repositories, Gatekeeper, context builder, and a strict CCXT-compatible fake Binance gateway.
- [ ] Confirm tests fail before the fallback is wired through the orchestrator.
- [ ] Implement only the missing integration wiring.
- [ ] Assert one exchange `create_order` call, non-null gateway order ID, exchange fill price/quantity, and matching local position projection for each direction.
- [ ] Run the standalone verifier and produce a JSON proof artifact.

### Task 5: Verification, memory, and packaging

**Files:**
- Modify: `CURRENT_STATE.md`
- Modify: `.github/agent/memory/project-memory.md`
- Modify: `.github/agent/memory/decisions-log.md`
- Modify: `.github/agent/memory/task-history.md`
- Create: `artifacts/verification/directional-exchange-first-proof.json`

- [ ] Run focused decision, Gatekeeper, bootstrap, order-context, position, and E2E tests.
- [ ] Run Python compile checks.
- [ ] Run full pytest and compare failures with the unchanged baseline/environment failures.
- [ ] Attempt Ruff and Mypy; report tool/dependency blockers exactly if unavailable.
- [ ] Re-read every modified critical code path and record self-review results.
- [ ] Package a new ZIP without caches, local secrets, or transient databases and compute SHA-256.
