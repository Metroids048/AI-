# Production Call Graph

## Status

`NOT_TRACED_STOP_CONDITION`

The required trace from one-click startup through API/Scheduler separation,
engine activation, task registration, lease/fencing, V2 cycle, exchange facts,
recovery, exit, runtime API, and frontend was not performed.

Reason: Phase 0 established that the worktree was dirty before audit evidence was
created. The master prompt requires immediate stop and forbids separating local
changes from claimed Gate work.

## Unverified Required Chain

```text
One-click startup
-> API process
-> Scheduler process
-> Engine activation
-> V2 task registration
-> Writer lease/fencing
-> Cycle service
-> Runtime control
-> Exchange snapshot
-> Reconciliation/recovery
-> Existing-position management
-> Decision/sampling/AI
-> Entry gate
-> Exchange receipt/fills
-> Position projection/protection
-> Exit/final reconciliation
-> Runtime API
-> Frontend
```

Every edge remains `UNVERIFIED`; none is marked `PRODUCTION_CALL`, `TEST_ONLY`,
`SCRIPT_ONLY`, or `UNREACHABLE` without source and runtime evidence.
