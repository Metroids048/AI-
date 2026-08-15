# Active Strategy Optimization Goal

The only completion condition is `FINAL_STATUS.json` with `success: true` and
`status: ACTIVE_STRATEGY_OPTIMIZED_AND_TESTNET_CLOSED_LOOP_VALIDATED`.

The supervisor must keep resuming the same Codex thread until that condition is
met. A turn response, an analysis artifact, a rejected hypothesis, or a test
result is never completion.

## Required Business Outcome

1. Reconstruct and attribute real `testnet_sampling_v2` Testnet episodes.
2. Improve the active strategy from evidence, with chronological OOS validation.
3. Update the existing active `testnet_sampling_v2` only after the policy passes.
4. Preserve `R2_MIN_THEORETICAL_NET_PAYOFF=1.15`, R1 semantics, Binance
   exchange-first execution, and Testnet-only operation.
5. Commit and push the validated strategy.
6. Restart the official runtime and prove a natural closed-bar Candidate reaches
   R1, R2, Intent, Binance Testnet fill, and Protection.
7. Keep the same frozen strategy running until a natural automatic reduce-only
   exit fills and Binance/local positions are flat with HEALTHY reconciliation.

## Hard Limits

- Never use mainnet, manual Candidates, manual orders, manual closes, direct DB
  writes, future bars, or relaxed R2 to manufacture evidence.
- Do not modify R1, the R2 threshold, execution idempotency, reconciliation, or
  Binance adapter semantics unless a red test proves a direct defect.
- Do not use unlimited parameter mining. Every attempted policy must be recorded
  in `LOOP_LEDGER.json` with its evidence, development result, OOS result, and
  accept/reject reason.

## Per-Turn Contract

Before work, read this file, `FINAL_RESUME_STATE.json`, `LOOP_LEDGER.json`,
`FINAL_STATUS.json`, current git state, and live runtime state. Execute exactly
the current `next_machine_action`, update the JSON state atomically, then check
whether the success condition is satisfied. If it is not, leave a concrete
machine action in the Ledger for the supervisor's next call. Do not report a
stage result as delivery.
