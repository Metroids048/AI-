# P1 Same-Cycle Research Shadow Implementation Plan

> **For agentic workers:** Execute inline in the current checkout. Do not create or switch a branch/worktree. Use test-driven development for every production change, then use `verify-work` and one independent read-only review before reporting `IMPLEMENTATION_COMPLETE_PENDING_REVIEW`.

**Goal:** Attach the three existing research-only candidates to every real V2 ACTIVE scheduler symbol-cycle as same-bar, evidence-only counterfactual observers while preserving `testnet_sampling_v2` as the sole intent and exchange writer.

**Architecture:** Extend the existing `execute_v2_automated_trading_cycles()` orchestration only after the ACTIVE cycle has returned, so research evaluation cannot delay or alter ACTIVE decision, risk, intent, submission, projection, protection, exit, or reconciliation behavior. Evaluate the immutable research pipeline from the ACTIVE symbol and closed 15m bar, persist a structured evidence envelope inside the existing `V2ExecutionDecision.payload`, and isolate each candidate exception inside the pure proposal pipeline. No new scheduler, writer, execution service, table, or migration is introduced.

**Tech Stack:** Python 3.11+, Pydantic immutable contracts, SQLAlchemy/SQLite existing V2 facts, pytest, Ruff, mypy.

---

## Root Cause And Boundaries

`P1_ROOT_CAUSE`: the existing proposal pipeline is called by the real scheduler only when `EngineActivation.SHADOW`; `EngineActivation.ACTIVE` runs `testnet_sampling_v2` but persists no same-cycle research-candidate evidence. Consequently prior research results cannot be matched to the ACTIVE decision on the same symbol and closed bar.

This plan observes existing strategy behavior only. It does not promote, optimize, tune, or execute any research candidate and does not start P2.

## ACTIVE_PATH_PROOF

| Segment | Current code path | Actual runtime condition |
|---|---|---|
| USER_ENTRY | `一键启动.cmd:15` | Always invokes the launcher with `v2_active`, `-EnableNaturalTestnet`, and `-PreserveExternalTestnetBaseline`. |
| LAUNCHER | `scripts/launch-paper-console.ps1:240` `Ensure-Runtime`; scheduler start at `:415` | Sets Testnet true, mainnet false, resolves `AUTOMATED_TRADING_ENGINE=v2_active`, captures external baseline, and fails fast unless the ACTIVE contract passes. |
| SCHEDULER PROCESS | `scripts/run-local-paper-scheduler.py:27` `run_scheduler` | Bootstraps the local runtime and starts one `RuntimeScheduler` with the requested engine. |
| SCHEDULER | `services/execution/scheduler.py:220` `RuntimeScheduler.start`; `:786` `_default_v2_automated_trading_runner` | ACTIVE registers only `automated_trading_v2_cycle`; `legacy_writer_enabled=false`; provenance contains `scheduler_instance_id` and scheduler coordination identity. |
| V2 JOB | `services/execution/tasks.py:348` `run_v2_automated_trading_cycles` | Formal scheduler/Celery entry delegates to the V2 bridge. |
| CYCLE RUNNER | `services/execution/v2_scheduler_entry.py:265` `execute_v2_automated_trading_cycles` | Acquires the global V2 writer lease, creates one `cycle_id` per BTC/ETH symbol, loads the closed 15m view, and invokes `run_automated_trading_cycle`. |
| ACTIVE DECISION | `services/automated_trading/application/cycle_service.py:1503` `run_automated_trading_cycle`; `services/automated_trading/application/decision_service.py:337` `evaluate_symbol` | Fixed `CandidateLane.TESTNET_SAMPLING`, `strategy_id=testnet_sampling_v2`, `strategy_version=1.0.0`; strategy rules remain untouched. |
| ENTRY GATE / RISK | `cycle_service.py:1582`; `services/automated_trading/application/entry_service.py:109` `evaluate_entry` | Reconciliation, entry control, external baseline direction, open position, risk, expiry and drift gates run before intent. `POSITION_ALREADY_OPEN` terminates ACTIVE entry only. |
| INTENT | `cycle_service.py:1703`; `fact_persistence.py:55` `persist_entry_intent_before_submission` | Created only after ACTIVE risk and drift approval and only durably persisted when `persist_facts=true`. |
| EXCHANGE SUBMIT | `cycle_service.py:1746`; `entry_service.py:318` `execute_entry` | Submission occurs only for ACTIVE, approved, non-research Testnet sampling candidates. |
| LOCAL PROJECTION | `cycle_service.py:1781`; `fact_persistence.py:182` `persist_entry_and_protection` | Position facts are written only after a real exchange order ID and confirmed fills. |
| PROTECTION | `cycle_service.py:1803`; `protection_service.py:161` and `:229` | Stop/target are derived from confirmed fill price and submitted through the existing protection escalation contract. |

