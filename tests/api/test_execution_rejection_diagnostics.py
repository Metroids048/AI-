from __future__ import annotations

from apps.api.routers.runs import summarize_order_rejections


def test_rejection_summary_groups_gateway_oos_risk_and_signal_failures() -> None:
    summary = summarize_order_rejections(
        [
            {
                "order_execution_id": "gateway",
                "rejection_codes": ["binance_auto_execute_failed"],
                "rejection_reason": "-2022",
            },
            {"order_execution_id": "oos", "rejection_codes": ["validated_edge_stats_missing_or_stale"]},
            {"order_execution_id": "risk", "rejection_codes": ["max_symbol_exposure_exceeded"]},
            {"order_execution_id": "signal", "rejection_codes": ["net_edge_after_cost_negative"]},
        ]
    )

    assert summary["counts"] == {"网关失败": 1, "OOS 证据": 1, "风控限制": 1, "信号不足": 1, "交易所拒绝": 0}
    assert summary["recent"][0]["category"] == "网关失败"
    assert summary["recent"][0]["message"] == "-2022"
