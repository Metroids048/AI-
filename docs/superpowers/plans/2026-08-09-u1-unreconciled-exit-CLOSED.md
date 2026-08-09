# U1 UNRECONCILED_EXIT — CLOSED (2026-08-09)

`U1_UNRECONCILED_EXIT = CLOSED`
`LOCAL_ACCEPTANCE = PASS` (A1–A15)
`POST_REPAIR_RUNTIME_ACCEPTANCE = PASS`

Commit: `e210963` fix(v2): keep terminal-exit reconciliation alive in SHADOW

## Root cause

```
ROOT_CAUSE = cycle_service.run_cycle 中 request.persist_facts 门的不对称覆盖
             (persist_facts = v2_activation is ACTIVE, v2_scheduler_entry.py:404)
```

`persist_facts` conflated two unrelated authorities: creating NEW exposure, and
converging exposure that **already exists** onto exchange truth. Under SHADOW,
every repair path was gated OFF while destructive ghost recovery stayed ON.

| path | gated by `persist_facts` before the fix | destructive? |
|---|---|---|
| `reconcile_closed_position_protections` | yes (off in SHADOW) | no — repairs |
| `_recover_confirmed_v2_exit_gaps` early return | yes (off in SHADOW) | no — repairs |
| `_project_confirmed_protection_exits` | yes (off in SHADOW) | no — repairs |
| `_persist_reconciliation_fact` | yes (off in SHADOW) | no — repairs |
| `recover_pending_state` + `execute_recovery_actions` | **no — always ran** | **yes — writes QUARANTINED** |

Self-lock: a BTC position opened under ACTIVE (`08-08 10:30:36`) kept live exchange
protection after the engine was downgraded to SHADOW. Its native stop filled
(`08-09 02:47:08.700Z`), the exchange went flat, ghost recovery quarantined the local
position, quarantine removed it from `get_open_positions`, and the only query that
could have rescued it lived inside a gated function. 150+ cycles over 2.5h never repaired it.

## Fix — Narrow-A

`CycleRequest.reconcile_existing_positions` = `persist_facts or activation is SHADOW`.
Activation controls NEW execution authority; it must not disable reconciliation of
exposure already created while ACTIVE. Order submission stays gated by `persist_facts`
(`_recover_confirmed_v2_entry_gap` and `fail_closed` deliberately unchanged).

Second guard: `CycleResult.terminal_exit_projection_incomplete`. When terminal-exit
projection cannot finish a cycle, an authoritative reduce-only fill may exist but be
unread, so `QUARANTINE_LOCAL_GHOST_POSITION` actions are deferred to the next cycle.
QUARANTINE remains valid for genuinely unexplainable divergence — this only fixes the
ordering (confirmed terminal-exit projection **before** local ghost quarantine).

## Repair — production path, no SQL

Repaired by the real scheduler on the **first BTC cycle after restart**:

```
2026-08-09 14:09:33 | fact_persistence | persisted reduce-only exit
  cycle=219da04e-9c6f-4f09-ad21-767579f45b8b
  position=10920a3c-6260-479c-8af6-6c410b303cfd
  order=28533281387 trades=['525914324']
```

No repair script was needed. No SQL, no synthetic fill, no forged order, no new BTC order.

The TP sibling cancellation returned `-2011 Unknown order sent` (Binance had already
purged the OCO sibling) and was logged as a warning without blocking the repair —
the documented normal terminal state.

## realized_pnl — deviation from the written instruction, with evidence

The order said write **gross** `-8.83088000`. The project's existing contract is **NET**:

- `fact_persistence.py`: `position.realized_pnl = gross_pnl - position.entry_fee - result.total_fee`
- `tests/services/test_automated_trading_fact_persistence.py` asserts `5.7340` from gross `6.000` (− 0.13 − 0.136)

Writing gross would have required changing that line, silently flipping the semantics for
every V2 position and creating exactly the dual standard the order forbade. Per the
order's own escape clause, the single existing contract was kept:

