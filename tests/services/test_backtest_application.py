from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from shared.models import CarryBacktestRequest, MarketExtras, StrategyCreate, StrategyRules
from services.data.repository import DataRepository
from services.strategy_library import StrategyRepository, ValidationRepository
from services.validation.application import CarryBacktestApplicationService


def _seed_bar(symbol: str, at: datetime, close: str) -> dict:
    return {
        "symbol": symbol,
        "exchange": "binance",
        "timeframe": "1h",
        "time": at,
        "open": Decimal(close),
        "high": Decimal(close),
        "low": Decimal(close),
        "close": Decimal(close),
        "volume": Decimal("50"),
    }


def test_carry_backtest_application_uses_persisted_data(db_session) -> None:
    strategy_repo = StrategyRepository(db_session)
    validation_repo = ValidationRepository(db_session)
    data_repo = DataRepository(db_session)

    strategy = strategy_repo.create_strategy(
        StrategyCreate(
            strategy_key="carry_app_v1",
            source="manual",
            core_thesis="carry app flow",
            rules=StrategyRules(
                entry_rules={"funding_threshold_bps": 5},
                exit_rules={"hold_hours": 8},
                stoploss_rules={"basis_bps": 20},
                takeprofit_rules={"close_after_windows": 1},
                position_rules={"notional_usdt": 1000},
            ),
        )
    )
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    data_repo.store_ohlcv_bars(
        [
            _seed_bar("BTC/USDT", start, "42000"),
            _seed_bar("BTC/USDT", start + timedelta(hours=8), "42100"),
            _seed_bar("BTC/USDT", start + timedelta(hours=16), "42180"),
            _seed_bar("BTC/USDT:USDT", start, "42010"),
            _seed_bar("BTC/USDT:USDT", start + timedelta(hours=8), "41920"),
            _seed_bar("BTC/USDT:USDT", start + timedelta(hours=16), "41840"),
        ]
    )
    data_repo.store_market_extras(
        [
            MarketExtras(symbol="BTC/USDT:USDT", time=start, funding_rate=Decimal("0.0008")),
            MarketExtras(
                symbol="BTC/USDT:USDT",
                time=start + timedelta(hours=8),
                funding_rate=Decimal("0.0007"),
            ),
        ]
    )

    run = CarryBacktestApplicationService(
        strategy_repo=strategy_repo,
        validation_repo=validation_repo,
        data_repo=data_repo,
    ).submit(
        CarryBacktestRequest(
            strategy_id=strategy.strategy_id,
            spot_symbol="BTC/USDT",
            perp_symbol="BTC/USDT:USDT",
            timeframe="1h",
            start_at=start,
            end_at=start + timedelta(hours=16),
        )
    )

    assert run.backtest_run_id is not None
    assert run.eligibility_result is not None
    assert run.eligibility_result.decision_status == "conditional"
    assert run.validation_methodology["data_quality"]["gap_check"]["has_gaps"] is False
