import pytest

from services.execution.exchange_order_transitions import validate_exchange_order_transition
from shared.models.execution_truth import ExchangeOrderState


def test_legal_intent_created_to_pretrade_approved() -> None:
    validate_exchange_order_transition(
        ExchangeOrderState.INTENT_CREATED,
        ExchangeOrderState.PRETRADE_APPROVED,
    )


def test_illegal_filled_to_intent_created() -> None:
    with pytest.raises(ValueError, match="illegal exchange order transition"):
        validate_exchange_order_transition(
            ExchangeOrderState.FILLED,
            ExchangeOrderState.INTENT_CREATED,
        )


def test_illegal_closed_to_filled() -> None:
    with pytest.raises(ValueError, match="illegal exchange order transition"):
        validate_exchange_order_transition(
            ExchangeOrderState.CLOSED,
            ExchangeOrderState.FILLED,
        )


def test_legal_protected_to_closed() -> None:
    validate_exchange_order_transition(
        ExchangeOrderState.PROTECTED,
        ExchangeOrderState.CLOSED,
    )
