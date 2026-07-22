"""Adapter to integrate C+ strategy into DecisionPipeline.

将C+五层架构集成到现有的决策流程中。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

import pandas as pd

from services.data import DataRepository
from services.strategy_library.c_plus import CPlusStrategyService
from shared.models import OrderType, StrategyContract, TradeSide, TradeSignal

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CPlusAdapterResult:
    """C+适配器结果."""

    direction: TradeSide | None
    reference_price: Decimal
    entry_price: Decimal | None
    stop_price: Decimal
    take_profit_price: Decimal | None
    order_type: OrderType
    confidence: float
    rejection_reason: str | None
    decision_id: str
    regime: str
    strategy_family: str | None


class CPlusDecisionAdapter:
    """将C+决策适配到现有的DecisionPipeline接口."""

    def __init__(self, *, data_repo: DataRepository):
        """初始化适配器.

        Args:
            data_repo: 数据仓储
        """
        self.data_repo = data_repo
        self.c_plus = CPlusStrategyService()

    def run_c_plus_decision(
        self,
        *,
        symbol: str,
        timeframe: str,
        signals: list[TradeSignal],
        strategy: StrategyContract,
    ) -> CPlusAdapterResult:
        """运行C+决策并适配到现有接口.

        Args:
            symbol: 交易对
            timeframe: 时间框架
            signals: 技术信号列表
            strategy: 策略契约

        Returns:
            适配后的决策结果
        """
        # 获取OHLCV数据
        frame = self._fetch_ohlcv(symbol=symbol, timeframe=timeframe, limit=200)

        if frame is None or len(frame) < 50:
            logger.warning(f"C+ adapter: insufficient data for {symbol} {timeframe}")
            return self._create_rejection(
                symbol=symbol,
                reason="insufficient_ohlcv_data",
            )

        # 运行C+决策
        try:
            decision = self.c_plus.make_decision(
                symbol=symbol,
                signals=signals,
                frame=frame,
            )
        except Exception as e:
            logger.exception(f"C+ decision failed for {symbol}: {e}")
            return self._create_rejection(
                symbol=symbol,
                reason=f"c_plus_execution_error: {e!s}",
            )

        # 转换为适配器结果
        if decision.rejection_reason:
            return self._create_rejection(
                symbol=symbol,
                reason=decision.rejection_reason,
                decision_id=decision.decision_id,
                regime=decision.regime.value,
            )

        if decision.entry_setup is None or decision.exit_plan is None:
            return self._create_rejection(
                symbol=symbol,
                reason="missing_entry_or_exit",
                decision_id=decision.decision_id,
                regime=decision.regime.value,
            )

        # 构造成功结果
        entry_setup = decision.entry_setup
        exit_plan = decision.exit_plan
        ensemble = decision.ensemble

        return CPlusAdapterResult(
            direction=ensemble.final_side,
            reference_price=entry_setup.reference_price,
            entry_price=entry_setup.trigger_price or entry_setup.reference_price,
            stop_price=exit_plan.initial_stop,
            take_profit_price=exit_plan.target_1,
            order_type=entry_setup.entry_type,
            confidence=ensemble.confidence,
            rejection_reason=None,
            decision_id=decision.decision_id,
            regime=decision.regime.value,
            strategy_family=ensemble.selected_family.value if ensemble.selected_family else None,
        )

    def _fetch_ohlcv(
        self,
        *,
        symbol: str,
        timeframe: str,
        limit: int = 200,
    ) -> pd.DataFrame | None:
        """获取OHLCV数据."""
        try:
            bars = self.data_repo.list_ohlcv_bars(
                symbol=symbol,
                timeframe=timeframe,
                limit=limit,
            )
            if not bars:
                return None

            data = {
                "timestamp": [bar.timestamp for bar in bars],
                "open": [float(bar.open) for bar in bars],
                "high": [float(bar.high) for bar in bars],
                "low": [float(bar.low) for bar in bars],
                "close": [float(bar.close) for bar in bars],
                "volume": [float(bar.volume) for bar in bars],
            }
            return pd.DataFrame(data)
        except Exception as e:
            logger.exception(f"Failed to fetch OHLCV for {symbol} {timeframe}: {e}")
            return None

    def _create_rejection(
        self,
        *,
        symbol: str,
        reason: str,
        decision_id: str = "unknown",
        regime: str = "unknown",
    ) -> CPlusAdapterResult:
        """创建拒绝结果."""
        return CPlusAdapterResult(
            direction=None,
            reference_price=Decimal("0"),
            entry_price=None,
            stop_price=Decimal("0"),
            take_profit_price=None,
            order_type=OrderType.MARKET,
            confidence=0.0,
            rejection_reason=reason,
            decision_id=decision_id,
            regime=regime,
            strategy_family=None,
        )
