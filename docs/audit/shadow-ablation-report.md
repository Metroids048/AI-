# Strategy Shadow Ablation

- Generated: 2026-07-22T08:48:49.399446+00:00
- Since: 2026-07-15T08:48:49.329389+00:00
- Decisions: 699
- This is candidate recall only; PnL, 1R/2R, expectancy, and drawdown require persisted exits and complete outcome bars.
- `unknown` is retained when historical traces cannot reconstruct a variant without guessing.

| variant | evaluated | candidates | blocked | unknown | long | short |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| A_CURRENT_PRODUCTION | 699 | 80 | 619 | 0 | 69 | 11 |
| B_NO_LLM_HARD_VETO | 699 | 82 | 617 | 0 | 71 | 11 |
| C_WEIGHTED_ENSEMBLE | 699 | 303 | 393 | 3 | 195 | 108 |
| D_HIERARCHICAL_MTF | 699 | 80 | 429 | 190 | 69 | 11 |
| E_COMBINED_BCD | 699 | 305 | 201 | 193 | 197 | 108 |
