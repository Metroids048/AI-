"""Test delta-neutral carry strategy fix."""

from decimal import Decimal
from unittest.mock import Mock

import pytest

from services.data import DataRepository
from services.execution.paper_signal import PaperSignalGenerator
from shared.models import (
    OHLCVBar,
    PaperRun,
    PaperRunStepRequest,
    StrategyContract,
    StrategyRules,
    TradeSide,
)


@pytest.fixture
def mock_data_repo():
    """Mock data repository with funding rate data."""
    repo = Mock(spec=DataRepository)

    # Mock OHLCV bar
    bar = OHLCVBar(
        symbol="BTC/USDT",
        timeframe="1h",
        timestamp="2026-07-15T10:00:00Z",
        open=Decimal("50000"),
        high=Decimal("51000"),
        low=Decimal("49000"),
        close=Decimal("50500"),
        volume=Decimal("1000"),
    )
    repo.get_latest_ohlcv_bar = Mock(return_value=bar)

    return repo


@pytest.fixture
def carry_strategy():
    """Carry strategy with funding arbitrage rules."""
    return StrategyContract(
        strategy_id="test_carry",
        version_id="v1",
        source="test",
        core_thesis="funding arbitrage",
        market="crypto",
        timeframe="1h",
        rules=StrategyRules(
            entry_rules={
                "funding_threshold_bps": 0.5,
                "min_estimated_net_edge_bps": 5.0,
                "requires_positive_funding": True,
                "fee_bps": 5.0,
                "slippage_bps": 3.0,
            },
            exit_rules={},
            stoploss_rules={"fixed_bps": 250},
            takeprofit_rules={"risk_reward": 2.0},
            position_rules={"risk_per_trade": 0.01, "max_leverage": 10},
        ),
    )


@pytest.fixture
def paper_run():
    """Paper run configuration."""
    return PaperRun(
        paper_run_id="test_run",
        strategy_id="test_carry",
        version_id="v1",
        exchange="binance",
        symbol_scope=["BTC/USDT"],
        execution_profile={"strategy_lane": "carry"},
        gate_decision_ref="test_backtest",
        paper_status="running",
        paper_metrics_summary={"account_equity": 10000.0},
    )


def test_carry_decision_generates_hedge_leg(mock_data_repo, carry_strategy, paper_run):
    """Test that _carry_decision generates hedge_leg information for delta-neutral carry."""
    generator = PaperSignalGenerator(data_repo=mock_data_repo)

    # Mock MarketQueryService.get_funding_arbitrage_signal to return positive funding
    from services.data.market import MarketQueryService
    from shared.models import FundingArbitrageSignal

    mock_signal = FundingArbitrageSignal(
        symbol="BTC/USDT",
        perp_symbol="BTC/USDT:USDT",
        funding_rate=Decimal("0.0001"),  # 0.01% = 1 bps
        funding_bps=1.0,
        basis_bps=5.0,
        fee_bps=5.0,
        slippage_bps=3.0,
        round_trip_cost_bps=32.0,
        estimated_net_edge_bps=10.0,  # Positive edge
        should_enter_paper=True,
        rejection_reasons=[],
        recommended_strategy_template={},
    )

    mock_market_service = Mock(spec=MarketQueryService)
    mock_market_service.get_funding_arbitrage_signal = Mock(return_value=mock_signal)

    # Patch MarketQueryService in the method
    from unittest.mock import patch

    with patch("services.execution.paper_signal.MarketQueryService", return_value=mock_market_service):
        decision = generator._carry_decision(
            strategy=carry_strategy,
            symbol="BTC/USDT",
            timeframe="1h",
            request=PaperRunStepRequest(
                paper_run_id="test_run",
                perp_symbol="BTC/USDT:USDT",
            ),
            paper_run=paper_run,
        )

    # Verify hedge_leg is generated
    assert decision.trace is not None
    hedge_leg = decision.trace.get("hedge_leg")
    assert hedge_leg is not None, "hedge_leg should be generated for admitted carry orders"

    # Verify hedge_leg structure
    assert hedge_leg["symbol"] == "BTC/USDT", "hedge symbol should be the spot symbol"
    assert hedge_leg["is_spot"] is True, "hedge leg should be marked as spot"
    assert hedge_leg["order_type"] == "market", "hedge leg should use market orders"
    assert hedge_leg["reason"] == "delta_neutral_hedge_for_funding_carry"

    # Verify direction is opposite to the perp leg
    assert decision.direction == TradeSide.SHORT, "perp should be SHORT when funding is positive"
    assert hedge_leg["direction"] == str(TradeSide.LONG), "spot hedge should be LONG (opposite to SHORT perp)"


