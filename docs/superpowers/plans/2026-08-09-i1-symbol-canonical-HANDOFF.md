# I-1 Symbol Canonical Migration — HANDOFF

> Written 2026-08-09 during the operator-authorized controlled migration window (choice B).
> Status: **CLOSED.** Migration committed ~01:57Z, runtime restored 02:12Z, post-start
> verification passed 02:31Z, and `entry_enabled` restored to its pre-maintenance state
> (`true`) at 03:00:27Z under explicit operator ruling.

## Formal conclusion

```
I1_SYMBOL_CANONICAL = PASS

DB migration        : 106,292 legacy rows -> canonical
Read path           : legacy input + canonical input -> same canonical rows
Writer topology     : multi-writer -> single scheduler writer
Runtime protection  : PASS
Production behavior : NO I1-CAUSED REGRESSION OBSERVED

Known unresolved    : MARKET_EXTRAS_STALE
                      MARKET_EXTRAS_NON_FUNDING_FIELDS_EMPTY

entry_enabled       : RESTORE_TO_PRE_MAINTENANCE_STATE  (done, true)
```

Operator ruling on the close-out: the global lock existed only to create the I-1
maintenance window. Leaving it at `0` after the four gates passed would convert a
one-shot safety measure into a new permanent runtime state and manufacture the next
round of *"why does the system never open a position"* false alarms. Restoring it
removes **only** the global maintenance lock. Strategy-level admission gates continue
to operate independently — restoring entry is **not** a licence to backfill edge
evidence or edit the manifest.

Concurrent facts that all remain true after the restore:

| Fact | State |
|---|---|
| I-1 | COMPLETE |
| `market_extras` symbol truth | **FIXED** |
| `market_extras` freshness | **NOT FIXED** |
| `market_extras` OI/ratio coverage | **NOT FIXED** |
| Active primary strategy | still FAIL_CLOSED by the validated-edge gate |
| SHADOW | active |
| V2 cycle stopped | `OUT_OF_T0_SCOPE` / `NOT_INVESTIGATED` |
| candidate registry 9-vs-10 pytest | `PRE_EXISTING_BASELINE_FAILURE` / `NOT_CAUSED_BY_I1` |

The two failing `tests/test_candidate_registry.py` assertions were deliberately left
alone. Editing them to manufacture an all-green finish for I-1 belongs to a different
task.

Next step is **I-2** per the T0 plan. Do not revisit symbol canonicalization.

## Timezone convention (previous misread source)

DB columns store **naive UTC**. The local machine is **UTC+8**. A DB `mtime` of
`01:04:05Z` and a shell listing of `09:04:30` are the *same instant*. Do not
conclude "writes stopped hours ago" by comparing the two directly.

## Verified runtime state (read-only, git d2e00e2)

| Item | Value |
|---|---|
| `v2_runtime_controls` | `entry_enabled=0`, `reason=i1_symbol_canonical_migration_window`, `updated_by=api`, `updated_at=2026-08-09 00:38:20.864972`, `version=2` |
| Open position | `BTC/USDT long qty=0.0388 entry=64996.2 state=PROTECTED` (pos `10920a3c`) |
| Protection | `SL=64768.9 @1000000160188648`, `TP=65337.2 @1000000160188659`, `PROTECTION_ACTIVE` |
| `market_extras` | total **106292**, legacy (`%:USDT`) **106292**, canonical **0** |
| `v2_execution_cycles` | count **13695**, max `started_at 2026-08-08 17:54:27` (stalled) |
| `scheduler_cycles` | count **32225+**, max `2026-08-09 01:09:05` (advancing) |
| DB | `.local_paper_console.db`, 341164032 bytes |
| `:8016` listener | **none** (API process alive, port closed) |

Artifacts: `artifacts/t0-i1-symbol-canonical-20260809/STEP01_PRE_MIGRATION_RUNTIME_STATE.json`

## CORRECTION: "3 independent scheduler writers" is disproven

The pre-window claim of three independent `runtime_scheduler_service` writers does
**not** hold at audit time. It was built on a dead PID and a reused PID.

