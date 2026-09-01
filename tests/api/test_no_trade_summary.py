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
            "trading_state": "TRADING_READY",
            "entry_authority": "TESTNET_FORWARD",
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
    assert result["summary_category"] == "SYSTEM_BLOCKED"
    assert result["reasons"]["system_failure_reason"] == "SCHEDULER_OFFLINE"
    assert result["runtime_status"] == "异常"


def test_degraded_runtime_exposes_stable_scheduler_failure_reason():
    result = build_no_trade_summary(
        **_facts(
            scheduler={"running": True, "scheduler_error": "ACTIVE_CONFIG_SNAPSHOT_CAPTURE_FAILED"},
            entry_runtime={
                "trading_state": "DEGRADED",
                "entry_authority": "TESTNET_FORWARD",
                "entry_authorized": False,
                "system_failure_reason": "ACTIVE_CONFIG_SNAPSHOT_CAPTURE_FAILED",
            },
        )
    )

    assert result["summary_category"] == "SYSTEM_BLOCKED"
    assert result["reasons"]["system_failure_reason"] == "ACTIVE_CONFIG_SNAPSHOT_CAPTURE_FAILED"


def test_exchange_unavailable_has_priority_over_data_and_strategy():
    facts = _facts(exchange={"status": "unavailable", "value": None})
    result = build_no_trade_summary(**facts)

    assert result["summary_code"] == "EXCHANGE_UNAVAILABLE"


def test_exchange_reconciliation_probe_is_not_labelled_as_an_outage():
    result = build_no_trade_summary(
        **_facts(
            exchange={
                "status": "unavailable",
                "value": None,
                "error": "exchange truth probe in progress",
            }
        )
    )

    assert result["summary_code"] == "EXCHANGE_RECONCILIATION_IN_PROGRESS"
    assert result["summary_category"] == "SYSTEM_BLOCKED"
    assert result["runtime_status"] == "同步中"
    assert result["current_status"]["runtime_health"] == "checking"


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


def test_stale_degraded_reconciliation_without_blockers_does_not_hide_entry_pause():
    facts = _facts(
        reconciliation={"status": "degraded", "blocked_symbols": [], "entry_blocked": False},
        entry_runtime={
            "trading_state": "ENTRY_PAUSED",
            "entry_authority": "NONE",
            "entry_authorized": False,
            "reason": "NO_AUTHORIZED_PRODUCTION_STRATEGY",
        },
    )

    result = build_no_trade_summary(**facts)

    assert result["summary_code"] == "ENTRY_PAUSED"
    assert result["entry_runtime"]["reason"] == "NO_AUTHORIZED_PRODUCTION_STRATEGY"


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
    assert result["summary_category"] == "AUTHORIZATION_BLOCKED"
    assert result["reasons"]["authorization_reason"] == "ENTRY_KILL_SWITCH_ACTIVE"
    assert result["entry_runtime"]["reason"] == "ENTRY_KILL_SWITCH_ACTIVE"


def test_management_only_reports_runtime_control_reason_not_authority_success():
    result = build_no_trade_summary(
        **_facts(
            entry_runtime={
                "trading_state": "MANAGEMENT_ONLY",
                "entry_authority": "TESTNET_FORWARD",
                "entry_authorized": False,
                "reason": "TESTNET_FORWARD_AUTHORIZED",
                "entry_control_reason": "ENTRY_PAUSED:operator",
            }
        )
    )

    assert result["summary_category"] == "AUTHORIZATION_BLOCKED"
    assert result["current_status"]["active_blocker"] == "ENTRY_PAUSED:operator"
    assert result["reasons"]["authorization_reason"] == "ENTRY_PAUSED:operator"
    assert result["entry_runtime"]["reason"] == "ENTRY_PAUSED:operator"


def test_candidate_timestamp_is_not_reported_as_entry_attempt_without_intent():
    now = datetime.now(UTC)
    result = build_no_trade_summary(
        **_facts(
            decisions=[
                {
                    "at": now,
                    "symbol": "BTC/USDT",
                    "reason": "PRICE_DRIFT_EXCEEDED",
                    "candidate_created": True,
                }
            ]
        )
    )

    assert result["timeline"]["last_candidate_at"] == now.isoformat()
    assert result["timeline"]["last_entry_attempt_at"] is None
    assert result["symbols"]["BTC/USDT"]["last_entry_attempt_at"] is None


