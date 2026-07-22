"""Test C+ five-layer strategy architecture."""

import pandas as pd
import pytest

from services.strategy_library.c_plus import CPlusStrategyService
from services.strategy_library.regime import MarketRegime
from shared.models import TradeSide, TradeSignal


@pytest.fixture
def sample_frame() -> pd.DataFrame:
    """生成测试用的OHLCV数据."""
    data = {
        "timestamp": pd.date_range("2024-01-01", periods=100, freq="1h"),
        "open": range(50000, 50100),
        "high": range(50100, 50200),
        "low": range(49900, 50000),
        "close": range(50000, 50100),
        "volume": [1000.0] * 100,
    }
    return pd.DataFrame(data)


@pytest.fixture
def uptrend_frame() -> pd.DataFrame:
    """生成上升趋势数据."""
    close_prices = [50000 + i * 50 for i in range(100)]
    data = {
        "timestamp": pd.date_range("2024-01-01", periods=100, freq="1h"),
        "open": close_prices,
        "high": [p + 100 for p in close_prices],
        "low": [p - 50 for p in close_prices],
        "close": close_prices,
        "volume": [1000.0] * 100,
    }
    return pd.DataFrame(data)


@pytest.fixture
def downtrend_frame() -> pd.DataFrame:
    """生成下降趋势数据."""
    close_prices = [50000 - i * 50 for i in range(100)]
    data = {
        "timestamp": pd.date_range("2024-01-01", periods=100, freq="1h"),
        "open": close_prices,
        "high": [p + 50 for p in close_prices],
        "low": [p - 100 for p in close_prices],
        "close": close_prices,
        "volume": [1000.0] * 100,
    }
    return pd.DataFrame(data)


@pytest.fixture
def range_frame() -> pd.DataFrame:
    """生成区间震荡数据."""
    import math

    close_prices = [50000 + 500 * math.sin(i * 0.1) for i in range(100)]
    data = {
        "timestamp": pd.date_range("2024-01-01", periods=100, freq="1h"),
        "open": close_prices,
        "high": [p + 100 for p in close_prices],
        "low": [p - 100 for p in close_prices],
        "close": close_prices,
        "volume": [1000.0] * 100,
    }
    return pd.DataFrame(data)


@pytest.fixture
def long_signals() -> list[TradeSignal]:
    """生成多头信号."""
    return [
        TradeSignal(
            symbol="BTC/USDT",
            side=TradeSide.LONG,
            source="technical_ema_trend",
            signal_time=pd.Timestamp("2024-01-01 12:00:00"),
            reason="uptrend_confirmed",
            confidence=0.8,
        ),
        TradeSignal(
            symbol="BTC/USDT",
            side=TradeSide.LONG,
            source="technical_macd",
            signal_time=pd.Timestamp("2024-01-01 12:00:00"),
            reason="bullish_crossover",
            confidence=0.7,
        ),
        TradeSignal(
            symbol="BTC/USDT",
            side=TradeSide.LONG,
            source="technical_fvg",
            signal_time=pd.Timestamp("2024-01-01 12:00:00"),
            reason="fvg_breakout",
            confidence=0.75,
        ),
    ]


@pytest.fixture
def mixed_signals() -> list[TradeSignal]:
    """生成混合方向信号."""
    return [
        TradeSignal(
            symbol="BTC/USDT",
            side=TradeSide.LONG,
            source="technical_ema_trend",
            signal_time=pd.Timestamp("2024-01-01 12:00:00"),
            reason="uptrend",
            confidence=0.7,
        ),
        TradeSignal(
            symbol="BTC/USDT",
            side=TradeSide.SHORT,
            source="technical_rsi",
            signal_time=pd.Timestamp("2024-01-01 12:00:00"),
            reason="overbought",
            confidence=0.6,
        ),
    ]


class TestRegimeRouter:
    """测试市场状态识别."""

    def test_identify_uptrend(self, uptrend_frame):
        from services.strategy_library.regime import RegimeRouter

        router = RegimeRouter()
        regime, weights = router.identify_regime(uptrend_frame)

        assert regime == MarketRegime.TREND_UP
        assert weights.trend_pullback > 0.7
        assert weights.breakout > 0.8
        assert weights.range_mean_reversion < 0.5

    def test_identify_downtrend(self, downtrend_frame):
        from services.strategy_library.regime import RegimeRouter

        router = RegimeRouter()
        regime, weights = router.identify_regime(downtrend_frame)

        assert regime == MarketRegime.TREND_DOWN
        assert weights.trend_pullback > 0.7

    def test_identify_range(self, range_frame):
        from services.strategy_library.regime import RegimeRouter

        router = RegimeRouter()
        regime, weights = router.identify_regime(range_frame)

        # 区间可能识别为RANGE或UNCERTAIN
        assert regime in [MarketRegime.RANGE, MarketRegime.UNCERTAIN]