| Claimed PID | Actual identity now | Verdict |
|---|---|---|
| 22088 | `python -u tmp_validation_driver.py --sync`, parent 29536, cwd `C:\Users\Windows11\Desktop\alpha` | **PID REUSED by an unrelated project — must not be killed** |
| 25708 | does not exist | gone |
| 26212 | `run-local-paper-scheduler.py --engine v2_shadow` | verified match |
| 21472 | `-m apps.api.local_server --port 8016` | verified match (API child) |
| 24644 | `-m apps.api.local_server --port 8016` | verified match (API shim parent) |

Real topology — two `venv-shim → base-interpreter` pairs, not three writers:

```
API        24644 (.ai-workspace\venv python.exe)  --child-->  21472 (Python312 python.exe)
SCHEDULER  22180 (.ai-workspace\venv python.exe)  --child-->  26212 (Python312 python.exe)
           (both shims' parent 11520 has exited)
```

### Evidence that 26212 is the sole writer

`scheduler_leases` (212 rows) has exactly **one** owner across the whole table:
`ssss:26212:ab274c4d8878`, pid 26212. Non-expired leases at audit time:

```
automated_trading_v2_cycle    hb 01:09  exp 01:12
paper_runtime_cycle           hb 01:09  exp 01:12
paper_observation_cycle       hb 01:07  exp 01:10
automated_trading_v2:BINANCE_TESTNET:single_writer
```

`logs/scheduler-state.json`: `running=true`, `scheduler_instance_id` =
`current_lock_owner` = `ssss:26212:ab274c4d8878`. The single-writer lease mechanism
is functioning; there is no duplicate-writer condition to repair.

Process resource profile confirms the shim is inert (never ran a loop):

| PID | Threads | Handles | WorkingSet | UserTime | KernelTime |
|---|---|---|---|---|---|
| 22180 (shim) | 1 | 57 | 2.9 MB | **0** | **0** |
| 26212 (real) | 31 | 685 | 363 MB | 18137968750 | 4977656250 |

`scripts/run-local-paper-scheduler.py` contains no `subprocess`/`Popen`/respawn
logic — it is a plain `asyncio.run`. So killing 26212 cannot be undone by 22180.

## Authorization delta — why the force-kill did NOT run

Operator rule: *"执行前必须再次确认这 5 个 PID 的当前 command line / executable
identity 没有被系统复用。如果任何 PID 已经不是你描述的那个进程，STOP，不按旧 PID 强杀。"*

PID 22088 is now an unrelated project's process. **STOP condition met → no `/F` issued.**

Revised set needed for a true zero-writer window:

- `26212` — authorized + verified. The actual writer.
- `21472`, `24644` — authorized + verified. API pair.
- `22180` — **NOT in the authorized list.** Scheduler's shim parent, structurally
  identical to the already-authorized `24644`. Needs an explicit operator ACK.
- `22088` — **excluded permanently** (reused).
- `25708` — moot (gone).

## Progress against the operator-locked sequence

| # | Step | State |
|---|---|---|
| 1 | taskkill /F scheduler | **DONE** — only PID 26212 (the sole writer). Shim 22180 self-exited |
| 2 | clean API child + wrapper | **DONE** — 21472 killed; 24644 self-exited with it |
| 3 | wait | DONE |
| 4 | confirm writer=0, 8016=0, cycles frozen | **PASS** — leases 0, processes 0, `:8016` 0, `scheduler_cycles` frozen at 32252 |
| 5 | read-only DB health | **PASS** — `quick_check=ok`, `journal_mode=delete`, no `-wal`/`-shm`/`-journal` |
| 6 | re-run collision audit (P0-1) | **PASS** — re-ran in the writer-zero window |
| 7 | require SAFE_PLAIN_MIGRATION | **SAFE_PLAIN_MIGRATION** — canonical 0, overlap 0, conflicting 0, internal 0 |
| 8 | backup after writers stopped | **DONE** — `.local_paper_console.db.backup_i1_20260809_015526` |
| 9 | backup SHA256 / size to disk | **DONE** — `fd1f4883…e296c9`, 341368832 bytes, `.sha256` sidecar + manifest |
| 10-13 | BEGIN IMMEDIATE → UPDATE → verify → COMMIT | **COMMIT** — 106292 rows renamed, fingerprint identical |
| 14 | restore via official launcher **once** | **DONE** — `一键启动.cmd` invoked once, EXITCODE=0, `reclaim_stale_scheduler_locks` reclaimed 213 stale leases |
| 15 | post-start verification | **PASS** — 4 independent gates, see below |

