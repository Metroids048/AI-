# Task 1 — Put automatic Paper entry orders on the immutable intent contract

## Context

The automatic Paper/Testnet entry path currently creates an `ExecutionOrderRequest` whose executable semantics live in `entry_context`. The repository already contains `TradeIntent`, `DecisionEngine`, config snapshots, and matching persistence fields on `OrderExecution`, but the hot path does not populate them.

## Required behavior

1. Add a small deterministic adapter at the Paper entry boundary that creates a `TradeIntent` from the real generated entry request and active config snapshot.
2. Apply it only to new OPEN entries in `PaperCycleOrchestrator`; do not change close/protection/reconcile paths in this task.
3. Use real persisted/generated values: paper cycle key, strategy/version, symbol/direction, requested quantity derived from requested notional and reference price, stop/take prices, decision candle close time, active `config_snapshot_id`, and active `config_hash`.
4. Never synthesize a config identity. If no active config snapshot exists, local Paper behavior may remain compatible, but automatic gateway submission must remain fail-closed elsewhere; this task must not silently invent snapshot IDs or hashes.
5. `ExecutionGatekeeperService.submit_order()` must persist the intent identity fields already present on `OrderExecution`: `intent_id`, `cycle_id`, `decision_id`, `config_snapshot_id`, and `config_hash`.
6. Preserve all existing risk gates, stop-loss requirements, idempotency behavior, risk values, and mainnet/Testnet switches.
7. Do not modify gateway normalization or decision-event persistence in this task.

## Tests and verification

- Follow strict TDD: add focused tests first and run them to observe the expected failure.
- Cover at least: automatic entry request carries an immutable OPEN intent when an active snapshot exists; gatekeeper persists all intent/config identity fields; no active snapshot does not create a fake intent.
- Run the focused test files you change plus existing contract/runtime regression tests relevant to this path.

## Ownership

You own the minimal production/test files needed for this task, expected around `services/execution/paper_cycle_orchestrator.py`, `services/execution/gatekeeper.py`, a small adapter module if useful, and focused tests. Do not edit current uncommitted migration/evidence/Testnet-authorization files.

## Constraints

- Work directly in the existing shared checkout on main; do not create a branch/worktree and do not commit.
- You are not alone in the codebase. Preserve all existing changes and do not revert others.
- Do not change risk thresholds, stop/take-profit values, leverage, position caps, credentials, or mainnet flags.
- Report exact RED and GREEN commands/results.
