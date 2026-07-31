# Changeset

## Locked Range

`9afa16681e1525897ab03b89ad1febc37c30d807..7351110595bc063f3db69afa1b5554cdb8de7d3a`

Observed summary:

```text
34 files changed, 4429 insertions(+), 488 deletions(-)
```

Evidence:

- `RAW/07-diff-stat.command.json`
- `RAW/08-diff-name-status.command.json`
- `RAW/09-log-range.command.json`

## Path-Level Classification

| Category | Observed paths |
|---|---|
| Database/Migrations | `migrations/versions/0019_*`, `0020_*`, automated-trading models/repository |
| Scheduler/Tasks | `apps/api/celery_app.py`, `services/execution/scheduler.py`, `tasks.py`, `v2_scheduler_entry.py` |
| Cycle | `services/automated_trading/application/cycle_service.py` |
| Reconciliation/Recovery | `recovery_executor.py`, repository changes |
| Entry/Protection/Exit | fact persistence, invariants, repository, cycle changes |
| API | `apps/api/routers/automated_trading.py` |
| Frontend | `frontend/admin/src/pages/PaperConsole.jsx` |
| Scripts | database invariant audit and shadow script |
| Tests | API, audit, integration, cycle, database, fact persistence, repository, schema tests |
| Docs/Memory/Evidence | task history, project knowledge, `audit/v2_closure/*` |

## Integrity Limits

This is only a path-level classification. The audit did not inspect or accept the
content of these changes because Phase 0 stopped on a dirty worktree. In
particular, existing `audit/v2_closure/*`, commit subjects, and test files are not
treated as Gate evidence.

The pre-existing untracked file was:

```text
?? docs/audit/AI-global-project-acceptance-audit-master-prompt.md
```

It is outside the locked commit range and prevents an unambiguous HEAD-only
acceptance audit under the master prompt's rules.
