"""Client Order ID contract (plan section 6.2).

Required properties under test: length, character set, stability for the same
intent, no collisions across different intents, and no collisions across the
entry/exit/stop/target legs.
"""

from __future__ import annotations

import pytest

from services.automated_trading.domain.client_order_id import (
    CLIENT_ORDER_ID_PATTERN,
    MAX_CLIENT_ORDER_ID_LENGTH,
    OrderLegKind,
    entry_client_order_id,
    exit_client_order_id,
    is_v2_client_order_id,
    leg_kind_of,
    stop_client_order_id,
    target_client_order_id,
)

INTENT = "3f8c1d2e-7a45-4b91-9c33-0e7b21ac9f10"
POSITION = "b91e4477-2c6a-49df-8f10-5ad3e2c7b884"


def _all_generators() -> list[str]:
    return [
        entry_client_order_id(INTENT),
        exit_client_order_id(POSITION),
        stop_client_order_id(POSITION),
        target_client_order_id(POSITION),
    ]


@pytest.mark.parametrize("client_order_id", _all_generators())
def test_length_within_exchange_limit(client_order_id: str) -> None:
    assert len(client_order_id) <= MAX_CLIENT_ORDER_ID_LENGTH


@pytest.mark.parametrize("client_order_id", _all_generators())
def test_character_set_is_exchange_safe(client_order_id: str) -> None:
    assert CLIENT_ORDER_ID_PATTERN.match(client_order_id)


def test_same_intent_is_stable_across_calls() -> None:
    """A retry must reuse the ID, or an EXCHANGE_UNKNOWN timeout duplicates the position."""
    assert entry_client_order_id(INTENT) == entry_client_order_id(INTENT)
    assert exit_client_order_id(POSITION) == exit_client_order_id(POSITION)
    assert stop_client_order_id(POSITION) == stop_client_order_id(POSITION)
    assert target_client_order_id(POSITION) == target_client_order_id(POSITION)


def test_different_intents_do_not_collide() -> None:
    other = "00000000-0000-4000-8000-000000000001"
    assert entry_client_order_id(INTENT) != entry_client_order_id(other)
    assert exit_client_order_id(POSITION) != exit_client_order_id(other)


def test_legs_do_not_collide_for_the_same_identifier() -> None:
    """Entry/exit/stop/target on one identifier must all be distinct."""
    ids = {
        entry_client_order_id(POSITION),
        exit_client_order_id(POSITION),
        stop_client_order_id(POSITION),
        target_client_order_id(POSITION),
    }
    assert len(ids) == 4


def test_hash_separator_prevents_boundary_collisions() -> None:
    """("ab", 1) and ("a", 11) must not hash to the same ID."""
    assert entry_client_order_id("ab", leg=1) != entry_client_order_id("a", leg=11)


def test_distinct_attempts_and_revisions_differ() -> None:
    assert exit_client_order_id(POSITION, attempt=1) != exit_client_order_id(POSITION, attempt=2)
    assert stop_client_order_id(POSITION, revision=1) != stop_client_order_id(POSITION, revision=2)
    assert target_client_order_id(POSITION, revision=1) != target_client_order_id(POSITION, revision=2)


def test_leg_kind_is_reverse_parsable() -> None:
    assert leg_kind_of(entry_client_order_id(INTENT)) is OrderLegKind.ENTRY
    assert leg_kind_of(exit_client_order_id(POSITION)) is OrderLegKind.EXIT
    assert leg_kind_of(stop_client_order_id(POSITION)) is OrderLegKind.STOP
    assert leg_kind_of(target_client_order_id(POSITION)) is OrderLegKind.TARGET


def test_foreign_client_order_ids_are_not_claimed_as_v2() -> None:
    """Reconciliation relies on this to tell V2 orders from legacy or foreign ones."""
    assert leg_kind_of("aqrp-1234567890abcdef") is None
    assert is_v2_client_order_id("aqrp-1234567890abcdef") is False
    assert is_v2_client_order_id("web_1234567") is False
    assert is_v2_client_order_id(entry_client_order_id(INTENT)) is True


def test_id_carries_no_raw_identifier_or_secret() -> None:
    """Only a hash may appear; the raw intent id must not leak into the ID."""
    client_order_id = entry_client_order_id(INTENT)
    assert INTENT not in client_order_id
    assert INTENT.split("-")[0] not in client_order_id


@pytest.mark.parametrize(
    "generator",
    [entry_client_order_id, exit_client_order_id, stop_client_order_id, target_client_order_id],
)
def test_empty_identifier_is_rejected(generator) -> None:
    with pytest.raises(ValueError, match="required"):
        generator("")


def test_non_positive_counters_are_rejected() -> None:
    with pytest.raises(ValueError, match="leg must be"):
        entry_client_order_id(INTENT, leg=0)
    with pytest.raises(ValueError, match="attempt must be"):
        exit_client_order_id(POSITION, attempt=0)
    with pytest.raises(ValueError, match="revision must be"):
        stop_client_order_id(POSITION, revision=0)
    with pytest.raises(ValueError, match="revision must be"):
        target_client_order_id(POSITION, revision=0)
