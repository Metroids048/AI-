from datetime import UTC, datetime
from decimal import Decimal

from scripts.build_trade_lifecycle_forensics import (
    classify_taxonomy,
    excursion_metrics,
    recovery_windows,
    stop_floor_evidence,
)
from services.research.exit_policy_shadow.contracts import Bar


def _bar(offset: int, *, high: str, low: str) -> Bar:
    return Bar(
        time=datetime(2026, 8, 1, 0, offset, tzinfo=UTC),
        open=Decimal("100"),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal("100"),
        volume=Decimal("1"),
    )


def test_stop_floor_reports_the_winning_term() -> None:
    result = stop_floor_evidence(entry_price=Decimal("100"), atr14=Decimal("0.1"), runtime_stop=Decimal("99.65"))
    assert result["source"] == "PCT_FLOOR_0.35%"
    assert result["status"] == "CONFIRMED"


def test_excursions_are_direction_aware() -> None:
    result = excursion_metrics(
        entry_price=Decimal("100"),
        side="long",
        quantity=Decimal("2"),
        risk_per_unit=Decimal("1"),
        bars=[_bar(0, high="101.5", low="98.5")],
    )
    assert result["mfe_r"] == "1.5"
    assert result["mae_r"] == "-1.5"


def test_recovery_excludes_exit_bar_and_marks_complete_horizon() -> None:
    exit_time = datetime(2026, 8, 1, 0, 0, 30, tzinfo=UTC)
    bars = [
        _bar(0, high="103", low="99"),
        _bar(1, high="101.5", low="99.5"),
    ]
    result = recovery_windows(
        entry_price=Decimal("100"),
        side="long",
        risk_per_unit=Decimal("1"),
        exit_time=exit_time,
        bars=bars,
    )
    assert result["15m"]["status"] == "TRUNCATED"
    assert result["15m"]["recovered_1r"] is True


def test_stop_floor_recovery_is_stop_geometry_failure() -> None:
    recovery = {
        "4h": {
            "status": "COMPLETE",
            "recovered_entry": True,
            "max_adverse_r": "-0.2",
            "max_adverse_price": "-0.2",
        }
    }
    result = classify_taxonomy(exit_reason="HARD_STOP", floor_source="PCT_FLOOR_0.35%", recovery=recovery)
    assert result["primary"] == "STOP_GEOMETRY_FAILURE"
    assert result["labels"] == ["STOPPED_THEN_RECOVERED"]


def test_stop_continuing_against_entry_is_direction_failure() -> None:
    recovery = {
        "4h": {
            "status": "COMPLETE",
            "recovered_entry": False,
            "max_adverse_r": "-1.2",
            "max_adverse_price": "-1.2",
        }
    }
    result = classify_taxonomy(exit_reason="STOP", floor_source="ATR14_TERM", recovery=recovery)
    assert result["primary"] == "DIRECTION_FAILURE"
