# ExitLadder vs Fixed 2R Replay (same layered entry)

- Generated at: 2026-07-12T14:52:05.711803+00:00
- Candidate entry: `auto_paper_mature_templates` (4h/1h/15m + layered_regime_entry)
- Automatic Paper/Testnet settings: unchanged
- Promotion: **not allowed** (evidence only)

## Comparison

| Metric | Fixed 2R | ExitLadder |
| --- | ---: | ---: |
| Signals | 1004 | 429 |
| Trade slices | 1004 | 810 |
| Win rate | 0.4084 | 0.6654 |
| Net return | 2.193765 | -0.701726 |
| Net expectancy | 0.002185 | -0.000866 |
| Profit factor | 1.1308 | 0.8817 |
| Max drawdown | 0.5220 | 1.4356 |
| Avg hold hours | 22.73 | 43.21 |
| Ladder hits | {} | {'exit_ladder_1r': 224, 'exit_ladder_1.5r': 157} |

## Interpretation

- ExitLadder raises win rate (partial + BE) but **net expectancy and PF are worse** than fixed 2R on this window; max drawdown is worse.
- Fewer entry signals under ExitLadder because longer average holds block re-entry while a position is open.
- Ladder mechanics fired as designed (224× 1.0R, 157× 1.5R).
- **Do not change** automatic strategy enablement or Testnet arming based on this report.

## Notes

- Fixed 2R path isolates entry quality (legacy prescreen).
- ExitLadder path uses `AUTO_PAPER_TECHNICAL_RULES.takeprofit_rules.exit_ladder`.
- Replay code path: `TechnicalStrategyValidationService(exit_mode="exit_ladder"|"fixed_2r")`.