class TestStrategyFamilySelector:
    """测试策略族分类."""

    def test_classify_signals(self, long_signals):
        from services.strategy_library.family import StrategyFamily, StrategyFamilySelector
        from services.strategy_library.regime import RegimeWeight

        selector = StrategyFamilySelector()
        regime_weight = RegimeWeight(
            trend_pullback=1.0,
            breakout=1.0,
            range_mean_reversion=0.5,
            momentum_continuation=1.0,
        )

        weighted_signals = selector.classify_and_weight(long_signals, regime_weight)

        assert len(weighted_signals) == 3
        # ema_trend -> TREND_PULLBACK
        assert weighted_signals[0].family == StrategyFamily.TREND_PULLBACK
        # macd -> MOMENTUM_CONTINUATION
        assert weighted_signals[1].family == StrategyFamily.MOMENTUM_CONTINUATION
        # fvg -> BREAKOUT
        assert weighted_signals[2].family == StrategyFamily.BREAKOUT

        # 所有信号都是LONG，direction_score应该是1.0
        for ws in weighted_signals:
            assert ws.direction_score == 1.0


class TestWeightedEnsemble:
    """测试加权合成."""

    def test_aggregate_consistent_signals(self, long_signals):
        from services.strategy_library.ensemble.weighted import WeightedEnsembleService
        from services.strategy_library.family import StrategyFamilySelector
        from services.strategy_library.regime import RegimeWeight

        selector = StrategyFamilySelector()
        ensemble = WeightedEnsembleService()

        regime_weight = RegimeWeight(
            trend_pullback=1.0,
            breakout=1.0,
            range_mean_reversion=0.5,
            momentum_continuation=1.0,
        )

        weighted_signals = selector.classify_and_weight(long_signals, regime_weight)
        result = ensemble.aggregate(weighted_signals)

        assert result.final_side == TradeSide.LONG
        assert result.final_score > 0.35  # 超过多头阈值
        assert result.confidence > 0
        assert result.selected_family is not None

    def test_aggregate_mixed_signals(self, mixed_signals):
        from services.strategy_library.ensemble.weighted import WeightedEnsembleService
        from services.strategy_library.family import StrategyFamilySelector
        from services.strategy_library.regime import RegimeWeight

        selector = StrategyFamilySelector()
        ensemble = WeightedEnsembleService()

        regime_weight = RegimeWeight(
            trend_pullback=1.0,
            breakout=1.0,
            range_mean_reversion=0.3,  # 降低均值回归权重
            momentum_continuation=1.0,
        )

        weighted_signals = selector.classify_and_weight(mixed_signals, regime_weight)
        result = ensemble.aggregate(weighted_signals)

        # 混合信号可能有方向，也可能没有，取决于权重
        assert result.final_score is not None
        assert -1.0 <= result.final_score <= 1.0


class TestCPlusIntegration:
    """测试C+完整集成."""

    def test_full_pipeline_uptrend(self, uptrend_frame, long_signals):
        service = CPlusStrategyService()
        decision = service.make_decision(
            symbol="BTC/USDT",
            signals=long_signals,
            frame=uptrend_frame,
        )

        # 上升趋势 + 一致多头信号 = 应该生成多单
        assert decision.regime in [MarketRegime.TREND_UP, MarketRegime.UNCERTAIN]
        assert decision.ensemble.final_side in [TradeSide.LONG, None]

        if decision.rejection_reason is None:
            assert decision.entry_setup is not None
            assert decision.exit_plan is not None
            assert decision.exit_plan.initial_stop < decision.entry_setup.reference_price

    def test_rejection_no_signals(self, uptrend_frame):
        service = CPlusStrategyService()
        decision = service.make_decision(
            symbol="BTC/USDT",
            signals=[],
            frame=uptrend_frame,
        )

        assert decision.rejection_reason == "ensemble_no_direction"
        assert decision.entry_setup is None
        assert decision.exit_plan is None

    def test_decision_summary_formatting(self, uptrend_frame, long_signals):
        service = CPlusStrategyService()
        decision = service.make_decision(
            symbol="BTC/USDT",
            signals=long_signals,
            frame=uptrend_frame,
        )

        summary = service.format_decision_summary(decision)
        assert "BTC/USDT" in summary
        assert decision.decision_id[:8] in summary