Result: `ACTIVE_PATH_PROOF=PASS`. The observer hook belongs in `v2_scheduler_entry.py`, after the ACTIVE cycle result and before existing decision finalization.

## RESEARCH_PATH_PROOF

| Candidate | Implementation / entry point | Inputs and timeframe | Current runtime path | Authority / evidence / alignment |
|---|---|---|---|---|
| `trend_pullback_v2` | `services/strategy_library/candidates/trend_pullback_v2.py:121` `evaluate_trend_pullback_v2` | Closed 15m OHLCV/volume/ATR plus 15m/1h/4h regime evidence; no 5m decision dependency | Called by `run_proposal_pipeline`; real scheduler calls it only under V2 SHADOW; offline replay also calls it | Registry is `RESEARCH_ONLY`, `execution_eligible=false`; no intent/submit dependency. Existing evidence is nested SHADOW decision JSON. Cannot currently align with ACTIVE because activation modes are mutually exclusive. |
| `range_sweep_reversion_v1` | `services/strategy_library/candidates/range_sweep_reversion_v1.py:107` `evaluate_range_sweep_reversion` | Closed 15m Donchian/reversal bars, volume/ATR and 15m/1h/4h regime evidence; no 5m decision dependency | Same proposal pipeline, V2 SHADOW only in real scheduler, plus offline replay | Same research-only/no-authority boundary and current evidence limitation. |
| `failed_breakout_reversal_v1` | `services/strategy_library/candidates/failed_breakout_reversal_v1.py:173` `evaluate_failed_breakout_reversal` | Closed 15m sweep/confirmation bars, volume/ATR and 15m/1h/4h regime evidence; no 5m decision dependency | Same proposal pipeline, V2 SHADOW only in real scheduler, plus offline replay | Same research-only/no-authority boundary and current evidence limitation. |

`trend_momentum_v2_enriched` is absent from `proposal_pipeline._evaluators()` and is therefore `OUT_OF_SCOPE`.

Result: all three requested candidates are runtime-ready; `RESEARCH_PATH_PROOF=PASS`.

## Frozen File Scope

### MUST_CHANGE

- `services/execution/v2_scheduler_entry.py`: the direct observer hook on the real V2 symbol-cycle, same-cycle evidence envelope, alignment validation, and framework-level failure isolation.
- `services/strategy_library/proposal_pipeline.py`: isolate one candidate exception without losing the other candidate observations, and expose stable candidate version/error metadata to the observer.
- `tests/integration/test_v2_scheduler_entry_fact_chain.py`: P1 same-cycle, `POSITION_ALREADY_OPEN`, zero-authority, ACTIVE-unchanged, framework isolation, and no-legacy-writer integration contracts.
- `tests/services/strategy_library/test_proposal_pipeline.py`: per-candidate `SHADOW_STRATEGY_ERROR` isolation contract.

### MAY_CHANGE

