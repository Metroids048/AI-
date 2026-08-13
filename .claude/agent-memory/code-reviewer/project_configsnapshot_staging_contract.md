---
name: configsnapshot-staging-contract
description: Staging a ConfigSnapshot requires effective_cycle_id and canonical_config_hash; hand-rolled construction fails validation before any DB write
metadata:
  type: project
---

Any script or service that stages a runtime config change through `ConfigSnapshot`
must build it with `ConfigSnapshot.create(...)` and pass
`effective_cycle_id="NEXT_CYCLE"`.

**Why:** `shared/models/trading.py` defines `ConfigSnapshot(ImmutableContract)` with
`effective_cycle_id: str` **required, no default**, and a `validate_snapshot`
model-validator that rejects any `config_hash` not exactly equal to
`canonical_config_hash(config)` — which is `sha256:` + hex over
`json.dumps(..., sort_keys=True, separators=(",", ":"), ensure_ascii=False)`.
A hand-rolled `hashlib.sha256(json.dumps(config, sort_keys=True)).hexdigest()`
produces a bare hex digest with different separators and fails.
Separately, `ConfigSnapshotRepository.activate_pending` only activates when
`pending.effective_cycle_id in {cycle_id, "NEXT_CYCLE"}`, so a snapshot staged
without that value would never activate even if construction succeeded.

**How to apply:** When reviewing a config-staging script, construct the model
offline before trusting its `--apply` path or any "APPLIED" log line it prints.
Two validation errors fire *before* the DB write, so the script can be fully
non-functional while still reading as correct. Related: [[detached-pydantic-writes-are-silent-noops]].
