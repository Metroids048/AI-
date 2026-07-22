"""Entry models for different strategy families.

Ensemble只决定方向，EntryModel决定具体买卖点。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pandas as pd

from services.strategy_library.family import StrategyFamily
from shared.models import OrderType, TradeSide


@dataclass(frozen=True)
class EntrySetup:
    """入场设置."""

    entry_type: OrderType
    reference_price: Decimal
    trigger_price: Decimal | None  # 触发价（STOP订单）
    max_chase_price: Decimal | None  # 最大追价
    setup_reason: str
    expected_holding_bars: int  # 预期持仓K线数


class EntryModel:
    """入场模型基类."""

    def generate_entry(
        self,
        *,
        frame: pd.DataFrame,
        side: TradeSide,
        family: StrategyFamily,
        symbol: str,
    ) -> EntrySetup | None:
        """生成入场设置.

        Args:
            frame: OHLCV数据（已收盘K线）
            side: 方向
            family: 策略族
            symbol: 交易对

        Returns:
            入场设置，如果条件不满足返回None
        """
        raise NotImplementedError


class TrendPullbackEntry(EntryModel):
    """趋势回调入场模型."""

    def __init__(
        self,
        *,
        ema_period: int = 50,
        atr_period: int = 14,
        pullback_atr_min: float = 0.5,
        pullback_atr_max: float = 2.0,
    ):
        self.ema_period = ema_period
        self.atr_period = atr_period
        self.pullback_atr_min = pullback_atr_min
        self.pullback_atr_max = pullback_atr_max

    def generate_entry(
        self,
        *,
        frame: pd.DataFrame,
        side: TradeSide,
        family: StrategyFamily,
        symbol: str,
    ) -> EntrySetup | None:
        if len(frame) < max(self.ema_period, self.atr_period) + 2:
            return None

        close = frame["close"]
        ema = close.ewm(span=self.ema_period, adjust=False).mean()
        atr = self._calculate_atr(frame)

        latest_close = Decimal(str(close.iloc[-1]))
        latest_ema = float(ema.iloc[-1])
        latest_atr = float(atr.iloc[-1])

        # 检查回调幅度
        if side == TradeSide.LONG:
            distance_from_ema = latest_ema - float(latest_close)
        else:
            distance_from_ema = float(latest_close) - latest_ema

        if distance_from_ema < 0:
            return None  # 价格没有回调

        distance_in_atr = distance_from_ema / latest_atr
        if not (self.pullback_atr_min <= distance_in_atr <= self.pullback_atr_max):
            return None  # 回调幅度不在范围内

        # 入场价：突破回到EMA
        if side == TradeSide.LONG:
            trigger_price = Decimal(str(latest_ema))
            max_chase = trigger_price + Decimal(str(latest_atr * 0.5))
        else:
            trigger_price = Decimal(str(latest_ema))
            max_chase = trigger_price - Decimal(str(latest_atr * 0.5))

        return EntrySetup(
            entry_type=OrderType.CONDITIONAL,
            reference_price=latest_close,
            trigger_price=trigger_price,
            max_chase_price=max_chase,
            setup_reason="pullback_reclaim_ema",
            expected_holding_bars=12,
        )

    def _calculate_atr(self, frame: pd.DataFrame) -> pd.Series:
        high = frame["high"]
        low = frame["low"]
        close = frame["close"]
        tr1 = high - low
        tr2 = (high - close.shift()).abs()
        tr3 = (low - close.shift()).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        return tr.ewm(span=self.atr_period, adjust=False).mean()


class BreakoutEntry(EntryModel):
    """突破入场模型."""

    def __init__(
        self,
        *,
        lookback_period: int = 20,
        atr_period: int = 14,
        max_breakout_atr: float = 1.0,
    ):
        self.lookback_period = lookback_period
        self.atr_period = atr_period
        self.max_breakout_atr = max_breakout_atr

    def generate_entry(
        self,
        *,
        frame: pd.DataFrame,
        side: TradeSide,
        family: StrategyFamily,
        symbol: str,
    ) -> EntrySetup | None:
        if len(frame) < max(self.lookback_period, self.atr_period) + 2:
            return None

        high = frame["high"]
        low = frame["low"]
        close = frame["close"]

        atr = self._calculate_atr(frame)
        latest_close = Decimal(str(close.iloc[-1]))
        latest_atr = float(atr.iloc[-1])

        # 查找结构高低点
        if side == TradeSide.LONG:
            structure_high = float(high.iloc[-self.lookback_period : -1].max())
            breakout_distance = float(latest_close) - structure_high
        else:
            structure_low = float(low.iloc[-self.lookback_period : -1].min())
            breakout_distance = structure_low - float(latest_close)

        # 检查是否已突破
        if breakout_distance <= 0:
            return None

        # 突破距离不能太远
        if breakout_distance > self.max_breakout_atr * latest_atr:
            return None

        # 入场价：当前价市价单
        return EntrySetup(
            entry_type=OrderType.MARKET,
            reference_price=latest_close,
            trigger_price=None,
            max_chase_price=None,
            setup_reason="breakout_confirmed",
            expected_holding_bars=8,
        )

    def _calculate_atr(self, frame: pd.DataFrame) -> pd.Series:
        high = frame["high"]
        low = frame["low"]
        close = frame["close"]
        tr1 = high - low
        tr2 = (high - close.shift()).abs()
        tr3 = (low - close.shift()).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        return tr.ewm(span=self.atr_period, adjust=False).mean()


class RangeMeanReversionEntry(EntryModel):
    """区间均值回归入场模型."""

    def __init__(
        self,
        *,
        lookback_period: int = 20,
        atr_period: int = 14,
        deviation_threshold: float = 1.5,
    ):
        self.lookback_period = lookback_period
        self.atr_period = atr_period
        self.deviation_threshold = deviation_threshold

    def generate_entry(
        self,
        *,
        frame: pd.DataFrame,
        side: TradeSide,
        family: StrategyFamily,
        symbol: str,
    ) -> EntrySetup | None:
        if len(frame) < max(self.lookback_period, self.atr_period) + 2:
            return None

        close = frame["close"]

        latest_close = Decimal(str(close.iloc[-1]))
        mid = float(close.iloc[-self.lookback_period :].mean())
        std = float(close.iloc[-self.lookback_period :].std())

        # 检查偏离程度
        deviation = (float(latest_close) - mid) / std if std > 0 else 0

        if side == TradeSide.LONG and deviation >= -self.deviation_threshold:
            return None  # 多单需要价格低于均线
        if side == TradeSide.SHORT and deviation <= self.deviation_threshold:
            return None  # 空单需要价格高于均线

        # 入场价：市价单
        return EntrySetup(
            entry_type=OrderType.MARKET,
            reference_price=latest_close,
            trigger_price=None,
            max_chase_price=None,
            setup_reason="mean_reversion_extreme",
            expected_holding_bars=6,
        )

    def _calculate_atr(self, frame: pd.DataFrame) -> pd.Series:
        high = frame["high"]
        low = frame["low"]
        close = frame["close"]
        tr1 = high - low
        tr2 = (high - close.shift()).abs()
        tr3 = (low - close.shift()).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        return tr.ewm(span=self.atr_period, adjust=False).mean()


class MomentumContinuationEntry(EntryModel):
    """动量延续入场模型."""

    def __init__(
        self,
        *,
        momentum_period: int = 10,
        atr_period: int = 14,
    ):
        self.momentum_period = momentum_period
        self.atr_period = atr_period

    def generate_entry(
        self,
        *,
        frame: pd.DataFrame,
        side: TradeSide,
        family: StrategyFamily,
        symbol: str,
    ) -> EntrySetup | None:
        if len(frame) < max(self.momentum_period, self.atr_period) + 2:
            return None

        close = frame["close"]
        momentum = close.pct_change(self.momentum_period)

        latest_close = Decimal(str(close.iloc[-1]))
        latest_momentum = float(momentum.iloc[-1])

        # 检查动量方向
        if side == TradeSide.LONG and latest_momentum <= 0:
            return None
        if side == TradeSide.SHORT and latest_momentum >= 0:
            return None

        # 入场价：市价单
        return EntrySetup(
            entry_type=OrderType.MARKET,
            reference_price=latest_close,
            trigger_price=None,
            max_chase_price=None,
            setup_reason="momentum_continuation",
            expected_holding_bars=10,
        )


def get_entry_model(family: StrategyFamily) -> EntryModel:
    """根据策略族获取入场模型."""
    if family == StrategyFamily.TREND_PULLBACK:
        return TrendPullbackEntry()
    if family == StrategyFamily.BREAKOUT:
        return BreakoutEntry()
    if family == StrategyFamily.RANGE_MEAN_REVERSION:
        return RangeMeanReversionEntry()
    if family == StrategyFamily.MOMENTUM_CONTINUATION:
        return MomentumContinuationEntry()
    # 默认使用突破模型
    return BreakoutEntry()