- `scripts/verify_p1_same_cycle_research_shadow.py` and its focused test: only if needed to query the existing database read-only for real before/after counts, same-cycle matches, and the mutation ledger without exposing payloads through a new API.
- `.github/agent/memory/project-memory.md`, `.github/agent/memory/task-history.md`, and the mandated project session record: completion memory only.

### CONDITIONAL_CHANGE

- No database model or migration. The existing `V2ExecutionCycle` plus `V2ExecutionDecision.payload` already expresses cycle, symbol, bar, decision, timestamp, and nested evidence. A migration is prohibited unless a failing acceptance test proves this JSON store insufficient.
- No scheduler or runtime-state change. If the observer cannot be attached through the existing V2 bridge without changing those files, stop with `SCOPE_EXPANSION_REQUIRED` rather than modifying them.

### MUST_NOT_CHANGE

- `一键启动.cmd`, `scripts/launch-paper-console.ps1`, `scripts/run-local-paper-scheduler.py`, `services/execution/scheduler.py`, `services/execution/runtime_state.py`.
- `services/automated_trading/application/cycle_service.py`, `decision_service.py`, `entry_service.py`, risk, reconciliation, fill projection, protection, exchange adapter, and legacy frozen pipeline files.
- `testnet_sampling_v2` rules and all three research candidate implementations/parameters.
- Leverage, sizing, risk limits, max positions, drift threshold, SL/TP, reduce-only, external baseline, AI review, exchange-first behavior, symbol scope, and ACTIVE strategy identity.

## Same-Cycle Evidence Contract

The observer accepts only immutable research `MarketContext` plus scalar cycle identity. It never receives an adapter, entry executor, intent repository, order writer, position writer, or protection service.

Persist under the existing ACTIVE decision payload:

```python
{
    "active_strategy_id": "testnet_sampling_v2",
    "active_decision": "<terminal_stage>",
    "active_terminal_reason": "<reason_code>",
    "market_snapshot_reference": "<canonical hash>",
    "research_shadow": {
        "schema_version": "p1-same-cycle-research-shadow-v1",
        "lane": "RESEARCH_SHADOW",
        "scheduler_session_id": "<scheduler_instance_id>",
        "scheduler_cycle_id": "<coordination cycle id>",
        "cycle_id": "<V2 symbol cycle id>",
        "symbol": "BTC/USDT",
        "bar_close_time": "<same closed 15m bar>",
        "market_snapshot_reference": "<same canonical hash>",
        "active_strategy_id": "testnet_sampling_v2",
        "active_decision": "<terminal_stage>",
        "active_terminal_reason": "<reason_code>",
        "observations": [
            {
                "strategy_id": "trend_pullback_v2",
                "strategy_version": "2.0.0-research",
                "lane": "RESEARCH_SHADOW",
                "signal_direction": None,
                "signal_present": False,
                "decision_status": "SHADOW_NO_SIGNAL",
                "terminal_reason": "candidate_conditions_not_met",
                "entry_reference_price": None,
                "stop_reference_price": None,
                "target_reference_prices": [],
                "created_at": "<UTC timestamp>"
            }
        ]
    }
}
```

The shared reference is derived from the fixed scheduler symbol-cycle, ACTIVE closed-bar OHLCV fingerprint, research context hash, and bar-close time. The observer must verify that the research context symbol and `bars_15m.last_closed_at` equal the ACTIVE symbol and closed bar. Extra 1h/4h research inputs are loaded point-in-time with the existing `end_at=bar_close-timeframe_delta` rule; no future or partial bar is eligible.

## Red Tests

### Task 1: Per-Candidate Failure Isolation

**Files:** modify `tests/services/strategy_library/test_proposal_pipeline.py`, then `services/strategy_library/proposal_pipeline.py`.

