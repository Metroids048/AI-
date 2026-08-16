# P2-A Point-in-Time Funding Attribution

## Verdict

`FUNDING_WINDOW_MATCHED_ACCOUNT_LEVEL_AMBIGUOUS` — exchange funding events overlap the same 30 position windows, but the income ledger is account-level and cannot be uniquely assigned to a strategy position from this artifact alone.

## Method

- Cohort: the same 30 P2-A local `CLOSED` `testnet_sampling_v2` positions.
- Funding source: `binance_income.jsonl`, `incomeType=FUNDING_FEE`.
- Assignment window: `entry_fill_timestamp < event_time <= closed_at`.
- Negative funding is paid funding. Events are only window-matched by symbol/time; account-level income is not uniquely strategy-position attributable when external exposure or stale snapshots are present.

## Results

- Matched funding events: `3` across `3` of `30` positions; `3` have stale (>30m) snapshot context.
- Funding: `1.33323099` USDT; explicit commission: `243.4459965400000006` USDT; funding share: `0.005446667200698637635462921585`.
- Normalized-R PF: `0.7533482588333641490861885229` before funding -> `0.7518753903283085473423766035` after funding.
- Aligned actual USDT PF: `0.5091494675506176734448906898` before funding -> `0.5098020735012275949468888697` after funding.
- Aligned actual net PnL: `-416.13410667` USDT before funding -> `-414.80087568` USDT after funding.

## Interpretation

The naive window sum is not a valid strategy-only funding attribution: the income ledger is account-level, and contemporaneous position snapshots are stale or show external/contradictory exposure. Therefore the post-funding PF is illustrative only and must not drive a funding-only experiment. The 32-episode `0.3966` remains a separate account-episode metric until position-keyed exchange income or a trustworthy exposure ledger is available.
