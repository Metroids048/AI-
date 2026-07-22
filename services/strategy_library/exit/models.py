"""Exit models for different strategy families.

不同策略族使用专属的止损止盈逻辑。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pandas as pd

from services.strategy_library.family import StrategyFamily
from shared.models import TradeSide


@dataclass(frozen=True)
class ExitPlan:
    """出场计划."""

    initial_stop: Decimal
    target_1: Decimal | None  # 第一目标（部分止盈）
    target_2: Decimal | None  # 第二目标
    time_exit_bars: int  # 时间止损（K线数）
    trailing_activation_r: float | None  # 追踪止损激活R值
    exit_reason: str


class ExitModel:
    """出场模型基类."""

    def generate_exit_plan(
        self,
        *,
        frame: pd.DataFrame,
        side: TradeSide,
        family: StrategyFamily,
        entry_price: Decimal,
        symbol: str,
    ) -> ExitPlan:
        """生成出场计划.

        Args:
            frame: OHLCV数据
            side: 方向
            family: 策略族
            entry_price: 入场价格
            symbol: 交易对

        Returns:
            出场计划
        """
        raise NotImplementedError


class TrendPullbackExit(ExitModel):
    """趋势回调出场模型."""

    def __init__(
        self,
        *,
        atr_period: int = 14,
        stop_atr_multiple: float = 2.0,
        target_1_r: float = 1.5,
        target_2_r: float = 2.5,
        time_exit_bars: int = 24,
    ):
        self.atr_period = atr_period
        self.stop_atr_multiple = stop_atr_multiple
        self.target_1_r = target_1_r
        self.target_2_r = target_2_r
        self.time_exit_bars = time_exit_bars

    def generate_exit_plan(
        self,
        *,
        frame: pd.DataFrame,
        side: TradeSide,
        family: StrategyFamily,
        entry_price: Decimal,
        symbol: str,
    ) -> ExitPlan:
        if len(frame) < self.atr_period + 2:
            # 使用默认止损
            stop_distance = entry_price * Decimal("0.02")
        else:
            atr = self._calculate_atr(frame)
            stop_distance = Decimal(str(atr.iloc[-1])) * Decimal(str(self.stop_atr_multiple))

        # 计算止损和止盈
        if side == TradeSide.LONG:
            initial_stop = entry_price - stop_distance
            target_1 = entry_price + stop_distance * Decimal(str(self.target_1_r))
            target_2 = entry_price + stop_distance * Decimal(str(self.target_2_r))
        else:
            initial_stop = entry_price + stop_distance
            target_1 = entry_price - stop_distance * Decimal(str(self.target_1_r))
            target_2 = entry_price - stop_distance * Decimal(str(self.target_2_r))

        return ExitPlan(
            initial_stop=initial_stop,
            target_1=target_1,
            target_2=target_2,
            time_exit_bars=self.time_exit_bars,
            trailing_activation_r=self.target_1_r,
            exit_reason="trend_pullback_exit",
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


class BreakoutExit(ExitModel):
    """突破出场模型."""

    def __init__(
        self,
        *,
        atr_period: int = 14,
        stop_atr_multiple: float = 1.5,
        target_1_r: float = 2.0,
        target_2_r: float = 3.0,
        time_exit_bars: int = 16,
    ):
        self.atr_period = atr_period
        self.stop_atr_multiple = stop_atr_multiple
        self.target_1_r = target_1_r
        self.target_2_r = target_2_r
        self.time_exit_bars = time_exit_bars

    def generate_exit_plan(
        self,
        *,
        frame: pd.DataFrame,
        side: TradeSide,
        family: StrategyFamily,
        entry_price: Decimal,
        symbol: str,
    ) -> ExitPlan:
        if len(frame) < self.atr_period + 2:
            stop_distance = entry_price * Decimal("0.015")
        else:
            atr = self._calculate_atr(frame)
            stop_distance = Decimal(str(atr.iloc[-1])) * Decimal(str(self.stop_atr_multiple))

        # 突破策略：止损在突破点内部
        if side == TradeSide.LONG:
            # 查找最近的结构低点
            if len(frame) >= 20:
                structure_low = Decimal(str(frame["low"].iloc[-20:-1].min()))
                initial_stop = max(structure_low, entry_price - stop_distance)
            else:
                initial_stop = entry_price - stop_distance
            target_1 = entry_price + (entry_price - initial_stop) * Decimal(str(self.target_1_r))
            target_2 = entry_price + (entry_price - initial_stop) * Decimal(str(self.target_2_r))
        else:
            if len(frame) >= 20:
                structure_high = Decimal(str(frame["high"].iloc[-20:-1].max()))
                initial_stop = min(structure_high, entry_price + stop_distance)
            else:
                initial_stop = entry_price + stop_distance
            target_1 = entry_price - (initial_stop - entry_price) * Decimal(str(self.target_1_r))
            target_2 = entry_price - (initial_stop - entry_price) * Decimal(str(self.target_2_r))

        return ExitPlan(
            initial_stop=initial_stop,
            target_1=target_1,
            target_2=target_2,
            time_exit_bars=self.time_exit_bars,
            trailing_activation_r=self.target_1_r,
            exit_reason="breakout_exit",
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


class RangeMeanReversionExit(ExitModel):
    """区间均值回归出场模型."""

    def __init__(
        self,
        *,
        lookback_period: int = 20,
        atr_period: int = 14,
        stop_atr_multiple: float = 1.5,
        time_exit_bars: int = 12,
    ):
        self.lookback_period = lookback_period
        self.atr_period = atr_period
        self.stop_atr_multiple = stop_atr_multiple
        self.time_exit_bars = time_exit_bars

    def generate_exit_plan(
        self,
        *,
        frame: pd.DataFrame,
        side: TradeSide,
        family: StrategyFamily,
        entry_price: Decimal,
        symbol: str,
    ) -> ExitPlan:
        if len(frame) < max(self.lookback_period, self.atr_period) + 2:
            stop_distance = entry_price * Decimal("0.02")
            mid = entry_price
        else:
            atr = self._calculate_atr(frame)
            stop_distance = Decimal(str(atr.iloc[-1])) * Decimal(str(self.stop_atr_multiple))
            mid = Decimal(str(frame["close"].iloc[-self.lookback_period :].mean()))

        # 区间策略：止损在区间边界外，止盈在中线或对侧
        if side == TradeSide.LONG:
            initial_stop = entry_price - stop_distance
            target_1 = mid  # 第一目标：回到中线
            target_2 = mid + (mid - entry_price)  # 第二目标：对侧
        else:
            initial_stop = entry_price + stop_distance
            target_1 = mid
            target_2 = mid - (entry_price - mid)

        return ExitPlan(
            initial_stop=initial_stop,
            target_1=target_1,
            target_2=target_2,
            time_exit_bars=self.time_exit_bars,
            trailing_activation_r=None,  # 区间策略不使用追踪止损
            exit_reason="range_mean_reversion_exit",
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


class MomentumContinuationExit(ExitModel):
    """动量延续出场模型."""

    def __init__(
        self,
        *,
        atr_period: int = 14,
        stop_atr_multiple: float = 1.8,
        target_1_r: float = 1.8,
        target_2_r: float = 3.0,
        time_exit_bars: int = 20,
    ):
        self.atr_period = atr_period
        self.stop_atr_multiple = stop_atr_multiple
        self.target_1_r = target_1_r
        self.target_2_r = target_2_r
        self.time_exit_bars = time_exit_bars

    def generate_exit_plan(
        self,
        *,
        frame: pd.DataFrame,
        side: TradeSide,
        family: StrategyFamily,
        entry_price: Decimal,
        symbol: str,
    ) -> ExitPlan:
        if len(frame) < self.atr_period + 2:
            stop_distance = entry_price * Decimal("0.018")
        else:
            atr = self._calculate_atr(frame)
            stop_distance = Decimal(str(atr.iloc[-1])) * Decimal(str(self.stop_atr_multiple))

        if side == TradeSide.LONG:
            initial_stop = entry_price - stop_distance
            target_1 = entry_price + stop_distance * Decimal(str(self.target_1_r))
            target_2 = entry_price + stop_distance * Decimal(str(self.target_2_r))
        else:
            initial_stop = entry_price + stop_distance
            target_1 = entry_price - stop_distance * Decimal(str(self.target_1_r))
            target_2 = entry_price - stop_distance * Decimal(str(self.target_2_r))

        return ExitPlan(
            initial_stop=initial_stop,
            target_1=target_1,
            target_2=target_2,
            time_exit_bars=self.time_exit_bars,
            trailing_activation_r=self.target_1_r,
            exit_reason="momentum_continuation_exit",
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


def get_exit_model(family: StrategyFamily) -> ExitModel:
    """根据策略族获取出场模型."""
    if family == StrategyFamily.TREND_PULLBACK:
        return TrendPullbackExit()
    if family == StrategyFamily.BREAKOUT:
        return BreakoutExit()
    if family == StrategyFamily.RANGE_MEAN_REVERSION:
        return RangeMeanReversionExit()
    if family == StrategyFamily.MOMENTUM_CONTINUATION:
        return MomentumContinuationExit()
    # 默认使用突破模型
    return BreakoutExit()
