"""Behavioral tests for the isolated QuantDinger Shadow worker."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from services.strategy_library.adapters.quantdinger import QuantDingerContractError
from services.strategy_library.adapters.quantdinger_shadow_runtime import (
    QuantDingerShadowRuntimeError,
    run_quantdinger_shadow_source,
)

SOURCE = """
def initialize(context):
    context.set_universe(["BTC/USDT"])
    context.subscribe(frequency="15m")
    context.set_warmup(2)
    context.set_default_protection(stop_loss_pct=0.01, take_profit_pct=0.02)

def handle_data(context, data):
    closes = get_history(2, "15m", "close", "BTC/USDT")
    if len(closes) < 2:
        return
    if float(closes.iloc[-1]) > float(closes.iloc[-2]):
        order_target_percent(
            "BTC/USDT",
            1.0,
            confidence=0.8,
            max_entry_drift_bps=25,
        )
"""


def _bars() -> list[dict[str, object]]:
    start = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
    closes = (65000, 65100, 64900, 65200)
    return [
        {
            "timestamp": (start + timedelta(minutes=15 * index)).isoformat(),
            "open": close - 10,
            "high": close + 20,
            "low": close - 20,
            "close": close,
            "volume": 1000,
        }
        for index, close in enumerate(closes)
    ]


def test_isolated_worker_runs_strategy_and_returns_shadow_candidates() -> None:
    result = run_quantdinger_shadow_source(
        SOURCE,
        bars_by_symbol={"BTC/USDT": _bars()},
        strategy_id="qd-ema-test",
    )

    assert result.manifest.instruments == ("BTC/USDT",)
    assert result.manifest.timeframe == "15m"
    assert len(result.signals) == 1
    candidates = result.candidates(cycle_id="shadow-cycle-1")
    assert len(candidates) == 1
    assert {str(candidate.lane) for candidate in candidates} == {"SHADOW"}
    assert all(candidate.non_promotable for candidate in candidates)
    replay_trades = result.replay_trades(bars_by_symbol={"BTC/USDT": _bars()})
    assert len(replay_trades) == 1
    assert all(trade.entry_time > trade.signal_candle_close_time for trade in replay_trades)
    assert replay_trades[0].exit_reason in {"stoploss", "takeprofit", "end_of_window"}
    assert result.rejected_events == ({"reason": "duplicate_target_while_position_open", "symbol": "BTC/USDT"},)


def test_worker_rejects_imports_before_subprocess_execution() -> None:
    with pytest.raises(QuantDingerContractError, match="imports"):
        run_quantdinger_shadow_source(
            "import os\n" + SOURCE,
            bars_by_symbol={"BTC/USDT": _bars()},
        )


def test_worker_timeout_is_fail_closed() -> None:
    source = SOURCE.replace(
        '    closes = get_history(2, "15m", "close", "BTC/USDT")',
        '    while True:\n        pass\n    closes = get_history(2, "15m", "close", "BTC/USDT")',
    )

    with pytest.raises(QuantDingerShadowRuntimeError, match="timed out"):
        run_quantdinger_shadow_source(
            source,
            bars_by_symbol={"BTC/USDT": _bars()},
            timeout_seconds=1,
        )
