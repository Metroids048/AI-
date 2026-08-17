# Daily Trading Review Design

**Date:** 2026-08-17
**Scope:** Read-only analytical projection for the existing Review Layer.

## 1. Report data model

`ReviewReport` remains the persisted report envelope. `report_date` is one complete UTC calendar day and `scope_type=daily`. Existing JSON fields carry the summary, strategy references, failure patterns, recommendations, and Chinese-first deviation lines. No new trading-truth table is introduced.

## 2. Aggregation pipeline

The service reads existing `V2ManagedPosition`, `V2ExecutionDecision`, and `FailureRecord` rows for `[00:00, 24:00)` UTC. It reports realized position PnL by the fixed five-symbol execution universe and emits an evidenced reason for symbols without realized PnL. Missing trade-level fee/funding/MFE/MAE fields remain absent/UNKNOWN rather than fabricated.

## 3. Scheduler job

The existing runtime scheduler owns the daily-review loop. The default schedule is UTC 00:15. The task defaults to the previous complete UTC day and accepts an explicit date for reruns. Review failure is contained by the scheduler's existing task wrapper and cannot stop automated trading.

## 4. API contract

Existing endpoints remain: `GET /api/v1/reviews`, `POST /api/v1/reviews/daily/{report_date}`, and `GET /api/v1/reviews/{id}`. The endpoint returns the domain `ReviewReport` envelope, not raw database rows. Explicit date validation remains at the route boundary.

## 5. `/review` frontend

The page keeps its existing React Query data sources and adds two read-only panels: the latest complete-UTC-day summary and the five-symbol PnL/no-trade evidence lines. Chinese labels are used for the primary answers; runtime decisions, exchange orders, and reconciliation remain visible below.

## 6. Idempotency

A daily report lookup by `(report_date, scope_type=daily)` returns the existing domain report on rerun. Explicit dates use the supplied day and never silently substitute the current day.

## 7. Error and UNKNOWN semantics

The service only emits values backed by persisted rows. Missing cost attribution, funding, stop geometry, and execution evidence are not inferred in the UI. Existing API errors remain visible, and scheduler failures are isolated from the trading cycle.

## 8. Tests

Backend tests cover previous-day default behavior through the task contract, explicit-date generation, idempotent rerun, five-symbol output, API generation, and scheduler registration. Frontend tests cover the Review page's runtime fallback and the production build verifies the new rendering compiles.

## Scope review

- No execution state machine, risk gate, exchange adapter, or manifest is modified.
- No second scheduler is introduced.
- No trading truth is copied into a parallel table.
- No promotion threshold or live geometry is changed.
- No secret or credential is added.
