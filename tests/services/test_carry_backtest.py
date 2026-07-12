from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from services.validation.carry import CarryBacktestService
from shared.models import OHLCVBar, StrategyContract, StrategyRules


def _bar(symbol: str, at: datetime, close: str) -> OHLCVBar:
    return OHLCVBar(
        symbol=symbol,
        exchange="binance",
        timeframe="1h",
        time=at,
        open=Decimal(close),
        high=Decimal(close),
        low=Decimal(close),
        close=Decimal(close),
        volume=Decimal("100"),
    )


def test_carry_backtest_rejects_when_real_net_metrics_fail_thresholds() -> None:
    service = CarryBacktestService()
    start = datetime(2024, 1, 1, tzinfo=UTC)
    strategy = StrategyContract(
        strategy_id="strategy-1",
        strategy_key="carry_v1",
        source="manual",
        core_thesis="settlement carry",
        rules=StrategyRules(
            entry_rules={"funding_threshold_bps": 5},
            exit_rules={"hold_hours": 8},
            stoploss_rules={"basis_bps": 20},
            takeprofit_rules={"close_after_windows": 1},
            position_rules={"notional_usdt": 1000},
        ),
    )
    spot_bars = [
        _bar("BTC/USDT", start, "42000"),
        _bar("BTC/USDT", start + timedelta(hours=8), "42100"),
        _bar("BTC/USDT", start + timedelta(hours=16), "42180"),
    ]
    perp_bars = [
        _bar("BTC/USDT:USDT", start, "42010"),
        _bar("BTC/USDT:USDT", start + timedelta(hours=8), "41920"),
        _bar("BTC/USDT:USDT", start + timedelta(hours=16), "41840"),
    ]
    funding = [
        {"time": start, "funding_rate": Decimal("0.0008")},
        {"time": start + timedelta(hours=8), "funding_rate": Decimal("0.0007")},
    ]

    result = service.run_backtest(
        strategy=strategy,
        spot_bars=spot_bars,
        perp_bars=perp_bars,
        funding_points=funding,
    )

    assert result.metrics_summary is not None
    assert result.metrics_summary.total_trades >= 1
    assert result.metrics_summary.expectancy < 0
    assert result.eligibility_result is not None
    assert result.eligibility_result.decision_status == "rejected_with_reason"
    assert "min_expectancy" in result.eligibility_result.failed_thresholds


def test_carry_metrics_change_with_trade_distribution() -> None:
    service = CarryBacktestService()
    start = datetime(2024, 1, 1, tzinfo=UTC)
    strategy = StrategyContract(
        strategy_id="strategy-2",
        strategy_key="carry_v2",
        source="manual",
        core_thesis="settlement carry",
        rules=StrategyRules(
            entry_rules={"funding_threshold_bps": 1},
            exit_rules={"hold_hours": 8},
            stoploss_rules={"basis_bps": 20},
            takeprofit_rules={"close_after_windows": 1},
            position_rules={"notional_usdt": 1000, "trials_count": 3},
        ),
    )
    funding = [
        {"time": start, "funding_rate": Decimal("0.0010")},
        {"time": start + timedelta(hours=8), "funding_rate": Decimal("0.0010")},
    ]
    smooth = service.run_backtest(
        strategy=strategy,
        spot_bars=[
            _bar("BTC/USDT", start, "100"),
            _bar("BTC/USDT", start + timedelta(hours=8), "101"),
            _bar("BTC/USDT", start + timedelta(hours=16), "102"),
        ],
        perp_bars=[
            _bar("BTC/USDT:USDT", start, "100"),
            _bar("BTC/USDT:USDT", start + timedelta(hours=8), "100"),
            _bar("BTC/USDT:USDT", start + timedelta(hours=16), "100"),
        ],
        funding_points=funding,
    )
    choppy = service.run_backtest(
        strategy=strategy,
        spot_bars=[
            _bar("BTC/USDT", start, "100"),
            _bar("BTC/USDT", start + timedelta(hours=8), "102"),
            _bar("BTC/USDT", start + timedelta(hours=16), "95"),
        ],
        perp_bars=[
            _bar("BTC/USDT:USDT", start, "100"),
            _bar("BTC/USDT:USDT", start + timedelta(hours=8), "100"),
            _bar("BTC/USDT:USDT", start + timedelta(hours=16), "103"),
        ],
        funding_points=funding,
    )

    assert smooth.metrics_summary is not None
    assert choppy.metrics_summary is not None
    assert smooth.metrics_summary.sharpe != choppy.metrics_summary.sharpe
    assert smooth.metrics_summary.max_drawdown != choppy.metrics_summary.max_drawdown
    assert smooth.metrics_summary.cost_breakdown_bps is not None
    assert "slippage_bps" in smooth.metrics_summary.cost_breakdown_bps


