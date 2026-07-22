# Scheduler Liveness Report

## Historical ledger

- The system produced decisions outside the trade bursts; order silence was mostly decision-layer rejection, not total scheduler silence.
- Historical decision-cycle gaps of 481.289 and 459.545 minutes show that older deployments were not continuously healthy.
- The current audit keeps UTC as the database/server truth and renders Asia/Shanghai separately. It does not infer one timezone from another.

## Post-fix invariants

- No immediate Paper cycle at startup.
- One database lease owner for `paper_runtime_cycle`.
- One persisted winner per `(job_name, scheduled_for)`.
- One entry order per strategy/symbol/timeframe/closed-candle/intent type.
- Every scheduler-originated order carries deployment and process provenance.
- “No order” is explained with persisted decision blockers; it is not reported as scheduler death unless heartbeats/cycles are absent.

## Live restart and two-instance observation

- Port 8000 API health returned `status=ok` after the final restart.
- The final scheduler instance started with a fresh heartbeat and `last_auto_cycle_at=null`, so restart did not immediately execute Paper.
- During the deliberate two-instance overlap, the 02:40 UTC slot had exactly one persisted winner and zero duplicate slots. It completed successfully in about 64 seconds.
- The temporary competitor was stopped after the test; the final main instance remains running.
- Codex automation 24 now performs the non-mutating hourly wall-clock observations for 24 hours.
- A final read-only Binance account probe timed out at the 30-second network boundary. Local API health, market heartbeat, scheduler cycles, and database coordination remained healthy; the wall-clock monitor must retain this as an external-account observability gap rather than treating it as proof of a flat or disconnected account.

## Evidence

- `artifacts/scheduler-24h-verification.json`: accelerated 24-hour, two-instance result.
- `artifacts/scheduler-24h-slots.csv`: all 96 normalized slots.
- `artifacts/live-scheduler-coordination.json`: current runtime lease/slot snapshot.
- `artifacts/cycle-liveness.csv`: historical decision-cycle timestamps and gaps.
