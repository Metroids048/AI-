# TASK-030 Security Scan Report

Date: 2026-07-07
Scope: repository-wide local scan for `C:\Users\win\Desktop\AI--main`

## Summary

- Project dependency audit: PASS. `py -3 -m pip_audit . --progress-spinner off --timeout 30` returned `No known vulnerabilities found`.
- Frontend dependency audit: PASS. `npm audit --audit-level=high` returned `found 0 vulnerabilities`.
- Secret scan: PASS for tracked source. No non-empty key/token/password values were found by the repository grep scan.
- Runtime scheduler risk: FIXED. Docker paper/live overlays now force `RUNTIME_SCHEDULER_MODE=celery` for `api`, `celery_worker`, and `celery_beat`.
- WebSocket reconnect visibility: FIXED. Binance live collector restart exceptions now update `LiveFeedBus` status to `reconnecting`.
- Third-party data visibility: IMPROVED. Validation, Review, Research, and Ops frontend pages now read real APIs; news and macro endpoints support `refresh=true` read-through ingestion.

## Commands

- `py -3 -m pytest -q` -> `149 passed, 1 skipped`
- `py -3 -m ruff check .` -> pass
- `py -3 -m mypy` -> pass
- `npm --workspace frontend/admin run test` -> `8 passed`
- `npm --workspace frontend/admin run build` -> pass on Vite `8.1.3`
- `npm audit --audit-level=high` -> `found 0 vulnerabilities`
- `py -3 scripts/compose_validate.py` -> skipped locally because Docker is not on PATH
- `py -3 -m pip_audit . --progress-spinner off --timeout 30` -> `No known vulnerabilities found`
- `git diff --check` -> no whitespace errors

## Findings

### Resolved

1. Python dependency audit no longer fails on pytest CVE-2025-71176. Dev dependency lower bound is now `pytest>=9.0.3`.
2. Frontend supply-chain audit no longer reports Vite/Vitest vulnerabilities. Vite/Vitest/React plugin/jsdom were upgraded and lockfile regenerated without `npm audit fix --force`.
3. Docker paper/live overlays no longer inherit `.env.example`'s local `inprocess` scheduler default.
4. Live feed reconnects are now operator-visible through `/api/v1/execution/trading-status` and `/api/v1/market/ohlcv/stream`.
5. Placeholder frontend entries were replaced with real data-backed pages for Validation, Review, Research, and Ops.

### Residual / Deferred

- `py -3 -m pip_audit --progress-spinner off` against the whole local Python installation still reports vulnerabilities in global packages not declared by this project: `litellm`, `nltk`, and `torch`. These are outside this repository's dependency graph, so they were not upgraded or removed in this task.
- Full `py -3 -m ruff format --check .` still reports historical formatting drift in files outside this change set. Changed Python files were formatted and pass targeted format check.
- Docker runtime validation remains unexecuted on this machine because Docker is not on PATH.

## Risk Notes

- API auth still uses the accepted single-tenant Bearer token model. No multi-user/RBAC scope was introduced.
- Binance REST/WS public reads remain the only exchange data path added here. No OKX/Bybit or mainnet live-trading expansion was made.
- The `refresh=true` news/macro paths fail soft: external fetch errors are returned as `refresh_error` while persisted data is still served.
