# Test Matrix

## Proof-Type Isolation

| Proof type | Status | Reason |
|---|---|---|
| `STATIC_CODE_REVIEW` | PARTIAL | Baseline and path-level changeset only; production call graph not inspected |
| `UNIT` | NOT_RUN_STOP_CONDITION | Phase 0 dirty worktree |
| `REPOSITORY_INTEGRATION` | NOT_RUN_STOP_CONDITION | Phase 0 dirty worktree |
| `STRICT_FAKE_SCHEDULER_E2E` | NOT_RUN_STOP_CONDITION | Phase 0 dirty worktree |
| `SHADOW_REAL_DATA` | NOT_RUN_STOP_CONDITION | Phase 0 dirty worktree |
| `TESTNET_CONTRACT` | NOT_RUN_STOP_CONDITION | Phase 0 dirty worktree and credentials absent |
| `NATURAL_SCHEDULER_TESTNET` | NOT_RUN_STOP_CONDITION | Contract was not eligible to run |
| `FRONTEND_RUNTIME` | NOT_RUN_STOP_CONDITION | Phase 0 dirty worktree |
| `STRATEGY_RESEARCH_READINESS` | NOT_RUN_STOP_CONDITION | Engineering acceptance not established |

## Mandatory Commands

No backend, frontend, migration, Docker, Shadow, Testnet, Natural E2E, or browser
validation command was run. This is intentional compliance with the master
prompt's stop condition, not a pass.

```text
[验证] ruff check .   -> NOT RUN: Phase 0 dirty-worktree stop
[验证] mypy           -> NOT RUN: Phase 0 dirty-worktree stop
[验证] pytest -q      -> NOT RUN: Phase 0 dirty-worktree stop
[验证] git diff --stat -> 34 files changed, 4429 insertions(+), 488 deletions(-)
[基线对比]             -> UNVERIFIED: dynamic checks prohibited after Phase 0 stop
```

Existing tests, test counts, prior Gate evidence, skips, and commit messages were
not counted.
