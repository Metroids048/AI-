"""Strategy family classification and weighting.

根据信号源将TradeSignal分类到策略族，并应用市场状态权重。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from services.strategy_library.regime import RegimeWeight
from shared.models import TradeSide, TradeSignal


class StrategyFamily(StrEnum):
    """策略族分类."""

    TREND_PULLBACK = "trend_pullback"
    BREAKOUT = "breakout"
    RANGE_MEAN_REVERSION = "range_mean_reversion"
    MOMENTUM_CONTINUATION = "momentum_continuation"
    UNKNOWN = "unknown"


# 信号源到策略族的映射
SIGNAL_TO_FAMILY: dict[str, StrategyFamily] = {
    # 趋势回调策略
    "technical_dow_trend": StrategyFamily.TREND_PULLBACK,
    "technical_ema_trend": StrategyFamily.TREND_PULLBACK,
    "technical_mtf_ma": StrategyFamily.TREND_PULLBACK,
    # 突破策略
    "technical_fvg": StrategyFamily.BREAKOUT,
    "technical_chan_theory": StrategyFamily.BREAKOUT,
    # 区间均值回归
    "technical_bollinger": StrategyFamily.RANGE_MEAN_REVERSION,
    "technical_rsi": StrategyFamily.RANGE_MEAN_REVERSION,
    "technical_vwap": StrategyFamily.RANGE_MEAN_REVERSION,
    # 动量延续
    "technical_macd": StrategyFamily.MOMENTUM_CONTINUATION,
    "technical_adx": StrategyFamily.MOMENTUM_CONTINUATION,
}


@dataclass(frozen=True)
class WeightedSignal:
    """带权重的信号."""

    signal: TradeSignal
    family: StrategyFamily
    base_weight: float
    regime_weight: float
    final_weight: float
    direction_score: float  # -1 to +1


class StrategyFamilySelector:
    """策略族分类和权重分配."""

    def __init__(
        self,
        *,
        base_weights: dict[StrategyFamily, float] | None = None,
    ):
        """初始化策略族选择器.

        Args:
            base_weights: 各策略族的基础权重，默认均为1.0
        """
        self.base_weights = base_weights or {
            StrategyFamily.TREND_PULLBACK: 1.0,
            StrategyFamily.BREAKOUT: 1.0,
            StrategyFamily.RANGE_MEAN_REVERSION: 1.0,
            StrategyFamily.MOMENTUM_CONTINUATION: 1.0,
            StrategyFamily.UNKNOWN: 0.5,  # 未分类信号降低权重
        }

    def classify_and_weight(
        self,
        signals: list[TradeSignal],
        regime_weight: RegimeWeight,
    ) -> list[WeightedSignal]:
        """将信号分类到策略族并计算最终权重.

        Args:
            signals: 原始交易信号列表
            regime_weight: 市场状态权重调整

        Returns:
            带权重的信号列表
        """
        weighted_signals: list[WeightedSignal] = []

        for signal in signals:
            family = self._classify_signal(signal)
            base_weight = self.base_weights.get(family, 0.5)
            regime_mult = self._get_regime_multiplier(family, regime_weight)
            final_weight = base_weight * regime_mult * (signal.confidence or 0.0)

            # 方向分数：LONG=+1, SHORT=-1
            direction_score = 1.0 if signal.side == TradeSide.LONG else -1.0

            weighted_signals.append(
                WeightedSignal(
                    signal=signal,
                    family=family,
                    base_weight=base_weight,
                    regime_weight=regime_mult,
                    final_weight=final_weight,
                    direction_score=direction_score,
                )
            )

        return weighted_signals

    def _classify_signal(self, signal: TradeSignal) -> StrategyFamily:
        """将信号分类到策略族."""
        return SIGNAL_TO_FAMILY.get(signal.source, StrategyFamily.UNKNOWN)

    def _get_regime_multiplier(self, family: StrategyFamily, regime_weight: RegimeWeight) -> float:
        """获取策略族在当前市场状态下的权重倍数."""
        if family == StrategyFamily.TREND_PULLBACK:
            return regime_weight.trend_pullback
        if family == StrategyFamily.BREAKOUT:
            return regime_weight.breakout
        if family == StrategyFamily.RANGE_MEAN_REVERSION:
            return regime_weight.range_mean_reversion
        if family == StrategyFamily.MOMENTUM_CONTINUATION:
            return regime_weight.momentum_continuation
        return 0.5  # UNKNOWN降低权重
