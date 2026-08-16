# Execution Cost Root Cause

- Cohort: 30 closed episodes; lineage `READ_ONLY`.
- Mean commission: `0.228340782731806042R`; mean trigger-to-fill: `-0.174646953215032985787687271R`.
- Commission R p50/p75/p90/worst: `0.22847515322115738` / `0.22907209122853628` / `0.22929255471853477` / `0.2296959027895924`.
- Risk-percent vs commission-R correlation: `-0.9634615830676366784031357899`; floor-bound share: `0.7666666666666666666666666667`.
- Fee-rate check: `CONSISTENT` (entry `0.0003999999982336197909440326993`, exit `0.0003999999986276670736146000477`, expected `0.0004`).
- Unexplained residual: `-1.291989096038500326295248051R`.
- Next research-only variable: `ATR_NATIVE_ONLY_FILTER`.

The current live position is excluded from this analysis and remains runtime-health observation only.
