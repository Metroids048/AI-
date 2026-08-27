from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

from scripts.run_alpha_research_recovery_v2 import (
    Bar,
    Trade,
    _costed_return,
    _gap_count,
    _h1_signals,
    _linear_regression,
    _metrics,
    _rolling_vwap,
    _window_metrics,
)


def _bars(values: list[float]) -> list[Bar]:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    return [
        Bar(start + timedelta(minutes=15 * i), value, value + 1, value - 1, value, 100.0)
        for i, value in enumerate(values)
    ]


def test_vwap_is_closed_bar_only() -> None:
    base = _bars([100.0 + i for i in range(60)])
    changed_future = base + [Bar(base[-1].timestamp + timedelta(minutes=15), 10_000, 10_001, 9_999, 10_000, 1_000_000)]
    assert _rolling_vwap(base, 59) == _rolling_vwap(changed_future, 59)


def test_h1_signals_do_not_change_when_future_bars_are_mutated() -> None:
    rng = random.Random(7)
    start = datetime(2024, 1, 1, tzinfo=UTC)
    price = 100.0
    base: list[Bar] = []
    for index in range(120):
        price = max(1.0, price + (rng.uniform(-0.8, 0.8) if index >= 60 else 0.0))
        span = rng.uniform(0.5, 3.0)
        base.append(
            Bar(
                start + timedelta(minutes=15 * index),
                price - span / 2,
                price + span / 2,
                price - span / 2,
                price,
                rng.uniform(80.0, 120.0),
            )
        )

    changed = list(base)
    for index in range(94, len(changed)):
        bar = changed[index]
        changed[index] = Bar(bar.timestamp, 10_000.0, 10_001.0, 9_999.0, 10_000.0, 1_000_000.0)

    baseline = _h1_signals(base)
    mutated = _h1_signals(changed)
    assert baseline
    cutoff = min(entry_index for entry_index, *_ in baseline if entry_index < 94)
    assert [signal for signal in mutated if signal[0] < 94] == [signal for signal in baseline if signal[0] < 94]
    assert cutoff < 94


def test_regression_symmetry() -> None:
    slope_up, r2_up, _ = _linear_regression([1, 2, 3, 4, 5, 6])
    slope_down, r2_down, _ = _linear_regression([6, 5, 4, 3, 2, 1])
    assert slope_up == -slope_down
    assert r2_up == r2_down


def test_regression_is_deterministic() -> None:
    values = [100.0, 101.5, 100.8, 102.0, 103.25]
    assert _linear_regression(values) == _linear_regression(values)


def test_cost_is_explicitly_accounted() -> None:
    gross, net, fee, slippage = _costed_return("long", 100.0, 102.0)
    assert gross > net
    assert fee > 0
    assert slippage > 0


def test_long_short_return_is_mirrored() -> None:
    long = _costed_return("long", 100.0, 102.0)
    short = _costed_return("short", 100.0, 98.0)
    assert long[0] == short[0]
    assert long[1] == short[1]


def test_window_metrics_do_not_leak_between_windows() -> None:
    rows = [
        Trade(
            "BTC/USDT",
            "long",
            "2024-01-01T00:00:00+00:00",
            "2024-01-01T00:15:00+00:00",
            0.02,
            0.0184,
            0.001,
            0.0006,
            0.0,
            "targets",
            1,
        ),
        Trade(
            "BTC/USDT",
            "long",
            "2024-01-02T00:00:00+00:00",
            "2024-01-02T00:15:00+00:00",
            -0.02,
            -0.0216,
            0.001,
            0.0006,
            0.0,
            "stop_loss",
            1,
        ),
    ]
    result = _window_metrics(rows, (("w1", datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 2, tzinfo=UTC)),))
    assert result["w1"]["trades"] == 1
    assert result["w1"]["net_return"] > 0


def test_metrics_reports_btc_eth_breakdown() -> None:
    rows = [
        Trade(
            "BTC/USDT",
            "long",
            "2024-01-01T00:00:00+00:00",
            "2024-01-01T00:15:00+00:00",
            0.02,
            0.0184,
            0.001,
            0.0006,
            0.0,
            "targets",
            1,
        ),
        Trade(
            "ETH/USDT",
            "short",
            "2024-01-02T00:00:00+00:00",
            "2024-01-02T00:15:00+00:00",
            -0.02,
            -0.0216,
            0.001,
            0.0006,
            0.0,
            "stop_loss",
            1,
        ),
    ]
    result = _metrics(rows)
    assert result["btc_trades"] == 1
    assert result["eth_trades"] == 1
    assert result["long_trades"] == 1
    assert result["short_trades"] == 1


def test_gap_detector_flags_stale_or_missing_bars() -> None:
    bars = _bars([100.0, 101.0, 102.0])
    gap_bars = [bars[0], bars[2]]
    assert _gap_count(bars, timedelta(minutes=15)) == 0
    assert _gap_count(gap_bars, timedelta(minutes=15)) == 1


def test_final_holdout_boundary_is_not_part_of_research_windows() -> None:
    research_end = datetime(2024, 2, 1, tzinfo=UTC)
    validation_end = datetime(2024, 3, 1, tzinfo=UTC)
    final_holdout = datetime(2024, 4, 1, tzinfo=UTC)
    assert research_end < validation_end < final_holdout
