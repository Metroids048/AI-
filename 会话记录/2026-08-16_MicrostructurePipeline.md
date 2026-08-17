# 2026-08-16 Microstructure pipeline

- Implemented independent Binance Testnet order-book collector for BTC/USDT and ETH/USDT.
- Added persistent snapshots, validation/deduplication, health metrics, retention, checkpoint recovery, readiness gate, maker/limit replay primitives, and replay/readiness CLIs.
- Wired collector into `scripts/launch-paper-console.ps1` as a separate hidden process with PID/log recovery.
- Real public Testnet verification: 3 valid rows per symbol; scheduler state remained `running=true`, `execution_coverage_count=2`, `reconciliation=HEALTHY`.
- Readiness gate is not yet met after scope correction: 2 post-start candidate windows (BTC 2, ETH 0) overlap collector uptime and coverage is 100%. No strategy, risk, geometry, execution, or current position was changed.
- Verification: focused tests `4 passed`; full suite `1633 passed, 7 skipped`; focused ruff passed; full-repo ruff retains the known unrelated `C416` and full mypy retains the known duplicate `check_positions.py` module; `git diff --check` passed.
- Coverage audit classified the former 0% as an implementation scope bug, fixed by excluding candidates whose ±5m window predates collector startup. Dry-run MARKET control and maker/limit fallback both completed on real snapshots without synthetic order-book data.
- Final state: `MICROSTRUCTURE_PIPELINE_READY_AND_COLLECTING`.