def test_deflated_sharpe_is_more_conservative_with_more_trials() -> None:
    service = CarryBacktestService()
    start = datetime(2024, 1, 1, tzinfo=UTC)

    def _strategy(trials_count: int) -> StrategyContract:
        return StrategyContract(
            strategy_id=f"strategy-trials-{trials_count}",
            strategy_key=f"carry_trials_{trials_count}",
            source="manual",
            core_thesis="trial penalty",
            rules=StrategyRules(
                entry_rules={"funding_threshold_bps": 1},
                exit_rules={"hold_hours": 8},
                position_rules={"notional_usdt": 1000, "trials_count": trials_count},
            ),
        )

    bars = [
        _bar("BTC/USDT", start, "100"),
        _bar("BTC/USDT", start + timedelta(hours=8), "101"),
        _bar("BTC/USDT", start + timedelta(hours=16), "103"),
    ]
    perp = [
        _bar("BTC/USDT:USDT", start, "100"),
        _bar("BTC/USDT:USDT", start + timedelta(hours=8), "100"),
        _bar("BTC/USDT:USDT", start + timedelta(hours=16), "101"),
    ]
    funding = [
        {"time": start, "funding_rate": Decimal("0.0010")},
        {"time": start + timedelta(hours=8), "funding_rate": Decimal("0.0012")},
    ]
    low_trials = service.run_backtest(strategy=_strategy(1), spot_bars=bars, perp_bars=perp, funding_points=funding)
    high_trials = service.run_backtest(strategy=_strategy(100), spot_bars=bars, perp_bars=perp, funding_points=funding)

    assert low_trials.metrics_summary is not None
    assert high_trials.metrics_summary is not None
    assert low_trials.metrics_summary.deflated_sharpe is not None
    assert high_trials.metrics_summary.deflated_sharpe is not None
    assert high_trials.metrics_summary.deflated_sharpe < low_trials.metrics_summary.deflated_sharpe


def test_carry_backtest_is_reproducible_for_identical_snapshot_inputs() -> None:
    service = CarryBacktestService()
    start = datetime(2024, 1, 1, tzinfo=UTC)
    strategy = StrategyContract(
        strategy_id="strategy-reproducible",
        strategy_key="carry_reproducible",
        source="manual",
        core_thesis="identical snapshots must produce identical validation evidence",
        rules=StrategyRules(
            entry_rules={"funding_threshold_bps": 1},
            exit_rules={"hold_hours": 8},
            position_rules={"notional_usdt": 1000, "trials_count": 5},
        ),
    )
    spot = [
        _bar("BTC/USDT", start, "100"),
        _bar("BTC/USDT", start + timedelta(hours=8), "101"),
        _bar("BTC/USDT", start + timedelta(hours=16), "102"),
    ]
    perp = [
        _bar("BTC/USDT:USDT", start, "100"),
        _bar("BTC/USDT:USDT", start + timedelta(hours=8), "100"),
        _bar("BTC/USDT:USDT", start + timedelta(hours=16), "101"),
    ]
    funding = [
        {"time": start, "funding_rate": Decimal("0.0010")},
        {"time": start + timedelta(hours=8), "funding_rate": Decimal("0.0012")},
    ]

    first = service.run_backtest(strategy=strategy, spot_bars=spot, perp_bars=perp, funding_points=funding)
    second = service.run_backtest(strategy=strategy, spot_bars=spot, perp_bars=perp, funding_points=funding)

    assert first.metrics_summary == second.metrics_summary
    assert first.eligibility_result == second.eligibility_result