### Step 15 — post-start verification (all PASS)

| Gate | Script | Result |
|---|---|---|
| Post-start audit | `i1_post_start_audit_readonly.py` | **PASS** — canonical invariant holds under live writes, single writer, position+protection intact, `entry_enabled=0` |
| Read-path acceptance | `i1_readpath_acceptance_readonly.py` | **PASS** — this is the defect I-1 existed to fix |
| Regression comparison | `i1_regression_compare_readonly.py` | **PASS** — no unprecedented terminal or rejection reason |
| Exchange protection | `i1_exchange_protection_evidence.py` | **PASS** — both SL/TP orders LIVE on Binance Testnet, zero ghost positions |

Restored topology — **1 scheduler + 1 API**, down from the pre-window dual-writer state:

```
API        32280 (venv shim)  --child-->  30660 (base interpreter, owns :8016)
SCHEDULER   7584 (venv shim)  --child-->  32132 (base interpreter, sole lease owner)
```

`scheduler_leases` has exactly one heartbeating identity: `ssss:32132:f4d2d32c832d`.

**Read-path proof (the actual payoff).** Pre-migration both probe forms returned 0 rows:

```
probe=BTC/USDT:USDT -> WHERE symbol='BTC/USDT': rows=10634 funding=10634 oi=0
probe=BTC/USDT      -> WHERE symbol='BTC/USDT': rows=10634 funding=10634 oi=0
probe=ETH/USDT:USDT -> WHERE symbol='ETH/USDT': rows=10634 funding=10634 oi=0
```

**Exchange protection evidence** (Binance Testnet, read-only):

```
STOP_LOSS    1000000160188648  A2S-9dc0d177295fb3ebf8  in_open_orders=True  status='new'  LIVE
TAKE_PROFIT  1000000160188659  A2T-8e98c64793d42fb9ac  in_open_orders=True  status='new'  LIVE
local managed open positions: 1    exchange open positions: 2
unmanaged external (manual, quarantined): ETH/USDT short 10.976 @1934.0
GHOST (local row with no exchange position): none
```

### Honest caveats — not fixed by I-1, do not claim otherwise

1. **`market_extras` is stale** — newest row `2026-07-26 08:32:20`. Live WebSocket
   collectors are disabled in this environment (`no Binance proxy configured`). I-1
   fixed the *symbol form* so the read path resolves; it did not resurrect the collector.
2. **Only `funding_rate` is populated.** `open_interest`, `long_ratio`, `short_ratio`,
   `liquidation_usd` are NULL for every row. A separate collection gap.
3. **"New writes are canonical" was not observed in the live runtime** — no
   funding/OI cycle fired post-restart (extras delta = 0), because of caveat 1. It is
   covered structurally instead: `repository.py` calls `canonical_market_symbol()` on
   the write path, and RT-01/02/03 assert it (6 passed).
4. **Binance Testnet cannot query conditional orders by `orderId`** — `fetch_order`
   returns `-2013 Order does not exist` for a live stop order. Query by
   `clientOrderId` and cross-check the open-orders list instead. Not a defect.
5. **Pre-existing, untouched by I-1:** `paper_runtime_cycle` logs
   `binanceusdm cancelOrder() requires a symbol argument` on ETH/USDT, and
   reconcile reports `DEGRADED`. Both live in the frozen legacy paper pipeline.

### Migration result (steps 10-13)

```
rows updated                : 106292   (all legacy rows)
total rows                  : 106292   (unchanged)
legacy rows / LIKE '%:USDT' : 0 / 0
duplicate (symbol,time)     : 0
distinct symbols            : 10  ADA AVAX BNB BTC DOGE ETH LINK SOL TRX XRP (all /USDT)
market_extras fingerprint   : 03cfb98153782a512daea44346f03c51822da009830e5326c75b6e331296daba
                              identical pre/post -> proven pure rename
ohlcv_bars fingerprint      : b5e7544c6d7e4995d6d040b135d81cd37a75d67415139176179c121e68658d88
                              identical, 232814 rows -> untouched
post-commit fresh process   : POST_MIGRATION = PASS, quick_check = ok
RT-01/02/03                 : 6 passed
```