- [ ] Add a test that replaces one evaluator with a function raising `RuntimeError("candidate failed")`, keeps the other two evaluators runnable, and asserts one structured error with `error_class=RuntimeError` and a bounded safe message while the other observations survive.
- [ ] Run `pytest -q tests/services/strategy_library/test_proposal_pipeline.py`; verify RED because the current pipeline propagates the exception.
- [ ] Add the minimal immutable error/version metadata and per-evaluator `try/except`; do not change any strategy parameters or evaluator results.
- [ ] Re-run the focused test and existing proposal replay tests GREEN.

### Task 2: ACTIVE Same-Cycle Observer And Position Gate Independence

**Files:** modify `tests/integration/test_v2_scheduler_entry_fact_chain.py`, then `services/execution/v2_scheduler_entry.py`.

- [ ] T1: run the formal bridge in ACTIVE with `cycle_id=C1`, `symbol=BTC/USDT`, `bar_close=T1`; assert the ACTIVE payload and all three observations carry `C1/BTC/USDT/T1` and the identical market snapshot reference.
- [ ] T2: make the ACTIVE result terminal `RISK_APPROVED/POSITION_ALREADY_OPEN`; assert all three research observations are still persisted.
- [ ] T3: make the research pipeline emit an explicit signal and instrument intent persistence, `execute_entry`, exchange order creation, position mutation, and protection mutation; call the observer and assert zero calls. Also assert no database intent/order/position row has a research candidate key.
- [ ] T4: evaluate the same ACTIVE request/result with the observer succeeding and with it raising; compare all ACTIVE decision, direction/signal, terminal reason, risk result, submission booleans, and request fields for exact equality after excluding only the appended research evidence metadata.
- [ ] T5: make one strategy error; assert `SHADOW_STRATEGY_ERROR` is recorded while ACTIVE finalization and reconciliation result persist unchanged.
- [ ] T6: assert ACTIVE still resolves `allow_legacy_writer=false` and the registered V2 job set contains no legacy writer.
- [ ] Run the new node IDs and verify RED because ACTIVE currently has no research payload and candidate exceptions are not isolated.
- [ ] Implement the observer after `run_automated_trading_cycle()` returns and before `_finalize_v2_cycle_decision()`. Catch all observer/framework errors and append evidence only; never change the ACTIVE result or task failure flag.
- [ ] Re-run all new tests GREEN.

### Task 3: Runtime Evidence Reader (Only If Required)

**Files:** conditionally create `scripts/verify_p1_same_cycle_research_shadow.py` and `tests/scripts/test_verify_p1_same_cycle_research_shadow.py`.

- [ ] Write a failing test over a temporary SQLite database that expects read-only counts for before/after shadow observations, matched/unmatched cycle-symbol-bar keys, and zero research-lineage intents/orders/positions/protections.
- [ ] Implement a read-only `mode=ro` query. It must never update data or call an exchange mutation API.
- [ ] Use it after the real launcher restart with a recorded rollout timestamp; preserve the exact output as runtime evidence.

## Minimal Implementation

1. Preserve the ACTIVE call and result object byte-for-byte in behavior: load entry timeframe, build `CycleRequest`, call `run_automated_trading_cycle`, then evaluate research from the fixed `bar_timestamp`.
2. Extend the pure pipeline only with structured per-candidate error capture and version metadata.
3. Build three normalized observation rows from one immutable `MarketContext` and one pipeline run.
4. Append the envelope to the existing decision JSON immediately before existing `_finalize_v2_cycle_decision` persistence.
5. On context/alignment/pipeline framework failure, record `RESEARCH_SHADOW_ERROR` with safe class/message and continue ACTIVE finalization.
6. Do not create any research intent, execution candidate, order, position, or protection object.

## Code Acceptance

Run in this order and capture exact summaries:

