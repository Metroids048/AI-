# Memory Index

- [ConfigSnapshot staging contract](project_configsnapshot_staging_contract.md) — effective_cycle_id + canonical_config_hash are required; hand-rolled snapshots fail before any DB write
- [Detached Pydantic writes are silent no-ops](project_detached_pydantic_writes.md) — mutating a repository read result then committing persists nothing
- [Exposure cap is entry-gating only](project_exposure_cap_is_entry_gating_only.md) — verified consumer map, plus the weekly task that reverts tier leverage
- [Verify against manifest numbers](feedback_verify_against_manifest_numbers.md) — check frozen constants and the touched-file whitelist against the manifest, not the tests
