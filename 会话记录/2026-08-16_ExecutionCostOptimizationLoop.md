# Execution Cost Optimization Loop

## Scope

- Corrected the loop boundary: the current protected BTC/USDT position is runtime-health observation only, not a research or completion blocker.
- No Testnet order, local position mutation, protection change, runtime setting, risk threshold, stop, target, or active manifest was changed.
- Final Holdout was not accessed.

## Evidence

- `docs/audits/2026-08-16-execution-cost-root-cause.json`: 30 closed episodes, 15 STOP / 15 TARGET, 30/30 exact exit-order/protection-trigger linkage, zero abnormal exits, and zero quantity mismatches.
- Observed commission is 4bps per side and `0.22834078R` per trade after risk normalization. Mean trigger-to-fill effect is `-0.17464695R`; `risk_pct` versus commission-R correlation is `-0.96346`, with 23/30 floor-bound episodes.
- `artifacts/active_strategy_optimization/atr_native_only_cost_calibrated_20260816.json`: the one-variable `ATR_NATIVE_ONLY_FILTER` challenger retained all existing signal, side, P1, TP, R2, sizing and execution rules. It failed BTC OOS (`371`, expectancy `-0.30199679R`, PF `0.61451`, LCB95 `-0.40543R`) and ETH OOS (`548`, expectancy `-0.27896443R`, PF `0.63789`, LCB95 `-0.36303R`). At 1.5x cost stress the combined OOS expectancy is `-0.44789986R` and PF `0.48497`.
- `docs/audits/2026-08-16-maker-limit-data-availability.json`: historical maker/limit replay is blocked by absent order-book/depth/quote fields; queue/fill probability, timeout, adverse selection and drift cannot be fabricated.
- Existing Policy A-E evidence remains proxy-only: Policy C led a 9-trade sample but is a 2x ATR/3x ATR stand-in, not active-lane OOS promotion evidence.

## Checkpoint

- `LOOP_LEDGER.json` and `FINAL_STATUS.json` now point to the next recoverable machine action: persist order-book snapshots, replay a maker/limit model only once fill/timeout/adverse-selection evidence exists, otherwise continue new-data signal research.
- `C:\Users\Windows11\.ai-workspace\scripts\sync-from-repo.ps1` could not run because it references a missing `C:\Users\Windows11\Desktop\Agent Platform\scripts\global-workspace\install-global-workspace.ps1` installation path.

## Verification

- Focused `ruff`: pass.
- Focused `mypy`: pass for four scripts.
- Focused pytest: `35 passed`.
- Full pytest: `1629 passed, 7 skipped`.
- Full `ruff check .`: existing unrelated `C416` in `scripts/verify_gate17_e2e.py:77`.
- Full `mypy .`: existing duplicate `check_positions` module between root and archived scripts.
