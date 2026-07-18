from __future__ import annotations

from datetime import UTC, datetime, timedelta

from scripts.verify_config import _is_current_sampling_gateway_order
from services.data.universe import AUTO_SIMULATION_EXECUTION_SYMBOLS, execution_scope_hash


def _row(*, created_at: datetime, gateway_order_id: str = "gateway-1") -> dict:
    return {
        "symbol": "BTC/USDT",
        "created_at": created_at.isoformat(),
        "gateway_order_id": gateway_order_id,
        "execution_status": "filled",
    }


def test_current_sampling_gateway_order_requires_scope_build_lane_and_freshness() -> None:
    now = datetime.now(UTC)
    context = {
        "execution_kind": "strategy_trade",
        "strategy_lane": "signal_observation",
        "runtime_build_id": "build-123",
        "execution_scope_hash": execution_scope_hash(AUTO_SIMULATION_EXECUTION_SYMBOLS),
    }

    assert _is_current_sampling_gateway_order(
        _row(created_at=now - timedelta(minutes=5)),
        context,
        now=now,
        build_id="build-123",
    )
    assert not _is_current_sampling_gateway_order(
        _row(created_at=now - timedelta(days=2)),
        context,
        now=now,
        build_id="build-123",
    )
    assert not _is_current_sampling_gateway_order(
        _row(created_at=now - timedelta(minutes=5)),
        {**context, "strategy_lane": "link_verification"},
        now=now,
        build_id="build-123",
    )
    assert not _is_current_sampling_gateway_order(
        _row(created_at=now - timedelta(minutes=5)),
        {**context, "execution_kind": "binance_demo_reconciliation"},
        now=now,
        build_id="build-123",
    )
    assert not _is_current_sampling_gateway_order(
        _row(created_at=now - timedelta(minutes=5)),
        {**context, "runtime_build_id": "old-build"},
        now=now,
        build_id="build-123",
    )
