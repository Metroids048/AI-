# Runtime P1 Parity Audit

- Status: `COMPLETE`; cohort `30`; valid actual-exit rows `30`.
- Read-only: SQLite opened with `mode=ro`; no execution or risk configuration changed.

## Mean R Waterfall

- R0 static Policy A replay net: `0.04783905R`
- R1 actual-entry static path gross: `0.29142342R`
- R2 dynamic P1 path gross: `0.24945516R`
- R3 actual exchange gross: `0.07374063R`; commission-net: `-0.15460015R`
- Stage PF (USDT): R0 gross `1.112299030244945435308432997`, R0 net `0.7006809911550145205956756106`, R1 `1.000552546900326372559781992`, R2 `1.431104725467372972488247009`, R3 gross `0.7237736617690017591932355455`, R3 net `0.4868816838630034039781938433`
- R1→R2 P1 effect: `-0.04196825R`
- R2→R3 execution/fill effect: `-0.40405532R`

## P1 Evidence

- Simulated P1-triggered rows: `21`; triggered but did not reach target: `12`.
- Historical `ProfitProtectionStopTightened` rows: `0`; events: `0`.
- The P1 policy label on a protection record is not treated as proof of a replacement; only the explicit protection event is.

## Decision

This artifact is the parity gate. If R2 remains materially below R3, continue exchange-fill/order-identity attribution before any signal or funding experiment. If historical replacement events are zero while simulated triggers are common, the next question is runtime observation cadence/mark-price availability, not signal quality.
