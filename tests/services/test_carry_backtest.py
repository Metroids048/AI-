from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from shared.models import OHLCVBar, StrategyContract, StrategyRules
from services.validation.carry import CarryBacktestService


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


def test_carry_backtest_builds_conditional_gate_when_deflated_sharpe_missing() -> None:
    service = CarryBacktestService()
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
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
    assert result.eligibility_result is not None
    assert result.eligibility_result.decision_status == "conditional"
    assert result.eligibility_result.reason is not None
