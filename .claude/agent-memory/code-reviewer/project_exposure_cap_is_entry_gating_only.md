---
name: exposure-cap-is-entry-gating-only
description: In the V2 active chain max_position_fraction is a sizing clamp only, not a gate; Gatekeeper._evaluate_numeric_risk is unreachable from V2
metadata:
  type: project
---

`max_symbol_exposure` / `max_position_fraction` **cannot reject a V2 entry and cannot
reduce or close an open position**. In the V2 chain it is only a `min()` clamp, so
lowering it makes new entries *smaller* rather than blocking them.

**Why:** verified consumer map —
- `cycle_service._calculate_quantity` is the only functional consumer, as
  `exposure_ceiling = equity * request.max_position_fraction`, applied via
  `min(risk_notional, exposure_ceiling, margin_ceiling)`. Single call site on the
  entry path (`entry_notional = ...`).
- `services/automated_trading/` contains **zero** references to the Gatekeeper, and
  `ExecutionGatekeeperService.validate_order` (the only dispatcher to
  `validate_entry`) has **no production caller**. The
  `max_symbol_exposure_exceeded` check in `gatekeeper._evaluate_numeric_risk` is
  therefore unreachable from V2. The legacy `paper_cycle_orchestrator` that does use
  it is contractually barred on the ACTIVE lane: `runtime_state.py` raises
  `LEGACY_JOB_REGISTERED` / `LEGACY_WRITER_ENABLED` if `paper_runtime_cycle` /
  `paper_observation_cycle` are registered or the legacy writer is on.
- V2's real entry gate is `entry_service.py` (~145-189): unmanaged-external-position,
  `POSITION_ALREADY_OPEN`, cooldown, daily-trade-limit, manifest eligibility,
  net-edge, a boolean `risk_budget_available`, AI veto, candidate expiry. **No
  exposure-fraction comparison anywhere.**
- `_evaluate_numeric_risk` also returns `[]` under `close_only_mode` and is never
  called from `validate_reduce_risk_exit`. `exit_service.py` references no exposure or
  leverage token. `tasks.risk_profile_sweep` gates on drawdown / consecutive-loss /
  api-failure and only emits a `RISK_LIMIT_BREACH` event with
  `recommended_action="pause_strategy"` — never exposure, never a close.

Two non-obvious consequences worth re-checking each time:
1. `_evaluate_numeric_risk` reads `max_symbol_exposure` from the **DB RiskProfile**,
   not from `PaperRun.execution_profile`. Editing the operator profile changes entry
   *sizing* and leaves the Gatekeeper exposure *gate* at its DB value.
2. `tasks.refresh_volatility_asset_risk_tiers` (weekly) overwrites
   `execution_profile["asset_risk_tiers"]` wholesale from
   `VOLATILITY_TIER_DEFAULTS`, so per-tier leverage edits silently revert.
   Profile-wide `max_symbol_exposure` survives (it is in
   `bootstrap.OPERATOR_AUTO_SETTING_KEYS` and applies as
   `min(tier.max_position_fraction, profile_exposure)` in `resolve_v2_execution_settings`).

**How to apply:** Reuse this map instead of re-deriving it. When someone claims an
exposure change will stall entries or unwind positions, establish which lane they mean
first — such claims are usually about the legacy Gatekeeper path, which is not the
ACTIVE writer. "Entry chain stalls while BTC/ETH hold positions" is true but caused by
`POSITION_ALREADY_OPEN`, not by the exposure cap. Re-verify point 2 before claiming any
per-tier leverage change is durable.
