# Automated Trading V2 Architecture

> **Status:** Design frozen, implementation in progress (Task 0–18)
> **Scope:** BTC/USDT, ETH/USDT; Binance USDT-M Testnet only
> **Mainnet:** Not supported in V2
> **Source of truth:** [2026-07-27-automatic-trading-v2-final-rebuild-plan.md](../superpowers/plans/2026-07-27-automatic-trading-v2-final-rebuild-plan.md)

## Overview

V2 rebuilds the automatic trading pipeline from scratch to eliminate ghost positions, establish exchange-as-truth semantics, and provide complete observability. The legacy `paper_*` mixed pipeline is frozen; no new business logic may be added to it.

## Core Principles

1. **Exchange-First Execution:** No local `Managed Position` may exist without a real exchange fill receipt.
2. **Immutable Receipts:** Every exchange interaction produces an append-only receipt with exchange order ID, trade IDs, and fill data.
3. **Single Writer:** Only one engine may submit Testnet orders at any time.
4. **Fail-Closed Reconciliation:** When reconciliation is not `HEALTHY`, all new entries are blocked; reduce-only exits remain allowed.
5. **Entry/Exit Gate Separation:** Kill switches, AI vetoes, and manifest failures block entry but never block hard stop-loss or emergency exits.
6. **Local Paper and Binance Testnet are mutually exclusive:** No mirror, no fallback, no silent mode switching.

## Architecture Layers

```
services/automated_trading/
├── domain/          # Enums, commands, events, receipts, state, invariants
├── application/     # Cycle, decision, entry, exit, protection, reconciliation, recovery, AI review
├── infrastructure/  # Models, repository, Binance adapter, local Paper adapter, runtime lock
└── observability/   # Decision funnel, runtime snapshot, evidence bundle, metrics
```

## Non-Negotiable Constraints

See [the plan document](../superpowers/plans/2026-07-27-automatic-trading-v2-final-rebuild-plan.md) Section 1.3 for the full list of 15 global invariants.

Key highlights:

- `BINANCE_TESTNET` mode without real fill receipt → no V2 Managed Position in database
- Local `INTENT_CREATED`, `SUBMITTING`, `ACKNOWLEDGED` are not fills
- `FILLED` requires `exchange_order_id`, at least one `trade_id`, positive `filled_quantity`, positive `average_fill_price`
- Reconciliation status != `HEALTHY` → block all new entries
- Hard exit does not depend on strategy Manifest, LLM, MetaLabel, Net Edge, or signal freshness
- Stop/TP absolute prices recalculated after real fill using `average_fill_price`
- Local Protection only `ACTIVE` after obtaining exchange protection order ID

## Migration Strategy

**Shadow → Active → Cutover**

1. V2 runs in `SHADOW` mode: reads real data, generates candidates, runs gates, normalizes orders, but does not submit.
2. After Shadow validation, V2 switches to `ACTIVE`: gains exclusive Testnet write permission.
3. Legacy system stops new entries; existing positions managed to closure by legacy exit path.
4. After all legacy positions flat and reconciliation healthy, legacy Testnet write call sites are deleted.
5. Rollback only disables V2 entry; it does not re-enable legacy dual writers.

## Phasing

- **Task 0–3:** Freeze baseline, state machine, database, single writer
- **Task 4–5:** Binance adapter, reconciliation, recovery
- **Task 6–10:** Decision funnel, entry, protection, exit, cycle orchestration
- **Task 11–12:** Testnet sampling, AI review
- **Task 13–14:** Runtime truth API, frontend
- **Task 15:** Shadow validation
- **Task 16:** Real Binance Testnet contract acceptance
- **Task 17:** Natural scheduler E2E (ONLY gate that allows claiming "链路已打通")
- **Task 18:** Cutover and legacy writer deletion

## Related ADRs

- [ADR-001: Automated Trading V2 Single Writer](../adr/ADR-001-automated-trading-v2-single-writer.md)
- [ADR-002: Exchange-First Receipts](../adr/ADR-002-exchange-first-receipts.md)
- [ADR-003: Entry Exit Gate Separation](../adr/ADR-003-entry-exit-gate-separation.md)

## Current Baseline

- **Deployment SHA:** 5e7b926242045e52bb4185b5d220aabc9749d78a
- **Schema revision:** 0015
- **Legacy pipeline:** `services/execution/paper_cycle_orchestrator.py`, `paper_exchange_execution.py`, `paper_order_lifecycle.py`, `paper_signal.py` (frozen, no new features)
- **Ghost position guards:** Preserved from legacy system
- **Config snapshot system:** Already in place (migrations 0010, 0011)
- **Decision engine:** Already in place with `TradeIntent` contract
