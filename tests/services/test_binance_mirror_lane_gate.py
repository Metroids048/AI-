"""Directional vs carry Binance mirror admission."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from services.execution.paper_runtime import PaperRuntimeService
from shared.config import settings
from shared.models import OrderExecution, PaperRun, TradeSide


def _service(gateway: object | None = object()) -> PaperRuntimeService:
    return PaperRuntimeService(
        data_repo=MagicMock(),
        execution_repo=MagicMock(),
        paper_repo=MagicMock(),
        strategy_repo=MagicMock(),
        gatekeeper=MagicMock(),
        gateway=gateway,
    )


def _armed_run() -> PaperRun:
    return PaperRun(
        paper_run_id="paper-armed",
        strategy_id="strategy-1",
        gate_decision_ref="backtest-1",
        execution_profile={
            "execution_mode": "binance_simulation_first",
            "mirror_to_gateway": True,
            "cost_gate_verified": True,
            "strategy_lane": "directional",
        },
        paper_status="running",
    )


def _order(*, lane: str, pipeline_status: str = "bet_taken", edge: float | None = None) -> OrderExecution:
    trace: dict = {"pipeline_status": pipeline_status, "strategy_lane": lane}
    if lane == "carry":
        trace["estimated_net_edge_bps"] = edge
        trace["min_estimated_net_edge_bps"] = 10.0
    return OrderExecution(
        strategy_id="strategy-1",
        symbol="BTC/USDT",
        direction=TradeSide.LONG,
        execution_status="accepted",
        stoploss_present=True,
        close_only_mode=False,
        entry_context={"decision_pipeline": trace},
        stoploss_plan={"price": 95.0},
        takeprofit_plan={"price": 110.0},
        validation_backtest_run_id="backtest-1",
        paper_run_id="paper-armed",
    )


@pytest.fixture
def arm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "binance_auto_execute", True)
    monkeypatch.setattr(settings, "binance_use_testnet", True)
    monkeypatch.setattr(settings, "live_trading_enabled", False)


def test_directional_order_allowed_when_binance_simulation_armed(arm_env: None) -> None:
    service = _service()
    assert (
        service.cycle_orchestrator._should_execute_on_binance(
            _armed_run(),
            order=_order(lane="directional"),
        )
        is True
    )


def test_carry_order_still_requires_net_edge(arm_env: None) -> None:
    service = _service()
    assert (
        service.cycle_orchestrator._should_execute_on_binance(
            _armed_run(),
            order=_order(lane="carry", edge=5.0),
        )
        is False
    )
    assert (
        service.cycle_orchestrator._should_execute_on_binance(
            _armed_run(),
            order=_order(lane="carry", edge=12.0),
        )
        is True
    )


def test_directional_rejected_without_pipeline_status(arm_env: None) -> None:
    service = _service()
    order = _order(lane="directional", pipeline_status="")
    order.entry_context["decision_pipeline"]["pipeline_status"] = ""
    assert service.cycle_orchestrator._should_execute_on_binance(_armed_run(), order=order) is False
