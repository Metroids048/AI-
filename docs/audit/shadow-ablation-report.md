# Strategy Shadow Ablation

- Generated: 2026-07-23T08:15:27.562922+00:00
- Since: 2026-07-16T08:15:27.411731+00:00
- Decisions: 584
- This is candidate recall only; PnL, 1R/2R, expectancy, and drawdown require persisted exits and complete outcome bars.
- `unknown` is retained when historical traces cannot reconstruct a variant without guessing.

| variant | evaluated | candidates | blocked | unknown | long | short |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| A_CURRENT_PRODUCTION | 584 | 81 | 503 | 0 | 70 | 11 |
| B_NO_LLM_HARD_VETO | 584 | 81 | 503 | 0 | 70 | 11 |
| C_WEIGHTED_ENSEMBLE | 584 | 307 | 272 | 5 | 159 | 148 |
| D_HIERARCHICAL_MTF | 584 | 81 | 415 | 88 | 70 | 11 |
| E_COMBINED_BCD | 584 | 307 | 184 | 93 | 159 | 148 |
