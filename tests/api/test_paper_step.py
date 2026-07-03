from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from services.data import DataRepository
from services.strategy_library import ValidationRepository
from shared.models import BacktestReport, BacktestRun, GateDecision


def _create_validated_strategy_and_paper_run(api_client, db_session) -> tuple[str, str]:
    strategy_resp = api_client.post(
        "/api/v1/strategies",
        json={
            "strategy_key": "paper_step_trend",
            "source": "open_source:freqtrade",
            "core_thesis": "open-source seeded trend following",
            "symbol_scope": ["BTC/USDT"],
            "rules": {
                "entry_rules": {"ema_fast": 20, "ema_slow": 50, "macd_confirmation": True},
                "exit_rules": {"max_hold_bars": 48},
                "stoploss_rules": {"atr_multiple": 2},
                "takeprofit_rules": {"risk_reward": 2},
                "position_rules": {"risk_per_trade": 0.01},
            },
        },
    )
    assert strategy_resp.status_code == 201
    strategy_id = strategy_resp.json()["strategy_id"]
    backtest = ValidationRepository(db_session).create_backtest_run(
        BacktestRun(
            strategy_id=strategy_id,
            execution_engine="freqtrade",
            metrics_summary=BacktestReport(
                strategy_id=strategy_id,
                engine="freqtrade",
                sharpe=1.4,
                profit_factor=1.4,
                max_drawdown=0.1,
                win_rate=0.55,
                expectancy=0.08,
            ),
            eligibility_result=GateDecision(
                strategy_id=strategy_id,
                passed=True,
                decision_status="accepted",
                reason="validated for paper",
            ),
        )
    )
    paper_resp = api_client.post(
        "/api/v1/execution/paper-runs",
        json={"strategy_id": strategy_id, "gate_decision_ref": backtest.backtest_run_id},
    )
    assert paper_resp.status_code == 202
    return strategy_id, paper_resp.json()["resource_id"]


def test_paper_run_step_generates_order_through_gatekeeper(api_client, db_session) -> None:
    _, paper_run_id = _create_validated_strategy_and_paper_run(api_client, db_session)
    now = datetime.now(UTC).replace(microsecond=0)
    DataRepository(db_session).store_ohlcv_bars(
        [
            {
                "symbol": "BTC/USDT",
                "exchange": "binance",
                "timeframe": "1h",
                "time": now - timedelta(minutes=40),
                "open": Decimal("60000"),
                "high": Decimal("60300"),
                "low": Decimal("59900"),
                "close": Decimal("60200"),
                "volume": Decimal("10"),
            },
            {
                "symbol": "BTC/USDT",
                "exchange": "binance",
                "timeframe": "1h",
                "time": now - timedelta(minutes=5),
                "open": Decimal("60200"),
                "high": Decimal("60500"),
                "low": Decimal("60100"),
                "close": Decimal("60400"),
                "volume": Decimal("12"),
            },
        ]
    )

    step_resp = api_client.post(
        f"/api/v1/execution/paper-runs/{paper_run_id}/step",
        json={"symbol": "BTC/USDT", "timeframe": "1h"},
    )

    assert step_resp.status_code == 201
    body = step_resp.json()
    assert body["execution_status"] == "accepted"
    assert body["paper_run_id"] == paper_run_id
    assert body["stoploss_present"] is True


def test_paper_run_step_rejects_when_market_data_is_stale(api_client, db_session) -> None:
    _, paper_run_id = _create_validated_strategy_and_paper_run(api_client, db_session)
    old = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=6)
    DataRepository(db_session).store_ohlcv_bars(
        [
            {
                "symbol": "BTC/USDT",
                "exchange": "binance",
                "timeframe": "1h",
                "time": old,
                "open": Decimal("60000"),
                "high": Decimal("60100"),
                "low": Decimal("59900"),
                "close": Decimal("60050"),
                "volume": Decimal("10"),
            }
        ]
    )

    step_resp = api_client.post(
        f"/api/v1/execution/paper-runs/{paper_run_id}/step",
        json={"symbol": "BTC/USDT", "timeframe": "1h"},
    )

    assert step_resp.status_code == 201
    body = step_resp.json()
    assert body["execution_status"] == "rejected"
    assert "data_not_fresh" in body["rejection_reason"]
