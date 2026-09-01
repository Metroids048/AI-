# R3 Runtime Closeout Evidence — 2026-09-01

## Verdict

`AUTO_TRADING_R3: FAIL / NO_FORWARD_VALIDATION_CANDIDATE / OUT_OF_SCOPE_R3`

The infrastructure changes and regressions described below are verified, but the
frozen Alpha/Forward validation produced no promotable candidate.  No Pending
ConfigSnapshot was staged, Production remains `PENDING / NO_VALIDATED_EDGE`, and
the standard runtime correctly resolves `EntryAuthority=NONE`.  Consequently no
new natural Testnet entry was authorized and final trading acceptance is not
claimed.

## Root Cause Matrix

| ID | Root cause / boundary | Resolution and evidence |
|---|---|---|
| RC-01 | The standard one-click launcher enabled Canary implicitly. | Removed `-EnableNaturalTestnet` from `一键启动.cmd`; the normal V2 scheduler always calls the standard cycle with `canary_acceptance=false`. Canary remains an explicit acceptance-only entry point. |
| RC-02 | Scheduler startup authority was derived separately from the Active ConfigSnapshot authority used by a Cycle; Pending activation also occurred once per symbol, allowing a background refresh to split BTC/ETH across two snapshots. | Scheduler now reads the single running directional run, activates/captures Active ConfigSnapshot once per scheduler Cycle, resolves both BTC/ETH settings from that immutable capture, validates the canonical hash, and applies Production → Forward → None precedence. Any cross-symbol binding mismatch degrades even when authority is `NONE`. |
| RC-03 | A Recovery Hold overwrote strategy authority with `NONE`, its owner check had a read/write race, and an absent RuntimeControl row could leave a normal installation falsely management-only. | Durable RuntimeControl changes only `entry_enabled`; scheduler/cycle retain authority, strategy and snapshot facts. Claim/clear use one conditional database `UPDATE`. Standard worker bootstrap inserts an enabled control only when the row is absent; it never overwrites an operator pause or recovery hold. Hold clear requires the full healthy/readiness predicate. |
| RC-04 | Scheduler health and entry readiness were conflated, and a Cycle-only `ENTRY_DATA_PENDING` value leaked into top-level state. | Runtime publishes only `TRADING_READY`, `MANAGEMENT_ONLY`, `ENTRY_BLOCKED` or `DEGRADED`, plus snapshot, authorization, control and reconciliation facts. With Authority `NONE`, lower-level data-stage detail no longer replaces the top-level `ENTRY_BLOCKED` authorization truth. |
| RC-05 | No-trade reporting collapsed infrastructure, authorization, strategy and risk outcomes and labelled an enabled-but-unauthorized runtime `ENTRY_PAUSED`. | Existing Runtime API now returns `SYSTEM_BLOCKED`, `AUTHORIZATION_BLOCKED`, `STRATEGY_NO_SIGNAL` or `RISK_REJECTED`, structured reasons, BTC/ETH facts and candidate/submission/fill timestamps. `summary_code` now retains `NO_FORWARD_VALIDATION_CANDIDATE` when `entry_enabled=true`; `ENTRY_PAUSED` is reserved for RuntimeControl/management-only pauses. Frontend consumes the same endpoint. |
| RC-06 | Windows child-first launcher cleanup allowed a live Supervisor to interpret intentional Worker termination as a crash. | Scheduler cleanup is parent-first and recognizes both Windows and forward-slash project paths. Real repeated health probes kept the same Supervisor/Worker PIDs until an intentional crash test. |
| RC-07 | Startup could observe a newly-created V2 critical task as dead and the 30-second readiness window could expire during a legitimate recovery cycle. | The registered task is published alive immediately; recovery-aware startup waits up to 120 seconds for the exchange-first management cycle. |
| RC-08 | Management-only Cycle publication returned before updating top-level reconciliation health. | Reconciliation is published before the RuntimeControl early return. A real Worker crash progressed Hold → Restart → Reconcile → Re-enable with authority unchanged and reconciliation healthy. |
| RC-09 | Runtime projection caching occurred only after each concurrent endpoint independently performed exchange/SQLite work. | Added one projection single-flight boundary and early 10-second cache. Concurrent snapshot/positions/reconciliation/no-trade calls shared one projection ID and returned HTTP 200. |
| RC-10 | The Active Snapshot has no sealed Forward authorization; no Pending Snapshot exists. | Re-ran the frozen bounded validation workflow without changing strategy or gates. It ended `NO_PROMOTABLE_ALPHA_AFTER_BOUNDED_SEARCH`; therefore staging was correctly skipped and no Canary fallback was used. |

## Frozen Validation Result

Artifact directory:
`artifacts/r3_forward_validation_20260831_history`