def test_forward_authorization_failure_is_not_counted_as_system_failure():
    now = datetime.now(UTC)
    facts = _facts(
        entry_runtime={
            "trading_state": "ENTRY_BLOCKED",
            "entry_authority": "NONE",
            "entry_authorized": False,
            "entry_authority_reason": "NO_FORWARD_VALIDATION_CANDIDATE",
        },
        decisions=[
            {
                "at": now,
                "reason": "NO_FORWARD_VALIDATION_CANDIDATE",
                "strategy_terminal_reason": None,
                "system_failure_reason": None,
                "execution_blocker": "NO_FORWARD_VALIDATION_CANDIDATE",
            }
        ],
    )

    result = build_no_trade_summary(**facts)

    assert result["summary_code"] == "NO_FORWARD_VALIDATION_CANDIDATE"
    assert result["summary_category"] == "AUTHORIZATION_BLOCKED"
    assert result["historical_window"]["operational_block_counts"] == {
        "NO_FORWARD_VALIDATION_CANDIDATE": 1
    }
    assert result["historical_window"]["system_failure_counts"] == {}


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
    assert result["summary_category"] == "STRATEGY_NO_SIGNAL"


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
    assert result["summary_category"] == "RISK_REJECTED"
    assert result["reasons"]["execution_blocker"] == "PRICE_DRIFT_EXCEEDED"
    assert result["runtime_status"] == "正常"


def test_throughput_reports_actual_canary_capacity_and_blocker_share_inputs():
    now = datetime.now(UTC)
    result = build_no_trade_summary(
        **_facts(
            open_positions_count=1,
            decisions=[
                {"at": now, "reason": "MAX_OPEN_EXPOSURES", "effective_max_open_positions": 1},
                {
                    "at": now - timedelta(minutes=1),
                    "reason": "MACD_DIRECTION_MISMATCH",
                    "effective_max_open_positions": 1,
                },
                {"at": now - timedelta(minutes=2), "reason": "NO_ENTRY_SIGNAL"},
            ],
        )
    )

    assert result["throughput"]["current_open_positions"] == 1
    assert result["throughput"]["effective_max_open_positions"] == 1
    assert result["throughput"]["remaining_slots"] == 0
    assert result["throughput"]["at_capacity"] is True
    assert result["throughput"]["operational_block_counts"] == {"MAX_OPEN_EXPOSURES": 1}
    assert result["throughput"]["strategy_filter_counts"] == {
        "MACD_DIRECTION_MISMATCH": 1,
        "NO_ENTRY_SIGNAL": 1,
    }
    assert result["current_status"]["active_blocker"] == "MAX_OPEN_EXPOSURES"


def test_strategy_filters_are_not_operational_blockers():
    now = datetime.now(UTC)
    result = build_no_trade_summary(
        **_facts(
            decisions=[
                {"at": now, "reason": "MULTI_TIMEFRAME_DISAGREEMENT"},
                {"at": now - timedelta(minutes=1), "reason": "MACD_DIRECTION_MISMATCH"},
            ]
        )
    )

    assert result["summary_code"] == "HEALTHY_WAITING_FOR_SIGNAL"
    assert result["current_status"]["active_blocker"] is None
    assert result["historical_window"]["strategy_filter_counts"] == {
        "MULTI_TIMEFRAME_DISAGREEMENT": 1,
        "MACD_DIRECTION_MISMATCH": 1,
    }
    assert result["historical_window"]["operational_block_counts"] == {}


def test_canary_capacity_uses_current_runtime_contract_not_historical_sizing():
    now = datetime.now(UTC)
    result = build_no_trade_summary(
        **_facts(
            execution_mode="BINANCE_TESTNET",
            entry_runtime={
                "trading_state": "TRADING",
                "entry_authority": "TESTNET_CANARY",
                "entry_authorized": True,
                "reason": None,
            },
            open_positions_count=1,
            decisions=[{"at": now, "reason": "NO_ENTRY_SIGNAL", "effective_max_open_positions": 1}],
        )
    )

    assert result["throughput"]["effective_max_open_positions"] == 2
    assert result["throughput"]["capacity_source"] == "TESTNET_CANARY_RUNTIME_CONTRACT"
    assert result["current_status"]["active_blocker"] is None


