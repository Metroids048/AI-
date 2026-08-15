from dataclasses import replace
from datetime import UTC, datetime, timedelta

from services.strategy_library.event_edge import (
    EdgeEvent,
    EventBar,
    QualityGate,
    _outcome,
    discover_quality_gate,
    gate_metrics,
)


def _event(event_id: str, *, r: float, cost: float = 0.0, volume: float = 1.2) -> EdgeEvent:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return EdgeEvent(
        event_id=event_id,
        event_type="HTF_STRUCTURE_BREAK",
        symbol="BTC/USDT",
        side="long",
        event_time=now,
        entry_time=now,
        entry=100.0,
        stop=99.0,
        target=101.5,
        atr=1.0,
        volume_ratio=volume,
        breakout_distance_atr=0.25,
        atr_percentile=0.5,
        trend_age=3,
        chop_score=0.3,
        retest_depth_atr=0.0,
        regime_4h="long",
        trend_strength_1h=1.0,
        outcome="TP_FIRST" if r > 0 else "STOP_FIRST",
        outcome_time=now,
        outcome_r=r,
        cost_r=cost,
        mfe_r=r if r > 0 else 0.0,
        mae_r=r if r < 0 else 0.0,
    )


def test_quality_gate_filters_event_features() -> None:
    gate = QualityGate("HTF_STRUCTURE_BREAK", "long", 1.2, 0.2, 2, 0.5, 0.2, 0.8)
    assert gate.accepts(_event("accepted", r=1.5))
    assert not gate.accepts(_event("rejected", r=1.5, volume=1.1))


def test_gate_metrics_reports_net_payoff_and_drawdown() -> None:
    metrics = gate_metrics((_event("a", r=1.5), _event("b", r=-1.0), _event("c", r=1.5, cost=0.1)))
    assert metrics.trades == 3
    assert metrics.wins == 2
    assert metrics.losses == 1
    assert metrics.payoff > 1
    assert metrics.expectancy > 0
    assert metrics.max_drawdown_r >= 1


def test_outcome_includes_entry_bar() -> None:
    bars = (
        EventBar(datetime(2026, 1, 1, tzinfo=UTC), 100, 100.2, 98.8, 99.5, 1),
        EventBar(datetime(2026, 1, 1, 0, 15, tzinfo=UTC), 99.5, 100, 99, 99.8, 1),
    )
    result = _outcome(side="long", entry_index=0, entry=100, stop=99, target=101.5, bars15=bars, max_holding_bars=2)
    assert result is not None and result[0] == "STOP_FIRST"


def test_metrics_sort_by_entry_time_and_negative_train_gate_abstains() -> None:
    first = _event("first", r=1.5)
    second = replace(_event("second", r=-1.0), entry_time=first.entry_time + timedelta(minutes=15))
    metrics = gate_metrics((second, first))
    assert metrics.max_drawdown_r == 1.0
    gate, _ = discover_quality_gate(tuple(_event(str(index), r=-1.0) for index in range(40)))
    assert gate is None
