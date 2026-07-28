"""Allowed transitions for exchange-first ExchangeOrderState."""

from __future__ import annotations

from shared.models.execution_truth import ExchangeOrderState

ALLOWED_EXCHANGE_ORDER_TRANSITIONS: dict[ExchangeOrderState, frozenset[ExchangeOrderState]] = {
    ExchangeOrderState.INTENT_CREATED: frozenset(
        {
            ExchangeOrderState.PRETRADE_APPROVED,
            ExchangeOrderState.PRETRADE_REJECTED,
        }
    ),
    ExchangeOrderState.PRETRADE_APPROVED: frozenset(
        {
            ExchangeOrderState.EXCHANGE_SUBMITTING,
            ExchangeOrderState.PRETRADE_REJECTED,
        }
    ),
    ExchangeOrderState.EXCHANGE_SUBMITTING: frozenset(
        {
            ExchangeOrderState.EXCHANGE_ACKNOWLEDGED,
            ExchangeOrderState.PARTIALLY_FILLED,
            ExchangeOrderState.FILLED,
            ExchangeOrderState.EXCHANGE_REJECTED,
            ExchangeOrderState.EXCHANGE_UNKNOWN,
            ExchangeOrderState.EMERGENCY_CLOSE_PENDING,
        }
    ),
    ExchangeOrderState.EXCHANGE_ACKNOWLEDGED: frozenset(
        {
            ExchangeOrderState.PARTIALLY_FILLED,
            ExchangeOrderState.FILLED,
            ExchangeOrderState.EXCHANGE_REJECTED,
            ExchangeOrderState.EXCHANGE_UNKNOWN,
        }
    ),
    ExchangeOrderState.PARTIALLY_FILLED: frozenset(
        {
            ExchangeOrderState.PARTIALLY_FILLED,
            ExchangeOrderState.FILLED,
            ExchangeOrderState.EXCHANGE_UNKNOWN,
            ExchangeOrderState.EXCHANGE_REJECTED,
        }
    ),
    ExchangeOrderState.FILLED: frozenset(
        {
            ExchangeOrderState.POSITION_PROJECTED,
            ExchangeOrderState.EXCHANGE_UNKNOWN,
            ExchangeOrderState.DUST_REMAINS,
            ExchangeOrderState.CLOSED,
        }
    ),
    ExchangeOrderState.POSITION_PROJECTED: frozenset(
        {
            ExchangeOrderState.PROTECTION_SUBMITTING,
            ExchangeOrderState.PROTECTED,
            ExchangeOrderState.PROTECTION_FAILED,
            ExchangeOrderState.EXCHANGE_UNKNOWN,
            ExchangeOrderState.CLOSED,
        }
    ),
    ExchangeOrderState.PROTECTION_SUBMITTING: frozenset(
        {
            ExchangeOrderState.PROTECTED,
            ExchangeOrderState.PROTECTION_FAILED,
            ExchangeOrderState.EXCHANGE_UNKNOWN,
        }
    ),
    ExchangeOrderState.PROTECTED: frozenset(
        {
            ExchangeOrderState.CLOSED,
            ExchangeOrderState.EMERGENCY_CLOSE_PENDING,
            ExchangeOrderState.EXCHANGE_UNKNOWN,
            ExchangeOrderState.DUST_REMAINS,
        }
    ),
    ExchangeOrderState.PRETRADE_REJECTED: frozenset(),
    ExchangeOrderState.EXCHANGE_REJECTED: frozenset(),
    ExchangeOrderState.PROTECTION_FAILED: frozenset(
        {
            ExchangeOrderState.EMERGENCY_CLOSE_PENDING,
            ExchangeOrderState.EXCHANGE_UNKNOWN,
            ExchangeOrderState.CLOSED,
        }
    ),
    ExchangeOrderState.EXCHANGE_UNKNOWN: frozenset(
        {
            ExchangeOrderState.EXCHANGE_ACKNOWLEDGED,
            ExchangeOrderState.PARTIALLY_FILLED,
            ExchangeOrderState.FILLED,
            ExchangeOrderState.POSITION_PROJECTED,
            ExchangeOrderState.PROTECTED,
            ExchangeOrderState.EXCHANGE_REJECTED,
            ExchangeOrderState.EMERGENCY_CLOSE_PENDING,
            ExchangeOrderState.DUST_REMAINS,
            ExchangeOrderState.CLOSED,
        }
    ),
    ExchangeOrderState.EMERGENCY_CLOSE_PENDING: frozenset(
        {
            ExchangeOrderState.CLOSED,
            ExchangeOrderState.EXCHANGE_UNKNOWN,
            ExchangeOrderState.DUST_REMAINS,
        }
    ),
    ExchangeOrderState.DUST_REMAINS: frozenset(
        {
            ExchangeOrderState.CLOSED,
            ExchangeOrderState.EMERGENCY_CLOSE_PENDING,
            ExchangeOrderState.EXCHANGE_UNKNOWN,
        }
    ),
    ExchangeOrderState.CLOSED: frozenset(),
}


def validate_exchange_order_transition(current: ExchangeOrderState, next_state: ExchangeOrderState) -> None:
    if next_state == current:
        return
    allowed = ALLOWED_EXCHANGE_ORDER_TRANSITIONS.get(current, frozenset())
    if next_state not in allowed:
        raise ValueError(f"illegal exchange order transition: {current} -> {next_state}")