Position and protection unchanged throughout: `BTC/USDT long 0.0388 @64996.2 PROTECTED`,
`SL 64768.9 @1000000160188648`, `TP 65337.2 @1000000160188659`.

Rollback path if post-start verification fails: restore
`.local_paper_console.db.backup_i1_20260809_015526` (hash above) and revert the write-side
code. Per D-A, a legacy reader fallback is FORBIDDEN — rollback is backup-only.

Stop conditions unchanged: after writers stop, if re-audit yields
`canonical_rows > 0` or `overlap > 0` or `conflicting > 0`, do **not** run
SAFE_PLAIN_MIGRATION. `conflicting > 0` → STOP, no auto-merge. The 106,292 figure
is preflight fact, not migration-time truth.

`entry_enabled` stays `false` through the entire post-start verification. Restoring
the API/scheduler does not authorize re-enabling entry.

## Out of scope — do not investigate inside I-1

- `v2_execution_cycles` frozen at 13695 while `scheduler_cycles` advances:
  `OUT_OF_T0_SCOPE`, `NOT_INVESTIGATED`, `NO_CAUSAL_LINK_TO_I1_ESTABLISHED`.
- If the restored topology again shows extra schedulers: record
  `RUNTIME_PROCESS_TOPOLOGY_ANOMALY` / `OUT_OF_I1_SCOPE`, but if it implies a real
  duplicate-writer risk, keep `entry_enabled=false` and stop I-1 wrap-up.
- `research_shadow` emits real decisions (`candidate_conditions_not_met`,
  `selection=EMPTY`). Activation is `SHADOW`; R-1 is `REMOVE_FROM_T0_BLOCKERS`.

## Read-only tools added this window

- `scripts/i1_runtime_state_snapshot.py` — step-1 state capture (mode=ro)
- `scripts/i1_writer_audit_readonly.py` — lease/instance table discovery (mode=ro)
- `scripts/i1_live_writers_readonly.py` — re-runnable WRITER_ZERO gate (mode=ro).
  Note: its process filter must keep the `Name -match 'python'` clause, otherwise
  the helper shell it spawns self-matches and the gate can never PASS.
- `scripts/i1_prekill_guard_readonly.py` — pre-kill guard (entry off, SL/TP present)
- `scripts/i1_db_health_readonly.py` — `quick_check` + file-family check (mode=ro)
- `scripts/i1_collision_audit_readonly.py` — P0-1 re-audit at migration time (mode=ro)
- `scripts/i1_backup_db.py` — hash-verified backup, refuses to overwrite
- `scripts/i1_migrate_market_extras_canonical.py` — **the only writer**; `BEGIN IMMEDIATE`,
  in-transaction verification, COMMIT or ROLLBACK
- `scripts/i1_post_migration_verify_readonly.py` — fresh-process post-commit check
- `scripts/i1_post_start_audit_readonly.py` — step-15 audit (mode=ro)
- `scripts/i1_readpath_acceptance_readonly.py` — step-15 read-path acceptance (mode=ro)
- `scripts/i1_regression_compare_readonly.py` — step-15 pre/post regression diff (mode=ro)
- `scripts/i1_exchange_protection_evidence.py` — step-15 exchange truth check (read-only network)
- `scripts/i1_post_toggle_acceptance_readonly.py` — post-entry-enable acceptance. Takes
  `<baseline_cycle_count> <toggle_at_utc>`; fails on I-1-caused anomalies only, and records
  the known-unresolved items without failing on them.

## Entry restore (close-out, operator-authorized)

Done through the API — **not** a direct DB write. `updated_by='api'` in
`v2_runtime_controls` is the proof.

```
POST /api/v2/automated-trading/controls/entry-enable
body: {"reason": "i1_symbol_canonical_migration_complete_restore_pre_maintenance_state"}
-> {"success": true, "entry_enabled": true, "changed_at": "2026-08-09T03:00:27.763799Z"}

control-plane read-back (GET /cycles): entry_enabled = True
DB projection: entry_enabled=1, updated_by='api', version 2 -> 3
maintenance reason 'i1_symbol_canonical_migration_window' replaced
```

