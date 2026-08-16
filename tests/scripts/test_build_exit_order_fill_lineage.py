from decimal import Decimal

from scripts.build_exit_order_fill_lineage import classify_exit_reason, exit_order_link, waterfall_for_row


def _row() -> dict:
    return {
        "risk_usdt": "10",
        "r0_static_replay": {"gross_pnl_usdt": "-1", "net_pnl_usdt": "-2"},
        "r1_static_actual_entry": {"gross_pnl_usdt": "0", "ambiguous_intrabar": False},
        "r2_dynamic_p1": {"gross_pnl_usdt": "1", "p1_triggered": False, "ambiguous_intrabar": False},
        "r3_actual_exchange": {"gross_pnl_usdt": "-1", "net_pnl_usdt": "-3"},
    }


def test_classify_exit_reason_prefers_abnormal_then_stop_then_target() -> None:
    assert classify_exit_reason([]) == "MISSING_EXIT_FILL"
    assert classify_exit_reason(["TAKE_PROFIT", "HARD_STOP"]) == "STOP"
    assert classify_exit_reason(["ABNORMAL_EXIT", "HARD_STOP"]) == "ABNORMAL_EXIT"


def test_waterfall_stage_deltas_are_normalized_and_non_overlapping() -> None:
    result = waterfall_for_row(_row(), partial_fill_detected=False, protection_event_count=1)
    assert result["entry_execution"] == Decimal("0.2")
    assert result["profit_protection"] == Decimal("0.1")
    assert result["trigger_to_fill_slippage"] == Decimal("-0.2")
    assert result["commission"] == Decimal("-0.2")
    assert result["unknown_residual"] == Decimal("0")


def test_missing_runtime_replacement_is_explicitly_unknown() -> None:
    row = _row()
    row["r2_dynamic_p1"]["p1_triggered"] = True
    result = waterfall_for_row(row, partial_fill_detected=False, protection_event_count=0)
    assert result["profit_protection"] == Decimal("0")
    assert result["unknown_residual"] == Decimal("0.1")


def test_exit_order_link_requires_exact_protection_trigger_identity() -> None:
    fills = [{"exchange_order_id": "exit-1"}, {"exchange_order_id": "exit-1"}]
    events = [{"event_type": "ProtectionTriggered", "event_payload": {"exchange_order_id": "exit-1"}}]
    assert exit_order_link(fills, events)["status"] == "MATCHED"
    assert exit_order_link(fills, [])["status"] == "MISSING_PROTECTION_TRIGGER_EVENT"
    mismatched = [{"event_type": "ProtectionTriggered", "event_payload": {"exchange_order_id": "other"}}]
    assert exit_order_link(fills, mismatched)["status"] == "MISMATCHED"
