"""Point-in-time market context shared by runtime and replay."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from pydantic import ConfigDict, Field

from shared.models import Exchange, MarketExtras, OHLCVBar, PlatformModel, Timeframe

TIMEFRAME_DELTAS: dict[str, timedelta] = {
    "1m": timedelta(minutes=1),
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
}


class FrozenContract(PlatformModel):
    """Immutable, strict cross-layer strategy contract."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class ClosedBar(FrozenContract):
    symbol: str
    exchange: Exchange
    timeframe: Timeframe
    timestamp: datetime = Field(alias="time")
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


class BarWindow(FrozenContract):
    timeframe: str
    bars: tuple[ClosedBar, ...]
    last_closed_at: datetime | None
    expected_interval_seconds: int = Field(gt=0)
    gap_count: int = Field(ge=0)


class StructureFeatures(FrozenContract):
    recent_high: Decimal | None = None
    recent_low: Decimal | None = None
    higher_highs: int = Field(default=0, ge=0)
    higher_lows: int = Field(default=0, ge=0)
    lower_highs: int = Field(default=0, ge=0)
    lower_lows: int = Field(default=0, ge=0)


class MomentumFeatures(FrozenContract):
    return_15m: Decimal | None = None
    return_1h: Decimal | None = None
    return_4h: Decimal | None = None


class VolumeFeatures(FrozenContract):
    latest_volume: Decimal | None = None
    mean_volume_20: Decimal | None = None
    volume_ratio: Decimal | None = None


class VolatilityFeatures(FrozenContract):
    true_range: Decimal | None = None
    atr_14: Decimal | None = None
    range_atr_ratio: Decimal | None = None


class DerivativesFeatures(FrozenContract):
    funding_rate: Decimal | None = None
    open_interest: Decimal | None = None
    long_ratio: Decimal | None = None
    short_ratio: Decimal | None = None
    liquidation_usd: Decimal | None = None
    observed_at: datetime | None = None


class SessionFeatures(FrozenContract):
    utc_hour: int = Field(ge=0, le=23)
    utc_weekday: int = Field(ge=0, le=6)


class DataFreshness(FrozenContract):
    age_seconds: dict[str, float | None]
    stale_timeframes: tuple[str, ...]
    has_gaps: bool


class MarketContext(FrozenContract):
    symbol: str
    decision_time: datetime
    bars_1m: BarWindow
    bars_5m: BarWindow
    bars_15m: BarWindow
    bars_1h: BarWindow
    bars_4h: BarWindow
    structure: StructureFeatures
    momentum: MomentumFeatures
    volume: VolumeFeatures
    volatility: VolatilityFeatures
    derivatives: DerivativesFeatures
    session: SessionFeatures
    freshness: DataFreshness
    source_ids: tuple[str, ...]
    missing_features: list[str]


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _closed_window(
    *,
    symbol: str,
    timeframe: str,
    bars: Sequence[OHLCVBar],
    decision_time: datetime,
) -> BarWindow:
    delta = TIMEFRAME_DELTAS[timeframe]
    eligible = sorted(
        (
            bar
            for bar in bars
            if bar.symbol == symbol
            and bar.timeframe.value == timeframe
            and _utc(bar.timestamp) + delta <= decision_time
        ),
        key=lambda bar: bar.timestamp,
    )
    closed = tuple(
        ClosedBar(
            symbol=bar.symbol,
            exchange=bar.exchange,
            timeframe=bar.timeframe,
            time=_utc(bar.timestamp),
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=bar.volume,
        )
        for bar in eligible
    )
    gap_count = sum(
        1
        for previous, current in zip(closed, closed[1:], strict=False)
        if _utc(current.timestamp) - _utc(previous.timestamp) != delta
    )
    last_closed_at = _utc(closed[-1].timestamp) + delta if closed else None
    return BarWindow(
        timeframe=timeframe,
        bars=closed,
        last_closed_at=last_closed_at,
        expected_interval_seconds=int(delta.total_seconds()),
        gap_count=gap_count,
    )


def _return(window: BarWindow) -> Decimal | None:
    if len(window.bars) < 2 or window.bars[-2].close <= 0:
        return None
    return (window.bars[-1].close / window.bars[-2].close) - Decimal("1")


def _structure(window: BarWindow) -> StructureFeatures:
    bars = window.bars[-48:]
    if not bars:
        return StructureFeatures()
    higher_highs = sum(current.high > previous.high for previous, current in zip(bars, bars[1:], strict=False))
    higher_lows = sum(current.low > previous.low for previous, current in zip(bars, bars[1:], strict=False))
    lower_highs = sum(current.high < previous.high for previous, current in zip(bars, bars[1:], strict=False))
    lower_lows = sum(current.low < previous.low for previous, current in zip(bars, bars[1:], strict=False))
    return StructureFeatures(
        recent_high=max(bar.high for bar in bars),
        recent_low=min(bar.low for bar in bars),
        higher_highs=higher_highs,
        higher_lows=higher_lows,
        lower_highs=lower_highs,
        lower_lows=lower_lows,
    )