```powershell
& $env:AGENT_PYTHON -m pytest -q tests/services/strategy_library/test_proposal_pipeline.py::test_proposal_pipeline_isolates_one_candidate_error tests/integration/test_v2_scheduler_entry_fact_chain.py::test_v2_active_persists_same_cycle_research_shadow_when_position_already_open tests/integration/test_v2_scheduler_entry_fact_chain.py::test_research_shadow_observer_has_zero_execution_authority tests/integration/test_v2_scheduler_entry_fact_chain.py::test_research_shadow_failure_does_not_change_active_result
& $env:AGENT_PYTHON -m pytest -q tests/services/strategy_library/test_proposal_pipeline.py tests/integration/test_v2_scheduler_entry_fact_chain.py
& $env:AGENT_PYTHON -m pytest -q tests/services/test_runtime_scheduler.py tests/services/test_local_v2_scheduler_script.py tests/integration/test_natural_automated_trading_cycle_contract.py
& $env:AGENT_PYTHON -m pytest -q tests/services/test_u1_shadow_terminal_exit_reconciliation.py tests/services/test_automated_trading_reconciliation.py
& $env:AGENT_PYTHON -m ruff check services/execution/v2_scheduler_entry.py services/strategy_library/proposal_pipeline.py tests/integration/test_v2_scheduler_entry_fact_chain.py tests/services/strategy_library/test_proposal_pipeline.py
& $env:AGENT_PYTHON -m mypy
& $env:AGENT_PYTHON -m pytest -q
pre-commit run --all-files
git diff --check
```

The known full-suite allowance is limited to the two already-proven candidate-registry baseline failures. Any new failure is `P1_FAIL_REGRESSION`.

## Runtime Acceptance

1. Record rollout timestamp and read-only pre-state: scheduler state, ACTIVE decisions with no research payload, Binance Testnet positions/open orders, local managed positions/protections, and external baseline.
2. Use only `一键启动.cmd`. Do not invoke an internal evaluator/cycle directly for runtime evidence.
3. Confirm launcher contract: ACTIVE, BINANCE_TESTNET, `testnet_sampling_v2`, `entry_authorized=true`, `legacy_writer=false`, external baseline captured.
4. Observe multiple natural `automated_trading_v2_cycle` runs, including a cycle where ACTIVE reaches `POSITION_ALREADY_OPEN` when available.
5. Query persisted decision payloads created after rollout. Require `after_shadow_observations>0`, `same_cycle_matches>0`, `unmatched=0`, and at least two concrete BTC/ETH examples.
6. Re-read Binance positions/orders and local projections. Attribute every newly observed exchange order by ACTIVE intent/candidate lineage. Research candidate lineage must remain absent.
7. Report the actual mutation ledger with every research counter equal to zero.

## Rollback Point

The immutable starting point is `482c26c368a45eb516045bb5f7fcfa5065bfd5c1` on the already checked-out `backup/2026-08-10-wip` branch with a clean tracked worktree. Do not reset, checkout, clean, rebase, or switch branches. If rollback is requested, manually reverse only the P1 diff after preserving runtime evidence; never touch user data, positions, orders, protections, or database state.

## Implementation Gate Self-Check

- One root cause only: **PASS**.
- ACTIVE path proven from current code: **PASS**.
- Three research paths proven runtime-ready: **PASS**.
- File scope frozen at two business files plus focused tests/evidence tooling: **PASS**.
- Red tests defined before production edits: **PASS**.
- P0 launcher, ACTIVE/Testnet resolution, entry authorization, external baseline, SHADOW no-submit, and legacy-writer contracts unchanged: **PASS**.
- ACTIVE strategy rules, research strategy parameters, risk and reconciliation unchanged: **PASS**.
- No second scheduler/writer/execution chain: **PASS**.
- Observer function receives no submit-capable dependency and ends before intent construction: **PASS**.
- Same symbol/bar/reference evidence is persisted in the existing decision row: **PASS**.
- `POSITION_ALREADY_OPEN` cannot suppress research evaluation: **PASS**.
- P2 promotion/tuning/switching excluded: **PASS**.

`IMPLEMENTATION_GATE=PASS`
