from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from services.strategy_library.context import TIMEFRAME_DELTAS, MarketContextBuilder
from services.strategy_library.regime import RegimeScore, RegimeScorerV2
from shared.models import Exchange, OHLCVBar, Timeframe


def _trend_bars(
    *,
    timeframe: str,
    decision_time: datetime,
    direction: int,
    count: int = 30,
) -> list[OHLCVBar]:
    delta = TIMEFRAME_DELTAS[timeframe]
    bars: list[OHLCVBar] = []
    for index in range(count):
        opened_at = decision_time - delta * (count - index)
        price = Decimal("100") + Decimal(direction * index) * Decimal("0.25")
        bars.append(
            OHLCVBar(
                symbol="BTC/USDT",
                exchange=Exchange.BINANCE,
                timeframe=Timeframe(timeframe),
                time=opened_at,
                open=price,
                high=price + Decimal("0.20"),
                low=price - Decimal("0.20"),
                close=price + Decimal(direction) * Decimal("0.10"),
                volume=Decimal("10"),
            )
        )
    return bars


def _context(*, direction_4h: int = 1, missing_5m: bool = False):
    decision_time = datetime(2026, 7, 30, 4, 0, tzinfo=UTC)
    bars_by_timeframe = {
        timeframe: _trend_bars(
            timeframe=timeframe,
            decision_time=decision_time,
            direction=direction_4h if timeframe == "4h" else 1,
        )
        for timeframe in TIMEFRAME_DELTAS
    }
    if missing_5m:
        bars_by_timeframe["5m"] = []
    return MarketContextBuilder().build(
        symbol="BTC/USDT",
        decision_time=decision_time,
        bars_by_timeframe=bars_by_timeframe,
        source_ids=["synthetic:closed-bars"],
    )


def test_4h_disagreement_reduces_score_but_does_not_hard_veto() -> None:
    scorer = RegimeScorerV2()

    aligned = scorer.score(_context(direction_4h=1))
    disagreed = scorer.score(_context(direction_4h=-1))

    assert disagreed.trend_up < aligned.trend_up
    assert disagreed.trend_up > 0
    assert disagreed.evidence["direction_4h"] < 0
    assert "hard_veto" not in type(disagreed).model_fields


def test_regime_scores_are_frozen_strict_and_bounded() -> None:
    score = RegimeScorerV2().score(_context())

    assert isinstance(score, RegimeScore)
    for field in (
        "trend_up",
        "trend_down",
        "range",
        "compression",
        "expansion",
        "unstable",
    ):
        assert 0 <= getattr(score, field) <= 1
    with pytest.raises(ValidationError):
        score.trend_up = 2
    with pytest.raises(ValidationError):
        type(score)(**{**score.model_dump(), "unexpected": 1})


def test_missing_intraday_confirmation_increases_unstable_without_zeroing_trend() -> None:
    score = RegimeScorerV2().score(_context(missing_5m=True))

    assert score.unstable > 0
    assert score.trend_up > 0
    assert score.evidence["missing_timeframe_fraction"] == pytest.approx(0.2)


def test_flat_structure_scores_as_range() -> None:
    decision_time = datetime(2026, 7, 30, 4, 0, tzinfo=UTC)
    context = MarketContextBuilder().build(
        symbol="BTC/USDT",
        decision_time=decision_time,
        bars_by_timeframe={
            timeframe: _trend_bars(
                timeframe=timeframe,
                decision_time=decision_time,
                direction=0,
            )
            for timeframe in TIMEFRAME_DELTAS
        },
        source_ids=["synthetic:closed-bars"],
    )

    score = RegimeScorerV2().score(context)

    assert score.range == pytest.approx(1.0)
    assert score.trend_up == 0
    assert score.trend_down == 0


def test_volatility_shock_raises_expansion_and_unstable() -> None:
    context = _context()
    bars_by_timeframe = {
        timeframe: [
            OHLCVBar.model_validate(bar.model_dump(by_alias=True)) for bar in getattr(context, f"bars_{timeframe}").bars
        ]
        for timeframe in TIMEFRAME_DELTAS
    }
    last = bars_by_timeframe["15m"][-1]
    bars_by_timeframe["15m"][-1] = last.model_copy(
        update={
            "high": last.close + Decimal("10"),
            "low": last.close - Decimal("10"),
        }
    )
    shocked = MarketContextBuilder().build(
        symbol="BTC/USDT",
        decision_time=context.decision_time,
        bars_by_timeframe=bars_by_timeframe,
        source_ids=["synthetic:closed-bars"],
    )

    score = RegimeScorerV2().score(shocked)

    assert score.expansion > 0.5
    assert score.unstable > 0
