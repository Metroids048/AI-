# P2-B Embedded Same-Cycle Research Shadow

## Verdict

`P2B_EMBEDDED_SHADOW_CONFIRMED` — the three candidates are persisted under `v2_execution_decisions.payload.research_shadow`; the legacy `v2_shadow_records` table is not the active storage path.

## Scope

- Cutover boundary: `2026-08-10T13:15:03.869648+00:00`.
- Decision rows after cutover: `8678`; malformed payloads: `0`.
- Legacy `v2_shadow_records` total (all history): `454`.

## Counts

- Raw embedded observations: `26001`; unique by `(strategy, symbol, bar_close_time, context_hash)`: `5607`.
- `trend_pullback_v2`: raw `8667`, unique `1869`, statuses `{'SHADOW_NO_SIGNAL': 1869}`, terminal reasons `{'candidate_conditions_not_met': 1869}`.
- `range_sweep_reversion_v1`: raw `8667`, unique `1869`, statuses `{'SHADOW_NO_SIGNAL': 1869}`, terminal reasons `{'candidate_conditions_not_met': 1869}`.
- `failed_breakout_reversal_v1`: raw `8667`, unique `1869`, statuses `{'SHADOW_NO_SIGNAL': 1848, 'SHADOW_SIGNAL_READY': 16, 'SHADOW_STRATEGY_REJECTED': 5}`, terminal reasons `{'candidate_conditions_not_met': 1848, 'SHADOW_SIGNAL_READY': 16, 'selected_score_below_threshold': 5}`.

## Interpretation

The zero-row observation came from querying the wrong table. Embedded observations exist for all three candidates. Their dominant state is `SHADOW_NO_SIGNAL` with `candidate_conditions_not_met`, so the current evidence points to low signal incidence rather than a disconnected shadow writer. The raw/unique split is reported because duplicate decision rows exist in the historical database.
