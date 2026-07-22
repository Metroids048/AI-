# Trade Burst Root Cause

The burst was not a strategy suddenly becoming high-frequency. `TestnetAcceptanceService.run()` deliberately opens, protects, immediately reduce-only closes, and cancels protection. The old CLI exposed that externally mutating workflow as an ordinary Agent validation command and randomized its idempotency key on every invocation.

Primary causal chain:

`Agent optimization/validation` → `run_testnet_acceptance.py` → `fresh random idempotency key` → `Binance Simulation open + protection + immediate close` → `reconciliation into audit-only strategy` → `exchange UI appears to show short-lived automated strategy trades`.

Independent deployment risk:

`8000 in-process scheduler` + `8016 external scheduler` → same database → no leader election → both may evaluate the same candle. Migration 0011 closes this with a lease and a unique scheduled-slot claim; the order table adds a second, candle-intent uniqueness barrier.

No trading threshold was changed as part of this repair.