If a *new I-1-related* anomaly ever appears, re-arm the lock with
`POST /api/v2/automated-trading/controls/entry-disable` and stop. `MARKET_EXTRAS_STALE`,
`OI/long-short NULL`, and `V2 cycle not advancing` are **not** grounds to re-close entry —
none were introduced by I-1.

### Post-toggle acceptance (step 5) = PASS

Observed 24+ cycles (needed 2). Single writer `ssss:32132:f4d2d32c832d`. Zero new managed
positions and zero `validated_edge` decisions since the toggle, confirming the primary
candidate is still fail-closed and that restoring the global lock granted it nothing.
No symbol/`market_extras`-shaped rejections; legacy rows still 0.

## Material event during the window: the BTC stop loss FIRED

Discovered by the step-5 gate. **Not** caused by the toggle and **not** caused by I-1 —
the stop filled 13 minutes *before* entry was re-enabled, on genuine adverse price action.

| Time (UTC) | Event |
|---|---|
| 02:31 | step-15 gate: SL + TP both LIVE (correct at that moment) |
| 02:44–02:47 | BTC 1m lows fell 64788 → 64760.5, crossing the 64768.9 stop trigger |
| **02:47:08.700** | **stop filled** @ 64768.6, qty 0.0388, fee 1.00520867, order `28533281387`, trade `525914324` |
| ~02:47 | exchange OCO-cancelled the TP leg; BTC position flat |
| 03:00:27 | entry re-enabled (13 min later) |

Realized ≈ `(64768.6 − 64996.2) × 0.0388 − 2.01394969 fees` ≈ **−10.84 USDT**. Protection
worked exactly as designed.

### Underlying defect found — `UNRECONCILED_EXIT` (escalate separately, NOT I-1)

The local projection never ingested the exit:

```
stop fill 28533281387 recorded in v2_exchange_fills? -> 0
any reduce_only BTC/USDT fill locally?               -> 0
v2_managed_positions.closed_at                       -> NULL   (still "open")
v2_managed_positions.state                           -> QUARANTINED
v2_protection_records.state                          -> PROTECTION_ACTIVE (points at a dead algo order)
```

The reconciler *detected* the divergence (hence `QUARANTINED`) but did not close the
position, book the exit fill, or compute realized PnL. That violates the Exchange-First
invariant #4 (exit price / filled qty / fees / realized PnL must come from Binance
execution data).

Why this is not I-1-caused and does not justify re-arming the I-1 lock:

- the reconcile path never reads `market_extras`; symbol form has no bearing on how
  Binance triggers a conditional order;
- the `02:19` scheduler log already showed `no exact managed position identity; protection
  refresh and emergency close disabled` **before** the stop fired;
- the stop fill predates the toggle by 13 minutes.

### Two exchange-behaviour traps this exposed

1. **A Binance algo/conditional order id is not the executed order's id.** `1000000160188648`
   is the algo id; the resulting market order was `28533281387`. `fetch_order(algo_id)`
   returns `-2013 Order does not exist` even while the protection is perfectly healthy.
   Resolve protection state by **clientOrderId**, never by algo id alone.
2. **An OCO sibling resolves to `status=None` after the other leg fires.** Binance purges
   the cancelled reduce-only leg from queryable history. "Vanished" + "sibling filled" +
   "position flat" = normal, not a protection failure. A naive gate reports this as a
   critical loss of protection — mine did, on the first run, before being corrected.

### Two measurement traps found while writing the step-15 gates

Both produced a false FAIL before being corrected. Anyone re-running these should know:

1. **Per-cycle leases expire between cycles.** A point-in-time "non-expired lease
   count" is legitimately `0` when sampled between cycles, so it is *not* a writer
   signal. Count **distinct owner identities heartbeating in a recent window** instead.
2. **A reason absent from an N-hour window is not a regression.** `RSI_OUTSIDE_RANGE`
   looked new against a 14 h pre-window but has **160 occurrences** since
   `2026-07-31 14:15:25`. Always test novelty against *all* history before the cut.
   Likewise a `NULL decision_terminal` is just an in-flight cycle.

Also note `ohlcv_bars` grows continuously (232814 → 232988 within 20 min of restart),
so any absolute row-count assertion against it goes stale immediately. Assert
append-only growth (`delta >= 0`), not equality.
