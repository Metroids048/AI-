from __future__ import annotations

from datetime import UTC, datetime, timedelta

from apps.api.routers.runtime import build_no_trade_summary


def _facts(**overrides):
    now = datetime.now(UTC)
    facts = {
        "observed_at": now,
        "window_hours": 3,
        "scheduler": {
            "running": True,
            "heartbeat_at": now,
            "last_auto_cycle_at": now,
        },
        "exchange": {"status": "available", "value": {"open_positions": []}},
        "data": {"fresh": True, "exchange_info_ready": True},
        "reconciliation": {"status": "healthy", "blocked_symbols": []},
        "entry_runtime": {
            "trading_state": "TRADING",
            "entry_authority": "TESTNET_CANARY",
            "entry_authorized": True,
            "reason": None,
        },
        "decisions": [],
        "entry_fills": [],
    }
    facts.update(overrides)
    return facts


def test_scheduler_offline_has_priority_over_waiting_for_signal():
    result = build_no_trade_summary(**_facts(scheduler={"running": False}))

    assert result["summary_code"] == "SCHEDULER_OFFLINE"
    assert result["runtime_status"] == "异常"


def test_exchange_unavailable_has_priority_over_data_and_strategy():
    facts = _facts(exchange={"status": "unavailable", "value": None})
    result = build_no_trade_summary(**facts)

    assert result["summary_code"] == "EXCHANGE_UNAVAILABLE"


def test_stale_market_data_is_not_reported_as_waiting_for_signal():
    result = build_no_trade_summary(**_facts(data={"fresh": False, "exchange_info_ready": True}))

    assert result["summary_code"] == "MARKET_DATA_STALE"


def test_reconciliation_block_has_priority_over_entry_pause():
    facts = _facts(
        reconciliation={"status": "blocked", "blocked_symbols": ["BTC/USDT"]},
        entry_runtime={"trading_state": "ENTRY_PAUSED", "entry_authority": "NONE", "entry_authorized": False},
    )
    result = build_no_trade_summary(**facts)

    assert result["summary_code"] == "RECONCILIATION_BLOCKED"
    assert result["reconciliation"]["blocked_symbols"] == ["BTC/USDT"]


def test_entry_paused_is_reported_when_system_facts_are_healthy():
    facts = _facts(
        entry_runtime={
            "trading_state": "ENTRY_PAUSED",
            "entry_authority": "NONE",
            "entry_authorized": False,
            "reason": "ENTRY_KILL_SWITCH_ACTIVE",
        }
    )
    result = build_no_trade_summary(**facts)

    assert result["summary_code"] == "ENTRY_PAUSED"
    assert result["entry_runtime"]["reason"] == "ENTRY_KILL_SWITCH_ACTIVE"


def test_canary_trading_with_production_pending_is_not_entry_paused():
    facts = _facts(
        entry_runtime={
            "trading_state": "TRADING",
            "entry_authority": "TESTNET_CANARY",
            "entry_authorized": True,
            "production_authorization_state": "PENDING",
        },
        decisions=[{"at": datetime.now(UTC), "reason": "NO_ENTRY_SIGNAL"}],
    )
    result = build_no_trade_summary(**facts)

    assert result["summary_code"] == "HEALTHY_WAITING_FOR_SIGNAL"


def test_duplicate_decisions_are_excluded_from_effective_rejections():
    now = datetime.now(UTC)
    facts = _facts(
        decisions=[
            {"at": now, "reason": "DUPLICATE_DECISION"},
            {"at": now - timedelta(minutes=1), "reason": "DUPLICATE_DECISION"},
            {"at": now - timedelta(minutes=2), "reason": "NO_ENTRY_SIGNAL"},
        ]
    )
    result = build_no_trade_summary(**facts)

    assert result["decisions"]["total"] == 3
    assert result["decisions"]["effective"] == 1
    assert result["decisions"]["duplicate"] == 2
    assert result["decisions"]["reason_counts"] == {"NO_ENTRY_SIGNAL": 1}


def test_no_entry_fill_uses_real_fill_not_decision_timestamp():
    now = datetime.now(UTC)
    fill_at = now - timedelta(hours=1, minutes=20)
    result = build_no_trade_summary(
        **_facts(
            decisions=[{"at": now - timedelta(minutes=2), "reason": "NO_ENTRY_SIGNAL"}],
            entry_fills=[{"at": fill_at, "reduce_only": False}],
        )
    )

    assert result["last_entry_at"] == fill_at.isoformat()
    assert result["hours_since_last_entry"] > 1.3


def test_sqlite_naive_decision_timestamp_is_normalized_to_utc():
    now = datetime.now(UTC)
    naive_recent = (now - timedelta(minutes=5)).replace(tzinfo=None)
    result = build_no_trade_summary(**_facts(decisions=[{"at": naive_recent, "reason": "NO_TRADE_COST_INEFFICIENT"}]))

    assert result["summary_code"] == "ENTRY_BLOCKED"
    assert result["decisions"]["dominant_reason"] == "NO_TRADE_COST_INEFFICIENT"


def test_reduce_only_fill_is_not_counted_as_entry():
    now = datetime.now(UTC)
    result = build_no_trade_summary(
        **_facts(
            entry_fills=[{"at": now - timedelta(minutes=10), "reduce_only": True}],
            decisions=[{"at": now - timedelta(minutes=5), "reason": "NO_ENTRY_SIGNAL"}],
        )
    )

    assert result["last_entry_at"] is None
    assert result["summary_code"] == "HEALTHY_WAITING_FOR_SIGNAL"


def test_stalled_decision_pipeline_beats_healthy_waiting():
    now = datetime.now(UTC)
    result = build_no_trade_summary(**_facts(decisions=[{"at": now - timedelta(hours=2), "reason": "NO_ENTRY_SIGNAL"}]))

    assert result["summary_code"] == "DECISION_PIPELINE_STALLED"


def test_recent_effective_decisions_with_no_entry_fill_are_healthy_waiting():
    now = datetime.now(UTC)
    result = build_no_trade_summary(
        **_facts(decisions=[{"at": now - timedelta(minutes=5), "reason": "NO_ENTRY_SIGNAL"}])
    )

    assert result["summary_code"] == "HEALTHY_WAITING_FOR_SIGNAL"


def test_entry_fill_outside_window_is_not_reported_as_recent_entry():
    now = datetime.now(UTC)
    result = build_no_trade_summary(**_facts(entry_fills=[{"at": now - timedelta(hours=4), "reduce_only": False}]))

    assert result["last_entry_at"] is None
    assert result["hours_since_last_entry"] is None


def test_entry_blocked_is_operational_block_not_runtime_failure():
    now = datetime.now(UTC)
    result = build_no_trade_summary(
        **_facts(decisions=[{"at": now - timedelta(minutes=5), "reason": "PRICE_DRIFT_EXCEEDED"}])
    )

    assert result["summary_code"] == "ENTRY_BLOCKED"
    assert result["runtime_status"] == "正常"


def test_manual_direction_conflict_is_reported_as_entry_block():
    now = datetime.now(UTC)
    result = build_no_trade_summary(
        **_facts(decisions=[{"at": now - timedelta(minutes=5), "reason": "MANUAL_POSITION_DIRECTION_CONFLICT"}])
    )

    assert result["summary_code"] == "ENTRY_BLOCKED"
    assert result["decisions"]["dominant_reason"] == "MANUAL_POSITION_DIRECTION_CONFLICT"