```
realized_pnl = -10.84482969            (NET, existing contract)
gross         = realized + entry_fee + exit_fee
              = -10.84482969 + 1.00874102 + 1.00520867
              = -8.83088000            (matches Binance realizedPnl exactly)
```

Fees stay on `v2_exchange_fills.commission` and are never double-charged.

## Acceptance A1–A15

`scripts/u1_acceptance_readonly.py` → `LOCAL_ACCEPTANCE = PASS`, all 16 checks PASS.
Exchange side independently confirmed by `scripts/u1_exchange_truth_snapshot_readonly.py`:
BTC flat, 0 leftover protection orders, ETH short 10.976 @ 1934.0 untouched,
stop leg resolves 1 fill via algo id, TP leg 0 fills.

Idempotency: `trade_fills=1`, `btc_reduce_only=1`, `exit_order_rows=1`, `closed_btc=1`,
post-restart `LOCAL_GHOST_QUARANTINED` incidents `=0`. Position/protection versions
stayed 3/4 across 4 further cycles — repeated reconciliation adds nothing.

## Regression tests

`tests/services/test_u1_shadow_terminal_exit_reconciliation.py` — 9 tests, all 5 required cases:
SHADOW+native SL, SHADOW+native TP, algo id ≠ execution order id (asserts `fetch_order`
is never called), OCO sibling disappearance as normal terminal, idempotency. Plus
ACTIVE-lane authority, sampling attribution, and both quarantine-deferral paths.

Both guards mutation-tested: reverting either one fails tests.

## Verification

```
[验证] ruff check .            -> Found 3 errors  (all PRE_EXISTING, in scripts/ files untouched
                                  by U1: run_proposal_research_replay.py B023 x2,
                                  verify_gate17_e2e.py C416)
[验证] ruff check <U1 files>   -> All checks passed!
[验证] mypy                    -> Success: no issues found in 225 source files
[验证] pytest (targeted, 4 files) -> 42 passed, 2 warnings in 4.79s
[验证] pytest -q (full)        -> 2 failed, 1400 passed, 16 skipped in 105.76s
[验证] git diff --cached --stat -> 5 files, 1265 insertions(+), 6 deletions(-)
[基线对比]                     -> 无新增失败。2 failed 全在 tests/test_candidate_registry.py
                                  (9 vs 10) = PRE_EXISTING_BASELINE_FAILURE，按指令未修改。
```

## Runtime state at close

`entry_enabled = true`, reason `u1_unreconciled_exit_repaired_restore_pre_incident_state`,
`updated_by = api`, version 5. Read back independently from `GET /cycles`
(`entry_enabled: true`) and from `v2_runtime_controls`. 4 cycles observed after restore
(2 complete BTC), all COMPLETED.

`reconciliation_status` stays DEGRADED solely on `EXTERNAL_POSITION_UNCLAIMABLE` for the
preserved ETH manual short — pre-existing and by design.

## Not done, deliberately

- **Push blocked.** `e210963` is committed locally but `git push` is refused by the repo's
  own `refuse-push-dirty-worktree` pre-push hook, which counts the whole
  `git status --porcelain`. The worktree carries ~30 untracked files predating this round
  (I-1/S1 scripts, plan docs, `.apodex/`, `.mirasim/`). Clearing it would mean committing
  files outside U1 scope, so it was left alone.
- No ACTIVE→SHADOW downgrade guard (out of scope this round). The gap that produced U1 —
  downgrading while a V2 position holds live exchange protection — is now harmless
  because reconciliation survives the downgrade, but nothing prevents the downgrade itself.
- No analytics refactor: no code under `services/strategy_library`, `services/validation`,
  `services/review`, or `apps/api` reads `v2_managed_positions` / `v2_exchange_fills`,
  so there was no sampling-attribution filter bug to fix. A test now pins `SAMPLING` on
  both entry and exit intents.

Next: I-2.
