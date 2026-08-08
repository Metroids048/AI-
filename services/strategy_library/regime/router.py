"""Market regime identification for strategy family selection.

Regime识别不直接决定是否开仓，而是用于调整各策略族的权重。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import pandas as pd


class MarketRegime(StrEnum):
    """市场状态分类."""

    TREND_UP = "trend_up"
    TREND_DOWN = "trend_down"
    RANGE = "range"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True)
class RegimeWeight:
    """策略族在当前市场状态下的权重调整."""

    trend_pullback: float
    breakout: float
    range_mean_reversion: float
    momentum_continuation: float


class RegimeRouter:
    """识别当前市场状态并返回策略族权重调整."""

    def __init__(
        self,
        *,
        trend_ema_periods: tuple[int, int] = (50, 200),
        adx_period: int = 14,
        adx_trend_threshold: float = 25.0,
        atr_period: int = 14,
        range_atr_multiple: float = 1.5,
    ):
        """初始化Regime Router.

        Args:
            trend_ema_periods: 快慢EMA周期，用于判断趋势方向
            adx_period: ADX周期，用于判断趋势强度
            adx_trend_threshold: ADX阈值，高于此值认为有趋势
            atr_period: ATR周期，用于判断波动率
            range_atr_multiple: 区间判断的ATR倍数
        """
        self.fast_ema, self.slow_ema = trend_ema_periods
        self.adx_period = adx_period
        self.adx_threshold = adx_trend_threshold
        self.atr_period = atr_period
        self.range_atr_multiple = range_atr_multiple

    def identify_regime(self, frame: pd.DataFrame) -> tuple[MarketRegime, RegimeWeight]:
        """识别市场状态并返回策略族权重.

        Args:
            frame: OHLCV数据，至少包含close, high, low列

        Returns:
            (市场状态, 策略族权重调整)
        """
        if len(frame) < max(self.slow_ema, self.adx_period + 1):
            return MarketRegime.UNCERTAIN, self._uncertain_weights()

        # 计算趋势方向
        fast_ema = frame["close"].ewm(span=self.fast_ema, adjust=False).mean()
        slow_ema = frame["close"].ewm(span=self.slow_ema, adjust=False).mean()
        ema_diff = fast_ema.iloc[-1] - slow_ema.iloc[-1]
        ema_diff_pct = ema_diff / slow_ema.iloc[-1]

        # 计算趋势强度 (ADX)
        adx = self._calculate_adx(frame)

        # 计算波动率环境
        atr = self._calculate_atr(frame)
        price_range = frame["high"].rolling(20).max() - frame["low"].rolling(20).min()
        latest_range = price_range.iloc[-1]
        range_threshold = atr.iloc[-1] * self.range_atr_multiple

        # 趋势判断逻辑
        # CHANGED 2026-08-07 to fix R-02: Let ADX 15-25 enter TREND instead of UNCERTAIN
        # Previous gate17 tightened RANGE to ADX<15, pushing ADX 15-25 into UNCERTAIN
        # where strict MTF killed all signals. Now we expand TREND downward to capture
        # the 15-25 transition zone when EMA bias exists.
        #
        # New logic:
        # - ADX >= 15 + clear EMA bias (>2% or <-2%) → TREND (was: ADX > 25)
        # - ADX < 15 + narrow price range → RANGE (unchanged from gate17)
        # - Otherwise → UNCERTAIN
        #
        # Rationale: ADX 15-25 with directional EMA bias is a valid early-trend state;
        # gate17's RANGE tightening was correct but we also need to lower the TREND
        # entry threshold to avoid orphaning the 15-25 zone into UNCERTAIN.
        has_ema_bias = abs(ema_diff_pct) > 0.02
        is_trending = (adx.iloc[-1] >= 15.0 and has_ema_bias) or (adx.iloc[-1] > self.adx_threshold)
        is_uptrend = ema_diff_pct > 0.02  # 快线高于慢线2%
        is_downtrend = ema_diff_pct < -0.02
        # RANGE: ADX < 15 (very weak trend) AND narrow price range
        is_range = (latest_range < range_threshold * 20) and (adx.iloc[-1] < 15.0)

        # 状态判断
        if is_trending and is_uptrend:
            return MarketRegime.TREND_UP, self._trend_up_weights(adx.iloc[-1])
        if is_trending and is_downtrend:
            return MarketRegime.TREND_DOWN, self._trend_down_weights(adx.iloc[-1])
        if is_range:
            return MarketRegime.RANGE, self._range_weights()

        return MarketRegime.UNCERTAIN, self._uncertain_weights()

    def _calculate_adx(self, frame: pd.DataFrame) -> pd.Series:
        """计算ADX (Average Directional Index)."""
        high = frame["high"]
        low = frame["low"]
        close = frame["close"]

        # True Range
        tr1 = high - low
        tr2 = (high - close.shift()).abs()
        tr3 = (low - close.shift()).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        # Directional Movement
        up_move = high - high.shift()
        down_move = low.shift() - low

        plus_dm = pd.Series(0.0, index=frame.index)
        minus_dm = pd.Series(0.0, index=frame.index)

        plus_dm[(up_move > down_move) & (up_move > 0)] = up_move
        minus_dm[(down_move > up_move) & (down_move > 0)] = down_move

        # Smoothed indicators
        atr = tr.ewm(span=self.adx_period, adjust=False).mean()
        plus_di = 100 * (plus_dm.ewm(span=self.adx_period, adjust=False).mean() / atr)
        minus_di = 100 * (minus_dm.ewm(span=self.adx_period, adjust=False).mean() / atr)

        # ADX
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1)
        return dx.ewm(span=self.adx_period, adjust=False).mean()

    def _calculate_atr(self, frame: pd.DataFrame) -> pd.Series:
        """计算ATR (Average True Range)."""
        high = frame["high"]
        low = frame["low"]
        close = frame["close"]

        tr1 = high - low
        tr2 = (high - close.shift()).abs()
        tr3 = (low - close.shift()).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        return tr.ewm(span=self.atr_period, adjust=False).mean()

    def _trend_up_weights(self, adx_value: float) -> RegimeWeight:
        """上升趋势：提高多头趋势、突破和回调权重."""
        trend_strength = min((adx_value - self.adx_threshold) / 30.0, 1.0)
        return RegimeWeight(
            trend_pullback=0.8 + 0.2 * trend_strength,
            breakout=0.9 + 0.1 * trend_strength,
            range_mean_reversion=0.3,  # 降低逆势均值回归
            momentum_continuation=0.85 + 0.15 * trend_strength,
        )

    def _trend_down_weights(self, adx_value: float) -> RegimeWeight:
        """下降趋势：提高空头趋势、突破和反弹失败权重."""
        trend_strength = min((adx_value - self.adx_threshold) / 30.0, 1.0)
        return RegimeWeight(
            trend_pullback=0.8 + 0.2 * trend_strength,  # 空头回调
            breakout=0.9 + 0.1 * trend_strength,  # 向下突破
            range_mean_reversion=0.3,  # 降低逆势均值回归
            momentum_continuation=0.85 + 0.15 * trend_strength,
        )

    def _range_weights(self) -> RegimeWeight:
        """区间震荡：提高均值回归，降低趋势突破."""
        return RegimeWeight(
            trend_pullback=0.4,  # 降低趋势延续
            breakout=0.3,  # 降低突破
            range_mean_reversion=1.0,  # 提高区间反转
            momentum_continuation=0.35,
        )

    def _uncertain_weights(self) -> RegimeWeight:
        """不确定状态：所有策略均等权重，但整体降低置信度."""
        return RegimeWeight(
            trend_pullback=0.6,
            breakout=0.6,
            range_mean_reversion=0.6,
            momentum_continuation=0.6,
        )
