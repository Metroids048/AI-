"""Exchange-first execution invariants and quantity helpers."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

from shared.config import settings
from shared.models.execution_truth import (
    Commission,
    DecisionFunnelStage,
    DecisionFunnelStatus,
    DecisionFunnelTerminal,
    ExchangeFillReceipt,
    ExchangeOrderRecord,
    ExchangeOrderState,
    ExecutionMode,
    PretradeMarketSnapshot,
    ReconciliationResult,
    ReconciliationStatus,
    RuntimeDatum,
    SimulatedFill,
)

_LEGACY_EXECUTION_MODES = {
    "paper_only": ExecutionMode.LOCAL_PAPER,
    "binance_simulation_first": ExecutionMode.BINANCE_TESTNET,
}


def resolve_execution_mode(value: str | ExecutionMode, *, migration: bool = False) -> ExecutionMode:
    if isinstance(value, ExecutionMode):
        return value
    try:
        return ExecutionMode(value)
    except ValueError:
        if value in _LEGACY_EXECUTION_MODES:
            if migration:
                return _LEGACY_EXECUTION_MODES[value]
            raise ValueError(f"legacy execution mode is migration-only: {value}") from None
        raise ValueError(f"unsupported execution mode: {value}") from None


def binance_client_order_id(*, live_run_id: str, idempotency_key: str) -> str:
    digest = hashlib.sha256(f"{live_run_id}:{idempotency_key}".encode()).hexdigest()
    return f"aq-{digest[:33]}"


@dataclass(frozen=True)
class CloseQuantityResult:
    quantity: Decimal
    authoritative_quantity: Decimal
    dust_remains: bool


def close_quantity(
    *,
    requested_quantity: Decimal | None,
    authoritative_quantity: Decimal,
    step_size: Decimal,
    reference_price: Decimal,
    min_notional: Decimal,
) -> CloseQuantityResult:
    """Cap at exchange truth and round down; never inflate a reduce-risk exit."""
    if authoritative_quantity < 0 or step_size <= 0:
        raise ValueError("authoritative quantity must be non-negative and step size positive")
    requested = authoritative_quantity if requested_quantity is None else max(requested_quantity, Decimal("0"))
    capped = min(requested, authoritative_quantity)
    steps = (capped / step_size).to_integral_value(rounding=ROUND_DOWN)
    quantity = steps * step_size
    remaining = max(authoritative_quantity - quantity, Decimal("0"))
    below_min_notional = quantity > 0 and quantity * reference_price < min_notional
    return CloseQuantityResult(
        quantity=quantity,
        authoritative_quantity=authoritative_quantity,
        dust_remains=remaining > 0 or below_min_notional,
    )


def validate_pretrade_snapshot(
    snapshot: PretradeMarketSnapshot,
    *,
    decision_reference: Decimal,
) -> Decimal:
    if snapshot.decision_age_seconds > settings.pretrade_max_decision_age_seconds:
        raise ValueError(
            "PRETRADE_DECISION_STALE: "
            f"age={snapshot.decision_age_seconds:.3f}s "
            f"limit={settings.pretrade_max_decision_age_seconds}s"
        )
    drift = abs(snapshot.mark_price - decision_reference) / decision_reference
    threshold = max(
        Decimal(str(settings.pretrade_min_price_drift_bps)) / Decimal("10000"),
        Decimal(str(settings.pretrade_atr_drift_fraction)) * snapshot.atr / decision_reference,
    )
    if drift > threshold:
        raise ValueError(f"PRETRADE_PRICE_DRIFT: drift={drift} limit={threshold}")
    return drift


__all__ = [
    "CloseQuantityResult",
    "Commission",
    "DecisionFunnelStage",
    "DecisionFunnelStatus",
    "DecisionFunnelTerminal",
    "ExchangeFillReceipt",
    "ExchangeOrderRecord",
    "ExchangeOrderState",
    "ExecutionMode",
    "PretradeMarketSnapshot",
    "ReconciliationResult",
    "ReconciliationStatus",
    "RuntimeDatum",
    "SimulatedFill",
    "close_quantity",
    "binance_client_order_id",
    "resolve_execution_mode",
    "validate_pretrade_snapshot",
]
