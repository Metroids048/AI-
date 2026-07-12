# ADR: Durable Workflow Boundary for a Future Temporal Migration

**Status:** Accepted for design evaluation only; Celery + Redis remains the production runtime.

## Context

The platform currently uses Celery Beat, Celery workers, Redis, and persisted SQLAlchemy state for ingestion, validation, Paper cycles, notifications, and review. The research loop must remain restartable, idempotent, fail-closed, and auditable. Introducing Temporal now would add a cluster, workers, deployment topology, operational credentials, and a second task runtime without closing an immediate safety gap.

## Decision

Do not add the Temporal SDK or Temporal Server in the current implementation tranche. Apply Temporal durability rules to the existing Celery design:

- Keep database writes, exchange calls, RSS/LLM calls, and other I/O in task/activity-like boundaries.
- Give every externally visible task a stable logical idempotency key and persist the result before acknowledging completion.
- Configure late acknowledgements, worker-loss requeue, bounded retries, time limits, and explicit non-retryable authentication/configuration failures.
- Persist per-symbol checkpoints and compensation state for Testnet acceptance and dual-leg carry.
- Expose read-only runtime status from persisted state or scheduler snapshots; do not mutate state from health reads.
- Use a saga-style compensation order for exchange operations and keep compensation idempotent.

## Candidate Temporal Workflows

1. `PaperRuntimeWorkflow`: periodic signal scan, decision trace, Gatekeeper admission, order lifecycle, protective exits, and Review writeback.
2. `TestnetAcceptanceWorkflow`: preflight, sequential symbol child workflows, compensation, and final reconciliation.
3. `CarryExecutionWorkflow`: Spot/Futures leg coordination, funding flip handling, compensation, and flat-account proof.
4. `DailyReviewWorkflow`: scheduled review generation and failure knowledge writeback.

Candidate Activities are exchange calls, database reads/writes, data ingestion, LLM calls, notifications, and reconciliation. Workflows would own only deterministic orchestration, timers, signals, queries, and bounded state transitions.

## Migration Preconditions

- Celery task contracts and idempotency keys are stable and covered by replay-like deterministic tests.
- Every task has persisted status, heartbeat/lease, retry classification, and a recovery command.
- A Temporal dev-server PoC proves workflow replay, activity retry, cancellation compensation, Continue-As-New for long-running Paper workflows, and versioning with an existing run.
- Migration is one workflow family at a time with dual-read observability; no live order path is migrated without an explicit rollback plan.

## Consequences

This preserves the required stack and avoids a second production scheduler now. The ADR makes the future boundary explicit and prevents Celery tasks from accumulating non-deterministic orchestration that would make a later migration unsafe.