def test_carry_decision_no_hedge_when_rejected(mock_data_repo, carry_strategy, paper_run):
    """Test that hedge_leg is None when carry order is rejected."""
    generator = PaperSignalGenerator(data_repo=mock_data_repo)

    # Mock signal with rejection
    from services.data.market import MarketQueryService
    from shared.models import FundingArbitrageSignal

    mock_signal = FundingArbitrageSignal(
        symbol="BTC/USDT",
        perp_symbol="BTC/USDT:USDT",
        funding_rate=Decimal("0.00001"),  # Too low
        funding_bps=0.1,
        basis_bps=5.0,
        fee_bps=5.0,
        slippage_bps=3.0,
        round_trip_cost_bps=32.0,
        estimated_net_edge_bps=-5.0,  # Negative edge
        should_enter_paper=False,
        rejection_reasons=["below_funding_threshold", "below_min_estimated_net_edge"],
        recommended_strategy_template={},
    )

    mock_market_service = Mock(spec=MarketQueryService)
    mock_market_service.get_funding_arbitrage_signal = Mock(return_value=mock_signal)

    from unittest.mock import patch

    with patch("services.execution.paper_signal.MarketQueryService", return_value=mock_market_service):
        decision = generator._carry_decision(
            strategy=carry_strategy,
            symbol="BTC/USDT",
            timeframe="1h",
            request=PaperRunStepRequest(
                paper_run_id="test_run",
                perp_symbol="BTC/USDT:USDT",
            ),
            paper_run=paper_run,
        )

    # Verify no hedge_leg for rejected orders
    assert decision.should_trade is False
    assert decision.trace is not None
    hedge_leg = decision.trace.get("hedge_leg")
    assert hedge_leg is None, "hedge_leg should be None when order is rejected"


def test_negative_funding_hedge_direction(mock_data_repo, carry_strategy, paper_run):
    """Test hedge direction when funding is negative (requires_positive_funding=False)."""
    # Modify strategy to allow negative funding
    carry_strategy.rules.entry_rules["requires_positive_funding"] = False

    generator = PaperSignalGenerator(data_repo=mock_data_repo)

    # Mock signal with negative funding
    from services.data.market import MarketQueryService
    from shared.models import FundingArbitrageSignal

    mock_signal = FundingArbitrageSignal(
        symbol="BTC/USDT",
        perp_symbol="BTC/USDT:USDT",
        funding_rate=Decimal("-0.0001"),  # Negative funding
        funding_bps=-1.0,
        basis_bps=5.0,
        fee_bps=5.0,
        slippage_bps=3.0,
        round_trip_cost_bps=32.0,
        estimated_net_edge_bps=10.0,
        should_enter_paper=True,
        rejection_reasons=[],
        recommended_strategy_template={},
    )

    mock_market_service = Mock(spec=MarketQueryService)
    mock_market_service.get_funding_arbitrage_signal = Mock(return_value=mock_signal)

    from unittest.mock import patch

    with patch("services.execution.paper_signal.MarketQueryService", return_value=mock_market_service):
        decision = generator._carry_decision(
            strategy=carry_strategy,
            symbol="BTC/USDT",
            timeframe="1h",
            request=PaperRunStepRequest(
                paper_run_id="test_run",
                perp_symbol="BTC/USDT:USDT",
            ),
            paper_run=paper_run,
        )

    # Verify directions are opposite
    hedge_leg = decision.trace.get("hedge_leg")
    assert hedge_leg is not None
    assert decision.direction == TradeSide.LONG, "perp should be LONG when funding is negative"
    assert hedge_leg["direction"] == str(TradeSide.SHORT), "spot hedge should be SHORT (opposite to LONG perp)"