def test_capacity_two_of_two_is_current_entry_block():
    now = datetime.now(UTC)
    result = build_no_trade_summary(
        **_facts(
            execution_mode="BINANCE_TESTNET",
            entry_runtime={
                "trading_state": "TRADING",
                "entry_authority": "TESTNET_CANARY",
                "entry_authorized": True,
                "reason": None,
            },
            open_positions_count=2,
            decisions=[{"at": now, "reason": "MULTI_TIMEFRAME_DISAGREEMENT"}],
        )
    )

    assert result["summary_code"] == "ENTRY_BLOCKED"
    assert result["current_status"]["active_blocker"] == "MAX_OPEN_EXPOSURES"
    assert result["runtime_status"] == "正常"


def test_manual_direction_conflict_is_reported_as_entry_block():
    now = datetime.now(UTC)
    result = build_no_trade_summary(
        **_facts(decisions=[{"at": now - timedelta(minutes=5), "reason": "MANUAL_POSITION_DIRECTION_CONFLICT"}])
    )

    assert result["summary_code"] == "ENTRY_BLOCKED"
    assert result["decisions"]["dominant_reason"] == "MANUAL_POSITION_DIRECTION_CONFLICT"


def test_missing_reason_becomes_explicit_invariant_failure_not_unknown():
    result = build_no_trade_summary(**_facts(decisions=[{"at": datetime.now(UTC)}]))

    assert result["historical_window"]["system_failure_counts"] == {"DECISION_REASON_MISSING": 1}
    assert "UNKNOWN" not in result["decisions"]["reason_counts"]


def test_recent_system_failures_remain_visible_when_current_blocker_is_none():
    now = datetime.now(UTC)
    result = build_no_trade_summary(
        **_facts(
            decisions=[
                {
                    "at": now,
                    "reason": "NO_ENTRY_SIGNAL",
                    "strategy_terminal_reason": "NO_ENTRY_SIGNAL",
                    "system_failure_reason": "ENTRY_DATA_NOT_READY",
                    "execution_blocker": None,
                }
            ]
        )
    )

    assert result["current_status"]["active_blocker"] is None
    assert result["historical_window"]["strategy_filter_counts"] == {"NO_ENTRY_SIGNAL": 1}
    assert result["historical_window"]["recent_system_failures"] == {"ENTRY_DATA_NOT_READY": 1}


def test_management_cycle_is_not_counted_as_strategy_opportunity():
    now = datetime.now(UTC)
    result = build_no_trade_summary(
        **_facts(
            decisions=[
                {"at": now, "reason": "NO_ENTRY_SIGNAL", "cycle_kind": "MANAGEMENT"},
                {"at": now - timedelta(minutes=1), "reason": "NO_ENTRY_SIGNAL", "cycle_kind": "ENTRY_OPPORTUNITY"},
            ]
        )
    )

    assert result["decisions"]["management"] == 1
    assert result["decisions"]["effective"] == 1


def test_no_trade_summary_reports_per_symbol_candidate_gate_and_timeline():
    now = datetime.now(UTC)
    result = build_no_trade_summary(
        **_facts(
            observed_at=now,
            decisions=[
                {
                    "at": now - timedelta(minutes=2),
                    "symbol": "BTC/USDT",
                    "reason": "PRICE_DRIFT_EXCEEDED",
                    "candidate_created": True,
                    "entry_gate_result": "ENTRY_GATE_REJECTED",
                    "entry_submitted": False,
                },
                {
                    "at": now - timedelta(minutes=3),
                    "symbol": "ETH/USDT",
                    "reason": "NO_ENTRY_SIGNAL",
                    "candidate_created": False,
                    "entry_gate_result": "ENTRY_SIGNAL_EVALUATED",
                    "entry_submitted": False,
                },
            ],
        )
    )

    assert result["timeline"]["last_candidate_at"] == (now - timedelta(minutes=2)).isoformat()
    assert result["timeline"]["last_entry_attempt_at"] is None
    assert result["symbols"]["BTC/USDT"]["candidate_created"] is True
    assert result["symbols"]["BTC/USDT"]["gatekeeper_result"] == "ENTRY_GATE_REJECTED"
    assert result["symbols"]["ETH/USDT"]["candidate_created"] is False
