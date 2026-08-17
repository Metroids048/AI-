# Exit Order Fill Lineage

- Status: `READ_ONLY`; episodes `30`; closed `30`.
- SQLite was opened read-only; no exchange or local execution state was changed.

## Waterfall (R)

| Loss source | Total R |
| --- | ---: |
| abnormal_exits | 0 |
| cohort_data_mismatch | 0 |
| commission | -6.854805209546915586574987740 |
| entry_execution | 7.342671237986427618328956333 |
| entry_slippage | 0 |
| exit_trigger_geometry | 0 |
| funding_attributable | 0 |
| intrabar_timing | 0 |
| partial_fill | 0 |
| profit_protection | 0 |
| trigger_to_fill_slippage | -5.239408596450989573630618130 |
| unknown_residual | -1.291989096038500326295248051 |

## Exit Reasons

- `STOP`: 15
- `TARGET`: 15

## Supplemental natural exit

The fixed 30-episode replay cohort is unchanged. A read-only supplemental episode was
added in `2026-08-16-exit-order-fill-lineage-natural-addendum.json` for BTC/USDT position
`0e3814c7-d23a-48a8-bef6-d240fc809b66`: stop `1000000168673444` triggered a matched
reduce-only exit `28542950261` / trade `527891302` at `62975.9` on 2026-08-16 09:57:37Z.
The replay parity input has no R0-R3 row for this episode, so no synthetic waterfall
metrics were added.
