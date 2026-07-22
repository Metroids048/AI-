"""Weighted ensemble that aggregates strategy family signals without strict consensus.

不再要求所有信号一致，而是通过加权合成得到最终方向和置信度。
"""

from __future__ import annotations

from dataclasses import dataclass

from services.strategy_library.family import StrategyFamily, WeightedSignal
from shared.models import TradeSide


@dataclass(frozen=True)
class EnsembleResult:
    """加权合成结果."""

    final_side: TradeSide | None
    final_score: float  # -1 to +1
    confidence: float  # 0 to 1
    selected_family: StrategyFamily | None
    contributors: dict[str, float]  # signal_source -> weighted_score
    long_weight: float
    short_weight: float
    total_weight: float


class WeightedEnsembleService:
    """加权合成服务，不再硬性要求信号一致."""

    def __init__(
        self,
        *,
        long_threshold: float = 0.35,
        short_threshold: float = -0.35,
        min_total_weight: float = 0.5,
    ):
        """初始化加权合成服务.

        Args:
            long_threshold: 多头方向阈值（>=此值认为多头）
            short_threshold: 空头方向阈值（<=此值认为空头）
            min_total_weight: 最小总权重阈值（低于此值拒绝）
        """
        self.long_threshold = long_threshold
        self.short_threshold = short_threshold
        self.min_total_weight = min_total_weight

    def aggregate(
        self,
        weighted_signals: list[WeightedSignal],
    ) -> EnsembleResult:
        """加权合成所有信号.

        Args:
            weighted_signals: 带权重的信号列表

        Returns:
            合成结果
        """
        if not weighted_signals:
            return EnsembleResult(
                final_side=None,
                final_score=0.0,
                confidence=0.0,
                selected_family=None,
                contributors={},
                long_weight=0.0,
                short_weight=0.0,
                total_weight=0.0,
            )

        # 计算加权分数
        weighted_scores: dict[str, float] = {}
        long_weight = 0.0
        short_weight = 0.0
        total_weight = 0.0

        for ws in weighted_signals:
            weighted_score = ws.direction_score * ws.final_weight
            weighted_scores[ws.signal.source] = weighted_score

            total_weight += ws.final_weight
            if ws.signal.side == TradeSide.LONG:
                long_weight += ws.final_weight
            else:
                short_weight += ws.final_weight

        # 总权重不足，拒绝
        if total_weight < self.min_total_weight:
            return EnsembleResult(
                final_side=None,
                final_score=0.0,
                confidence=0.0,
                selected_family=None,
                contributors=weighted_scores,
                long_weight=long_weight,
                short_weight=short_weight,
                total_weight=total_weight,
            )

        # 计算最终分数：加权平均
        final_score = sum(weighted_scores.values()) / total_weight

        # 方向判断
        final_side: TradeSide | None = None
        if final_score >= self.long_threshold:
            final_side = TradeSide.LONG
        elif final_score <= self.short_threshold:
            final_side = TradeSide.SHORT

        # 置信度：基于分数距离阈值的远近
        if final_side == TradeSide.LONG:
            confidence = min((final_score - self.long_threshold) / (1.0 - self.long_threshold), 1.0)
        elif final_side == TradeSide.SHORT:
            confidence = min(
                (self.short_threshold - final_score) / (self.short_threshold - (-1.0)),
                1.0,
            )
        else:
            confidence = 0.0

        # 选择主导策略族
        selected_family = self._select_dominant_family(weighted_signals, final_side)

        return EnsembleResult(
            final_side=final_side,
            final_score=final_score,
            confidence=confidence,
            selected_family=selected_family,
            contributors=weighted_scores,
            long_weight=long_weight,
            short_weight=short_weight,
            total_weight=total_weight,
        )

    def _select_dominant_family(
        self,
        weighted_signals: list[WeightedSignal],
        final_side: TradeSide | None,
    ) -> StrategyFamily | None:
        """选择主导策略族（权重最大的同向策略族）."""
        if final_side is None:
            return None

        family_weights: dict[StrategyFamily, float] = {}
        for ws in weighted_signals:
            if ws.signal.side == final_side:
                family_weights[ws.family] = family_weights.get(ws.family, 0.0) + ws.final_weight

        if not family_weights:
            return None

        return max(family_weights, key=lambda f: family_weights[f])
