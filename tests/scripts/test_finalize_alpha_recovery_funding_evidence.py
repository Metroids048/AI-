"""Unit tests for the read-only Recovery funding ledger boundaries."""

from datetime import UTC, datetime
from decimal import Decimal

from scripts.finalize_alpha_recovery_funding_evidence import attribute_funding


def _episode(position_id: str, opened: str, closed: str) -> dict[str, object]:
    return {
        "position_id": position_id,
        "symbol": "BTC/USDT",
        "direction": "long",
        "quantity": "1",
        "open_time": datetime.fromisoformat(opened).replace(tzinfo=UTC),
        "close_time": datetime.fromisoformat(closed).replace(tzinfo=UTC),
        "trading_realized_pnl_usdt": Decimal("10"),
        "commission_usdt": Decimal("2"),
        "entry_fill_count": 1,
        "exit_fill_count": 1,
    }


def test_attribute_funding_keeps_realized_pnl_net_of_commission() -> None:
    episode = _episode("p1", "2026-08-01T07:00:00", "2026-08-01T09:00:00")
    event = {"time": datetime(2026, 8, 1, 8, tzinfo=UTC), "symbol": "BTC/USDT", "income_usdt": Decimal("-1")}

    ledger = attribute_funding([episode], [event])

    assert ledger[0]["funding_status"] == "FUNDING_EXACT"
    assert ledger[0]["funding_usdt"] == "-1"
    assert ledger[0]["economic_net_pnl_usdt"] == "9"


def test_attribute_funding_marks_no_funding_window_as_exact_zero() -> None:
    episode = _episode("p1", "2026-08-01T08:01:00", "2026-08-01T15:59:00")

    ledger = attribute_funding([episode], [])

    assert ledger[0]["funding_status"] == "FUNDING_ZERO_BY_NO_EVENT"
    assert ledger[0]["funding_usdt"] == "0"


def test_attribute_funding_marks_overlapping_symbol_positions_ambiguous() -> None:
    first = _episode("p1", "2026-08-01T07:00:00", "2026-08-01T09:00:00")
    second = _episode("p2", "2026-08-01T07:30:00", "2026-08-01T08:30:00")
    event = {"time": datetime(2026, 8, 1, 8, tzinfo=UTC), "symbol": "BTC/USDT", "income_usdt": Decimal("-1")}

    ledger = attribute_funding([first, second], [event])

    assert {row["funding_status"] for row in ledger} == {"AMBIGUOUS_FUNDING_ATTRIBUTION"}
