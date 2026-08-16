# 2026-08-16 Microstructure pipeline

- Implemented independent Binance Testnet order-book collector for BTC/USDT and ETH/USDT.
- Added persistent snapshots, validation/deduplication, health metrics, retention, checkpoint recovery, readiness gate, maker/limit replay primitives, and replay/readiness CLIs.
- Wired collector into `scripts/launch-paper-console.ps1` as a separate hidden process with PID/log recovery.
- Real public Testnet verification: 3 valid rows per symbol; scheduler state remained `running=true`, `execution_coverage_count=2`, `reconciliation=HEALTHY`.
- Readiness gate is not yet met: 82 candidate windows (BTC 42, ETH 40) are present, but candidate-window microstructure coverage is 0% after the initial sample. No strategy, risk, geometry, execution, or current position was changed.
- Verification: focused tests `3 passed`; full suite `1632 passed, 7 skipped`; focused ruff passed; full-repo ruff retains the known unrelated `C416` and full mypy retains the known duplicate `check_positions.py` module; `git diff --check` passed.
- Final state: `MICROSTRUCTURE_PIPELINE_READY_AND_COLLECTING`.
