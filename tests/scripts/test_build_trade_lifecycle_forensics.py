import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from scripts.build_trade_lifecycle_forensics import (
    classify_taxonomy,
    excursion_metrics,
    load_authoritative_closed_episodes,
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


def test_authoritative_cohort_uses_v2_closed_auto_positions_not_static_parity_audit(tmp_path: Path) -> None:
    """Forensics must include every closed automatic V2 position and exclude manual recovery exits."""
    database = tmp_path / "runtime.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE v2_execution_intents (
                intent_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                candidate_key TEXT NOT NULL,
                candidate_type TEXT NOT NULL,
                decision_bar_timestamp TEXT NOT NULL
            );
            CREATE TABLE v2_managed_positions (
                position_id TEXT PRIMARY KEY,
                intent_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                quantity NUMERIC NOT NULL,
                entry_price NUMERIC NOT NULL,
                entry_fee NUMERIC NOT NULL,
                state TEXT NOT NULL,
                closed_at TEXT,
                realized_pnl NUMERIC
            );
            CREATE TABLE v2_exchange_fills (
                fill_id TEXT PRIMARY KEY,
                intent_id TEXT NOT NULL,
                exchange_order_id TEXT NOT NULL,
                reduce_only BOOLEAN NOT NULL,
                filled_quantity NUMERIC NOT NULL,
                fill_price NUMERIC NOT NULL,
                commission NUMERIC,
                exchange_event_time TEXT NOT NULL
            );
            CREATE TABLE v2_protection_records (
                protection_id TEXT PRIMARY KEY,
                position_id TEXT NOT NULL,
                stop_loss_price NUMERIC NOT NULL,
                take_profit_price NUMERIC,
                stop_exchange_order_id TEXT,
                tp_exchange_order_id TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE v2_execution_events (
                event_id TEXT PRIMARY KEY,
                aggregate_id TEXT NOT NULL,
                aggregate_type TEXT NOT NULL,
                event_type TEXT NOT NULL,
                event_payload JSON NOT NULL,
                occurred_at TEXT NOT NULL
            );
            """
        )
        connection.executemany(
            "INSERT INTO v2_execution_intents VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    "entry-eth",
                    "ETH/USDT",
                    "long",
                    "testnet_sampling_v2",
                    "SAMPLING",
                    "2026-08-18 10:00:00",
                ),
                (
                    "entry-sol",
                    "SOL/USDT",
                    "short",
                    "research_candidate",
                    "RESEARCH",
                    "2026-08-18 10:00:00",
                ),
                (
                    "manual-close",
                    "ETH/USDT",
                    "long",
                    "exit:legacy:MANUAL_REDUCE_ONLY",
                    "SAMPLING",
                    "2026-08-18 10:00:00",
                ),
                (
                    "exit-eth",
                    "ETH/USDT",
                    "long",
                    "exit:position-eth",
                    "SAMPLING",
                    "2026-08-18 11:00:00",
                ),
                (
                    "exit-sol",
                    "SOL/USDT",
                    "short",
                    "exit:position-sol",
                    "RESEARCH",
                    "2026-08-18 11:00:00",
                ),
            ],
        )
        connection.executemany(
            "INSERT INTO v2_managed_positions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    "position-eth",
                    "entry-eth",
                    "ETH/USDT",
                    "long",
                    5.354,
                    1903.21,
                    2.5,
                    "CLOSED",
                    "2026-08-18 11:00:00",
                    -12.0,
                ),
                (
                    "position-sol",
                    "entry-sol",
                    "SOL/USDT",
                    "short",
                    85.09,
                    76.30,
                    2.6,
                    "CLOSED",
                    "2026-08-18 11:00:00",
                    8.0,
                ),
                (
                    "manual-position",
                    "manual-close",
                    "ETH/USDT",
                    "long",
                    1,
                    1900,
                    0,
                    "CLOSED",
                    "2026-08-18 11:00:00",
                    0,
                ),
            ],
        )
        connection.executemany(
            "INSERT INTO v2_exchange_fills VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("eth-entry-fill", "entry-eth", "eth-entry", 0, 5.354, 1903.21, 2.5, "2026-08-18 10:01:00"),
                ("eth-exit-fill", "exit-eth", "eth-stop", 1, 5.354, 1899.00, 2.4, "2026-08-18 11:00:00"),
                ("sol-entry-fill", "entry-sol", "sol-entry", 0, 85.09, 76.30, 2.6, "2026-08-18 10:01:00"),
                ("sol-exit-fill", "exit-sol", "sol-tp", 1, 85.09, 75.00, 2.5, "2026-08-18 11:00:00"),
            ],
        )
        connection.executemany(
            "INSERT INTO v2_execution_events VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    "eth-closed",
                    "position-eth",
                    "POSITION",
                    "PositionClosed",
                    '{"exit_intent_id":"exit-eth","exchange_order_id":"eth-stop","reason":"HARD_STOP"}',
                    "2026-08-18 11:00:00",
                ),
                (
                    "sol-closed",
                    "position-sol",
                    "POSITION",
                    "PositionClosed",
                    '{"exit_intent_id":"exit-sol","exchange_order_id":"sol-tp","reason":"TAKE_PROFIT"}',
                    "2026-08-18 11:00:00",
                ),
            ],
        )
        connection.executemany(
            "INSERT INTO v2_protection_records VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("eth-protection", "position-eth", 1899.00, 1909.00, "eth-stop", "eth-tp", "2026-08-18 10:02:00"),
                ("sol-protection", "position-sol", 77.00, 75.00, "sol-stop", "sol-tp", "2026-08-18 10:02:00"),
            ],
        )

    episodes = load_authoritative_closed_episodes(database)

    assert [episode["position_id"] for episode in episodes] == ["position-eth", "position-sol"]
    assert episodes[0]["exit_reason"] == "HARD_STOP"
    assert episodes[1]["exit_reason"] == "TAKE_PROFIT"
    assert episodes[0]["entry_fee_usdt"] == "2.5"
    assert episodes[0]["exit_fee_usdt"] == "2.4"