def _volume(window: BarWindow) -> VolumeFeatures:
    bars = window.bars
    if not bars:
        return VolumeFeatures()
    sample = bars[-20:]
    mean_volume = sum((bar.volume for bar in sample), Decimal("0")) / Decimal(len(sample))
    return VolumeFeatures(
        latest_volume=bars[-1].volume,
        mean_volume_20=mean_volume,
        volume_ratio=(bars[-1].volume / mean_volume if mean_volume > 0 else None),
    )


def _volatility(window: BarWindow) -> VolatilityFeatures:
    bars = window.bars
    if not bars:
        return VolatilityFeatures()
    true_ranges: list[Decimal] = []
    for index, bar in enumerate(bars[-15:]):
        previous_close = bars[-15:][index - 1].close if index > 0 else bar.open
        true_ranges.append(max(bar.high - bar.low, abs(bar.high - previous_close), abs(bar.low - previous_close)))
    atr = sum(true_ranges[-14:], Decimal("0")) / Decimal(min(14, len(true_ranges)))
    recent = bars[-20:]
    price_range = max(bar.high for bar in recent) - min(bar.low for bar in recent)
    return VolatilityFeatures(
        true_range=true_ranges[-1],
        atr_14=atr,
        range_atr_ratio=(price_range / atr if atr > 0 else None),
    )


class MarketContextBuilder:
    """Build a deterministic context using information available at decision_time."""

    def build(
        self,
        *,
        symbol: str,
        decision_time: datetime,
        bars_by_timeframe: Mapping[str, Sequence[OHLCVBar]],
        market_extras: Sequence[MarketExtras] | None = None,
        source_ids: Sequence[str] = (),
    ) -> MarketContext:
        decision_time = _utc(decision_time)
        windows = {
            timeframe: _closed_window(
                symbol=symbol,
                timeframe=timeframe,
                bars=bars_by_timeframe.get(timeframe, ()),
                decision_time=decision_time,
            )
            for timeframe in TIMEFRAME_DELTAS
        }
        eligible_extras = sorted(
            (item for item in (market_extras or ()) if item.symbol == symbol and _utc(item.timestamp) <= decision_time),
            key=lambda item: item.timestamp,
        )
        latest_extra = eligible_extras[-1] if eligible_extras else None
        derivatives = DerivativesFeatures(
            funding_rate=latest_extra.funding_rate if latest_extra else None,
            open_interest=latest_extra.open_interest if latest_extra else None,
            long_ratio=latest_extra.long_ratio if latest_extra else None,
            short_ratio=latest_extra.short_ratio if latest_extra else None,
            liquidation_usd=latest_extra.liquidation_usd if latest_extra else None,
            observed_at=_utc(latest_extra.timestamp) if latest_extra else None,
        )
        missing_features = [f"bars_{timeframe}:missing" for timeframe, window in windows.items() if not window.bars]
        if derivatives.funding_rate is None:
            missing_features.append("funding_rate:missing")
        if derivatives.open_interest is None:
            missing_features.append("open_interest:missing")
        age_seconds = {
            timeframe: (
                max(0.0, (decision_time - window.last_closed_at).total_seconds())
                if window.last_closed_at is not None
                else None
            )
            for timeframe, window in windows.items()
        }
        stale_timeframes = tuple(
            timeframe
            for timeframe, age in age_seconds.items()
            if age is None or age > TIMEFRAME_DELTAS[timeframe].total_seconds()
        )
        entry_window = windows["15m"]
        return MarketContext(
            symbol=symbol,
            decision_time=decision_time,
            bars_1m=windows["1m"],
            bars_5m=windows["5m"],
            bars_15m=entry_window,
            bars_1h=windows["1h"],
            bars_4h=windows["4h"],
            structure=_structure(entry_window),
            momentum=MomentumFeatures(
                return_15m=_return(entry_window),
                return_1h=_return(windows["1h"]),
                return_4h=_return(windows["4h"]),
            ),
            volume=_volume(entry_window),
            volatility=_volatility(entry_window),
            derivatives=derivatives,
            session=SessionFeatures(
                utc_hour=decision_time.hour,
                utc_weekday=decision_time.weekday(),
            ),
            freshness=DataFreshness(
                age_seconds=age_seconds,
                stale_timeframes=stale_timeframes,
                has_gaps=any(window.gap_count > 0 for window in windows.values()),
            ),
            source_ids=tuple(dict.fromkeys(source_ids)),
            missing_features=missing_features,
        )