- `FINAL_REPORT.json`: `NO_PROMOTABLE_ALPHA_AFTER_BOUNDED_SEARCH`
- reason: `no_candidate_passed_research_and_validation_after_generations_0_1_2`
- active candidate: `null`
- `CHAMPION_PROPOSAL.json`: `production_authority=NOT_GRANTED`
- best screened family (`pandas_ta_broad_screen_v1`) had PF `1.5881`, expectancy
  `0.0082823`, MaxDD `0.10281`, but only `24` trades and only `1` positive window;
  density, final holdout, one-minute fidelity, Freqtrade/vectorbt and LCB
  requirements remained blocking.

## Runtime Evidence

- Baseline: `main@0faea6d74b53c63b2aa0e1bcd527f1d7a8b21d87`.
- One-click startup: `SUCCESS / READY`.
- Active snapshot: `e3cf16fa-9be8-48dc-a33a-ec344a07f4ef`;
  hash `sha256:ff0afbcbb14a90be6a493ba1f04afa53eb6b41a5e76b6d8a5f066e52436b59d9`;
  integrity valid; Pending snapshot `null`.
- Runtime after full restart: `ACTIVE / BINANCE_TESTNET`,
  `entry_enabled=true`, `entry_authority=NONE`,
  reason `NO_FORWARD_VALIDATION_CANDIDATE`, `ENTRY_BLOCKED`,
  `forward_authorized=false`, `production_authorized=false`.
- Single writer: one Supervisor tree, one Worker, only
  `automated_trading_v2_cycle` registered, `legacy_writer_enabled=false`.
- The final latest-code one-click restart completed at `2026-09-01T09:59:20Z`.
  Its first full scheduler Cycle completed at `2026-09-01T10:00:25Z` with
  BTC and ETH both bound to snapshot `e3cf16fa-9be8-48dc-a33a-ec344a07f4ef`
  and the same `sha256:ff0af...b59d9` hash; `reconciliation_healthy=true` and
  scheduler error `null`.
- Exchange/local after full restart: exchange positions `0`, exchange open
  orders `0`, local managed open positions `0`, discrepancy codes `[]`.
- Controlled empty-account Worker crash: Supervisor remained alive; Runtime
  progressed `MANAGEMENT_ONLY` under durable hold, spawned a new Worker,
  completed healthy reconciliation, cleared the system hold, restored
  `entry_enabled=true`, and retained `EntryAuthority=NONE`. No duplicate intent,
  position or open order appeared. This is empty-account crash evidence only;
  protected-position recovery remains unproved in this R3 run.

## Verification

- Targeted Runtime/no-trade/scheduler suite: `99 passed, 2 warnings`; the final
  snapshot-capture/absent-control red-green subset added `6 passed`.
- Scheduler plus atomic RuntimeControl audit suite: `53 passed`.
- `ruff check .`: `All checks passed!`
- `mypy`: `Success: no issues found in 312 source files`
- `pytest -q`: `1993 passed, 7 skipped, 7 warnings in 137.33s`
- `npm --workspace frontend/admin run test`: `21 passed` files,
  `118 passed` tests.
- Browser `/ops` and `/trading`: no console errors; Runtime showed
  `INFRASTRUCTURE: BLOCKED / AUTHORIZATION_BLOCKED`, `Authority: NONE`, valid
  snapshot, healthy reconciliation and exchange/local/open-order `0/0/0`.
  Runtime endpoints returned one shared projection ID and exact no-trade code
  `NO_FORWARD_VALIDATION_CANDIDATE`; a 35-second observation showed the expected
  approximately 8-second trading-page and 30-second Runtime refresh cadence,
  not a millisecond request loop. Existing React Router and chart auto-size
  warnings remain unrelated.
- Independent read-only review ran three rounds. Its final round confirmed the
  atomic RuntimeControl CAS and complete recovery-readiness predicate and found
  no remaining actionable finding.

## Six Gates

| Gate | Result | Boundary |
|---|---|---|
| 1 — Static | PASS | Targeted, full backend, frontend, Ruff and mypy passed. |
| 2 — Startup | PASS | Standard one-click startup; single Supervisor/Worker/V2 writer. |
| 3 — Authority | FAIL | No sealed Forward candidate; correct state is `NONE`, not Canary. |
| 4 — Natural trade | NOT RUN | New exposure is correctly blocked; no candidate was manufactured. |
| 5 — Crash recovery | PARTIAL PASS | Empty-account Worker recovery passed; protected-position crash recovery has no new R3 evidence. |
| 6 — Full restart | PASS (empty account) | Scoped full stop reached zero project processes/listeners; one-click restart reconciled exchange/local `0/0` and completed a new scheduler Cycle. |

`AUTO_TRADING_R3_FINAL_ACCEPTANCE: NOT PASSED`
