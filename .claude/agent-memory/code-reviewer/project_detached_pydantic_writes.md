---
name: detached-pydantic-writes-are-silent-noops
description: Repository list_/get_ methods return detached Pydantic contracts, so mutating them then calling session.commit() persists nothing
metadata:
  type: project
---

Mutating an object returned by a `services/strategy_library/repository.py`
read method and then calling `session.commit()` writes nothing to the database.

**Why:** those methods convert ORM rows into fresh Pydantic contract models
(e.g. `list_paper_runs` -> `_paper_run_from_orm(row)` -> `PaperRun(...)`).
The result is detached from the SQLAlchemy session. `PlatformModel` is *not*
frozen (only `ImmutableContract` sets `frozen=True`), so
`run.execution_profile = updated` succeeds silently instead of raising —
the write just evaporates. The only persisting path is the explicit
`update_paper_run(paper_run_id, execution_profile=...)` repository call.

**How to apply:** Whenever a diff mutates an attribute on something returned by
a repository read method, check whether that method returns an ORM row or a
contract model before accepting the change as persistent. A nearby
`session.commit()` makes the no-op look intentional and reviewed.
Related: [[configsnapshot-staging-contract]].
