# P2-A / Binance PF Cohort Alignment

## Verdict

`COHORT_MISMATCH_CONFIRMED` — do not run the funding-only experiment yet.

## Definitions

- P2-A replay PF `1.1122990302449454`: Policy A replay over 30 local `CLOSED` positions; modeled policy outcomes and modeled fee drag.
- Aligned actual PF: the same 30 local positions mapped to exchange entry/protection fills; realized PnL minus exchange commissions, before funding.
- Audit PF `0.3965963545259739`: 32 exchange `TradeEpisode` rows carrying local strategy context; `net_pnl` includes funding.

## Cohort evidence

- P2-A rows: 30; DB candidate positions: 34 ({'CLOSED': 30, 'QUARANTINED': 3, 'PROTECTED': 1}).
- Audit V2 closed episodes: 32.
- Audit extras not in P2-A: 3; these are the quarantined entries at 2026-08-11 07:45:41 and 2026-08-14 16:00:16/16:00:25.
- One P2-A position (ETH 2026-08-10 10:15:44.664000, intent `e80dc888-ebc8-4102-9fd3-a5971f604680`) has no standalone audit episode.
- The missing standalone episode is an episode-construction issue, not evidence that the exchange fill is absent: the account-level episode builder groups same-symbol fills into one lifecycle and absorbs that ETH short into an earlier episode with no V2 strategy context.

## PF result

- Aligned actual pre-funding PF: `0.5091494675506176734448906898` over 30 positions.
- P2-A decomposition normalized-R PF: `0.7533482588333641490861885229` over 30 positions. The previously cited `0.48688` is not present in the current repository/artifacts and is not reproducible from these rows; treat it as unsupported until its source/formula is recovered.
- Aligned actual pre-funding totals: gross `-172.68811013` USDT, commission `243.44599654` USDT, net `-416.13410667` USDT.
- This is the only actual PF that is cohort-comparable to the 30-position P2-A decomposition.
- Funding window matching has since been attempted; the account-level income ledger is not uniquely position-attributable, so no funding-only experiment is authorized.

## Decision

The 1.11 vs 0.3966 gap cannot be attributed to funding alone. First compare the 30-position aligned actual PF against replay using the same realized exchange fills and explicit commission/slippage assumptions; then add point-in-time funding per position. The 32-episode audit PF remains a separate account-episode metric until episode splitting is repaired or a position-keyed mapping is used.
