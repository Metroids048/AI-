# ADR-003: Entry Exit Gate Separation

- **Date:** 2026-07-28
- **Status:** Accepted
- **Supersedes:** Legacy unified gate system that blocked both entry and exit

## Context

The legacy automatic trading system used a shared gate evaluation for both opening new positions and closing existing ones. This led to dangerous failure modes:

1. **Stop-Loss Denial:** When Kill Switch activated, AI veto triggered, or strategy Manifest became invalid, the system would refuse to submit stop-loss orders, leaving positions unprotected.
2. **Data Freshness Deadlock:** Stale market data or expired candlestick would block exit logic, even though the position was already open and at risk.
3. **News Event Lockup:** High-risk news events (FOMC, CPI, liquidation cascade) would freeze all trading activity, preventing emergency position closure.
4. **Reconciliation Paralysis:** When exchange reconciliation failed or returned `UNAVAILABLE`, the system would block all order submission, including reduce-only exits that could have salvaged the account.

The root cause: **entry conditions and exit conditions were conflated**. A gate designed to prevent speculative new entries (e.g., "Net Edge after cost must be positive") was incorrectly applied to hard exits like stop-loss or emergency position reduction.

## Decision

V2 separates **Entry Gates** from **Exit Gates** at the architectural level.

### Entry Gates

**Purpose:** Decide whether to open a new position or increase exposure.

**Blocking Conditions:**
- Kill Switch `ACTIVE`
- Strategy Manifest status not `PROMOTABLE` or `RETAINED`
- AI Market Review returns `VETO_OVERRIDE`
- MetaLabel bet rejected
- Net Edge after cost <= 0
- Signal data older than 5 minutes
- Price drift > `max_entry_drift_bps`
- Reconciliation status != `HEALTHY`
- Hard drawdown lock active
- Daily loss limit reached
- Open position count >= max concurrent positions
- Risk per trade exceeds portfolio fraction cap

**Entry Gate Evaluation Point:**
```python
class EntryService:
    def evaluate_entry_gates(
        self,
        candidate: TradeCandidate,
        context: DecisionContext,
        gates: list[EntryGate]
    ) -> EntryGateResult:
        for gate in gates:
            result = gate.evaluate(candidate, context)
            if result.blocked:
                return EntryGateResult(
                    allowed=False,
                    terminal_reason=result.reason,
                    terminal_stage=DecisionStage.ENTRY_GATE_REJECTED,
                    blocking_gate=gate.name,
                )
        return EntryGateResult(allowed=True)
```

### Exit Gates (Reduce-Only)

**Purpose:** Decide whether to close or reduce an existing position.

**Allowed Even When:**
- Kill Switch `ACTIVE`
- Strategy Manifest `DELETED` or `ABANDONED`
- AI Market Review returns `VETO_OVERRIDE`
- MetaLabel skipped
- Net Edge unknown or negative
- Signal data stale or absent
- Price drift exceeds entry tolerance
- News risk event active

**Blocking Conditions (much narrower):**
- Reconciliation status `RECOVERY_REQUIRED` (position ownership ambiguous)
- Exchange API completely unavailable (cannot submit order)
- Position status already `CLOSING` or `CLOSED`
- Order submission would increase exposure (not reduce-only)

**Exit Gate Evaluation Point:**
```python
class ExitService:
    def evaluate_exit_gates(
        self,
        position: ManagedPosition,
        exit_reason: ExitReason,
        context: DecisionContext,
    ) -> ExitGateResult:
        # Hard exits bypass most gates
        if exit_reason in [
            ExitReason.STOP_LOSS_HIT,
            ExitReason.EMERGENCY_CLOSE,
            ExitReason.RECONCILIATION_MISMATCH,
            ExitReason.OPERATOR_MANUAL,
        ]:
            if context.exchange_available and position.ownership_status == OwnershipStatus.MANAGED:
                return ExitGateResult(allowed=True, bypass_reason=exit_reason.value)

        # Soft exits may be deferred
        if not context.exchange_available:
            return ExitGateResult(allowed=False, reason="exchange_unavailable")
        if context.reconciliation_status == ReconciliationStatus.RECOVERY_REQUIRED:
            return ExitGateResult(allowed=False, reason="reconciliation_recovery_required")

        return ExitGateResult(allowed=True)
```

### Decision Funnel Separation

The decision funnel records entry and exit evaluations separately:

```python
class DecisionFunnel:
    # Entry path
    entry_stages: list[DecisionStage]  # DATA_FRESH, REGIME_EVALUATED, ..., ENTRY_GATE_APPROVED
    entry_terminal_stage: DecisionStage | None
    entry_terminal_reason: str | None

    # Exit path
    exit_evaluations: list[ExitEvaluation]  # Per-position exit check
    exit_terminal_reason: str | None
```

When a cycle produces no new entry, `entry_terminal_stage` explains why (e.g., `ENTRY_GATE_REJECTED: kill_switch_active`).

When an exit is attempted but blocked, `exit_terminal_reason` explains why (must be narrow: `exchange_unavailable`, `recovery_required`, or `already_closing`).

### Reconciliation Integration

```python
class ReconciliationStatus(StrEnum):
    HEALTHY = "HEALTHY"  # Local == Exchange, all protections active
    DEGRADED = "DEGRADED"  # Minor discrepancy, investigate but do not block
    UNAVAILABLE = "UNAVAILABLE"  # Exchange query failed, defer new entries
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"  # Ownership ambiguous, block all orders
```

**Entry Impact:**
- `HEALTHY` → proceed
- `DEGRADED` → log warning, proceed with caution
- `UNAVAILABLE` or `RECOVERY_REQUIRED` → block all new entries

**Exit Impact:**
- `HEALTHY`, `DEGRADED`, `UNAVAILABLE` → allow reduce-only exits
- `RECOVERY_REQUIRED` → block until ownership resolved (cannot confirm order is truly reduce-only)

## Consequences

### Positive

- **No More Stop-Loss Denial:** Hard exits work even when Kill Switch is on, AI vetos entry, or strategy Manifest is invalid.
- **Emergency Resilience:** Exchange reconciliation failure or stale data does not trap capital in open positions.
- **Clear Semantics:** "This gate blocks entry" vs "This gate blocks exit" is explicit in code and logs.
- **Operator Override:** Manual exits never blocked by model-based gates (MetaLabel, Net Edge, AI).

### Negative

- **Increased Complexity:** Two gate systems instead of one.
- **Risk of Misclassification:** Developer might accidentally implement an exit-blocking check in Entry Gates.

### Mitigation

- Contract tests enforce that entry-blocking conditions do not affect reduce-only exit paths.
- Each gate annotated with `gate_type: Literal["ENTRY", "EXIT"]` in domain model.
- Decision funnel logs explicitly show which gates ran for entry vs exit.

## Verification

```bash
pytest tests/services/test_automated_trading_entry_gates.py -v
pytest tests/services/test_automated_trading_exit_gates.py -v
pytest tests/contracts/test_exit_gate_separation.py -v
```

Evidence must show:
- Kill Switch `ACTIVE` + open position with stop-loss hit → exit proceeds
- Reconciliation `RECOVERY_REQUIRED` + stop-loss hit → exit blocked (ownership ambiguous)
- Stale signal data → entry blocked, exit proceeds
- AI veto → entry blocked, exit proceeds
- Net Edge negative → entry blocked, exit proceeds
