"""C+ Weighted Ensemble Strategy - Five-layer architecture integration.

五层架构流程：
1. RegimeRouter: 识别市场状态
2. StrategyFamilySelector: 对信号分类并应用市场权重
3. WeightedEnsemble: 加权合成得到方向和置信度
4. EntryModel: 生成具体入场设置
5. ExitModel: 生成止损止盈计划
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import pandas as pd

from services.strategy_library.ensemble.weighted import EnsembleResult, WeightedEnsembleService
from services.strategy_library.entry import EntrySetup, get_entry_model
from services.strategy_library.exit import ExitPlan, get_exit_model
from services.strategy_library.family import StrategyFamilySelector
from services.strategy_library.regime import MarketRegime, RegimeRouter
from shared.models import TradeSignal


@dataclass(frozen=True)
class CPlusDecision:
    """C+策略决策结果."""

    decision_id: str
    symbol: str
    timestamp: datetime

    # Layer 1: Regime
    regime: MarketRegime

    # Layer 2-3: Family + Ensemble
    ensemble: EnsembleResult

    # Layer 4: Entry
    entry_setup: EntrySetup | None

    # Layer 5: Exit
    exit_plan: ExitPlan | None

    # Meta
    rejection_reason: str | None


class CPlusStrategyService:
    """C+ 加权Ensemble策略服务.

    整合五层架构，从信号到完整交易决策。
    """

    def __init__(self):
        """初始化C+策略服务."""
        self.regime_router = RegimeRouter()
        self.family_selector = StrategyFamilySelector()
        self.ensemble = WeightedEnsembleService(
            long_threshold=0.35,
            short_threshold=-0.35,
            min_total_weight=0.5,
        )

    def make_decision(
        self,
        *,
        symbol: str,
        signals: list[TradeSignal],
        frame: pd.DataFrame,
    ) -> CPlusDecision:
        """执行完整五层决策流程.

        Args:
            symbol: 交易对
            signals: 原始技术信号列表
            frame: OHLCV数据（已收盘K线）

        Returns:
            C+决策结果
        """
        decision_id = str(uuid.uuid4())
        timestamp = datetime.now(UTC)

        # Layer 1: 识别市场状态
        regime, regime_weight = self.regime_router.identify_regime(frame)

        # Layer 2: 策略族分类和权重
        weighted_signals = self.family_selector.classify_and_weight(signals, regime_weight)

        # Layer 3: 加权合成
        ensemble_result = self.ensemble.aggregate(weighted_signals)

        # 如果没有方向，直接拒绝
        if ensemble_result.final_side is None:
            return CPlusDecision(
                decision_id=decision_id,
                symbol=symbol,
                timestamp=timestamp,
                regime=regime,
                ensemble=ensemble_result,
                entry_setup=None,
                exit_plan=None,
                rejection_reason="ensemble_no_direction",
            )

        # Layer 4: 生成入场设置
        if ensemble_result.selected_family is None:
            return CPlusDecision(
                decision_id=decision_id,
                symbol=symbol,
                timestamp=timestamp,
                regime=regime,
                ensemble=ensemble_result,
                entry_setup=None,
                exit_plan=None,
                rejection_reason="no_dominant_family",
            )

        entry_model = get_entry_model(ensemble_result.selected_family)
        entry_setup = entry_model.generate_entry(
            frame=frame,
            side=ensemble_result.final_side,
            family=ensemble_result.selected_family,
            symbol=symbol,
        )

        if entry_setup is None:
            return CPlusDecision(
                decision_id=decision_id,
                symbol=symbol,
                timestamp=timestamp,
                regime=regime,
                ensemble=ensemble_result,
                entry_setup=None,
                exit_plan=None,
                rejection_reason="entry_condition_not_met",
            )

        # Layer 5: 生成出场计划
        exit_model = get_exit_model(ensemble_result.selected_family)
        exit_plan = exit_model.generate_exit_plan(
            frame=frame,
            side=ensemble_result.final_side,
            family=ensemble_result.selected_family,
            entry_price=entry_setup.reference_price,
            symbol=symbol,
        )

        return CPlusDecision(
            decision_id=decision_id,
            symbol=symbol,
            timestamp=timestamp,
            regime=regime,
            ensemble=ensemble_result,
            entry_setup=entry_setup,
            exit_plan=exit_plan,
            rejection_reason=None,
        )

    def format_decision_summary(self, decision: CPlusDecision) -> str:
        """格式化决策摘要供日志输出."""
        if decision.rejection_reason:
            return (
                f"C+ Decision {decision.decision_id[:8]} [{decision.symbol}] "
                f"REJECTED: {decision.rejection_reason} "
                f"(regime={decision.regime.value}, score={decision.ensemble.final_score:.2f})"
            )

        ensemble = decision.ensemble
        entry = decision.entry_setup
        exit_plan = decision.exit_plan

        side_label = ensemble.final_side.value.upper() if ensemble.final_side is not None else "NONE"
        return (
            f"C+ Decision {decision.decision_id[:8]} [{decision.symbol}] "
            f"{side_label} "
            f"regime={decision.regime.value} "
            f"family={ensemble.selected_family.value if ensemble.selected_family else 'N/A'} "
            f"score={ensemble.final_score:.2f} "
            f"confidence={ensemble.confidence:.2f} "
            f"entry={entry.entry_type.value if entry else 'N/A'}@{entry.reference_price if entry else 'N/A'} "
            f"stop={exit_plan.initial_stop if exit_plan else 'N/A'} "
            f"target1={exit_plan.target_1 if exit_plan else 'N/A'}"
        )
