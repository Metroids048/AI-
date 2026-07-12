from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from services.data import DataRepository
from services.execution.gatekeeper import ExecutionGatekeeperService
from services.execution.portfolio_risk import close_returns, correlation
from services.strategy_library import (
    ExecutionRepository,
    HypothesisRepository,
    PaperRunRepository,
    ReviewRepository,
    RiskProfileRepository,
    StrategyRepository,
    ValidationRepository,
)
from shared.models import (
    BacktestEngine,
    BacktestReport,
    BacktestRun,
    DecisionVetoResult,
    ExecutionOrderRequest,
    ExecutionRiskState,
    GateDecision,
    RiskEvent,
    RiskEventType,
    RiskSeverity,
    StrategyCreate,
    StrategyRules,
)


def _seed_gatekeeper_context(db_session) -> tuple[ExecutionGatekeeperService, str, str]:
    strategy = StrategyRepository(db_session).create_strategy(
        StrategyCreate(
            strategy_key="gatekeeper_test_strategy",
            source="manual",
            core_thesis="gatekeeper tests",
            rules=StrategyRules(
                stoploss_rules={"basis_bps": 20},
                takeprofit_rules={"basis_bps": 40},
                position_rules={"risk_per_trade": 0.01, "max_leverage": 2},
            ),
        )
    )
    backtest = ValidationRepository(db_session).create_backtest_run(
        BacktestRun(
            strategy_id=strategy.strategy_id,
            execution_engine="freqtrade",
            metrics_summary=BacktestReport(
                strategy_id=strategy.strategy_id,
                engine=BacktestEngine.FREQTRADE,
                sharpe=1.6,
                profit_factor=1.5,
                max_drawdown=0.1,
                win_rate=0.55,
                expectancy=0.09,
            ),
            eligibility_result=GateDecision(
                strategy_id=strategy.strategy_id,
                passed=True,
                decision_status="accepted",
                reason="validated",
            ),
        )
    )
    now = datetime.now(UTC).replace(microsecond=0)
    DataRepository(db_session).store_ohlcv_bars(
        [
            {
                "symbol": "BTC/USDT",
                "exchange": "binance",
                "timeframe": "1h",
                "time": now - timedelta(minutes=5),
                "open": Decimal("60000"),
                "high": Decimal("60100"),
                "low": Decimal("59900"),
                "close": Decimal("60050"),
                "volume": Decimal("12"),
            }
        ]
    )
    gatekeeper = ExecutionGatekeeperService(
        data_repo=DataRepository(db_session),
        validation_repo=ValidationRepository(db_session),
        hypothesis_repo=HypothesisRepository(db_session),
        risk_profile_repo=RiskProfileRepository(db_session),
        execution_repo=ExecutionRepository(db_session),
        paper_repo=PaperRunRepository(db_session),
        review_repo=ReviewRepository(db_session),
    )
    return gatekeeper, strategy.strategy_id, backtest.backtest_run_id


def _risk_state(**overrides) -> ExecutionRiskState:
    payload = {
        "account_equity": 10_000.0,
        "equity_peak": 10_000.0,
        "daily_realized_pnl": 0.0,
        "weekly_realized_pnl": 0.0,
        "consecutive_losses": 0,
        "api_failures_window": 0,
        "open_positions": 0,
        "symbol_exposure": 0.0,
        "total_exposure": 0.0,
        "requested_notional": 100.0,
        "requested_leverage": 1.0,
        "correlated_cluster_exposure": 0.0,
        "net_directional_exposure": 0.0,
        "portfolio_correlation_available": True,
    }
    payload.update(overrides)
    return ExecutionRiskState(**payload)


def _order_request(strategy_id: str, backtest_run_id: str, **overrides) -> ExecutionOrderRequest:
    payload = {
        "strategy_id": strategy_id,
        "symbol": "BTC/USDT",
        "direction": "long",
        "entry_context": {"timeframe": "1h"},
        "stoploss_plan": {"price": 59000},
        "takeprofit_plan": {"price": 62000},
        "validation_backtest_run_id": backtest_run_id,
        "risk_state": _risk_state(),
    }
    payload.update(overrides)
    return ExecutionOrderRequest(**payload)


def test_gatekeeper_accepts_healthy_order_and_persists_risk_snapshot(db_session) -> None:
    gatekeeper, strategy_id, backtest_run_id = _seed_gatekeeper_context(db_session)

    order = gatekeeper.submit_order(_order_request(strategy_id, backtest_run_id))

    assert order.execution_status == "accepted"
    assert order.rejection_codes == []
    assert order.evaluated_risk_state is not None
    assert order.evaluated_risk_state.account_equity == 10_000.0


@pytest.mark.parametrize(
    ("overrides", "expected_code"),
    [
        ({"risk_state": _risk_state(symbol_exposure=0.10, requested_notional=200.0)}, "max_symbol_exposure_exceeded"),
        ({"risk_state": _risk_state(total_exposure=0.50, requested_notional=100.0)}, "max_total_exposure_exceeded"),
        ({"risk_state": _risk_state(open_positions=3)}, "max_open_positions_exceeded"),
        ({"risk_state": _risk_state(requested_leverage=4.0)}, "max_leverage_exceeded"),
        ({"risk_state": _risk_state(daily_realized_pnl=-300.0)}, "daily_loss_limit_breached"),
        ({"risk_state": _risk_state(weekly_realized_pnl=-800.0)}, "weekly_loss_limit_breached"),
        ({"risk_state": _risk_state(account_equity=9_000.0, equity_peak=10_000.0)}, "drawdown_limit_breached"),
        ({"risk_state": _risk_state(account_equity=8_000.0, equity_peak=10_000.0)}, "hard_stop_drawdown_breached"),
        ({"risk_state": _risk_state(consecutive_losses=4)}, "consecutive_loss_limit_breached"),
        ({"risk_state": _risk_state(api_failures_window=3)}, "api_failure_limit_breached"),
        ({"risk_state": None}, "missing_risk_state"),
    ],
)
def test_gatekeeper_rejects_risk_limit_breaches(db_session, overrides: dict, expected_code: str) -> None:
    gatekeeper, strategy_id, backtest_run_id = _seed_gatekeeper_context(db_session)

    order = gatekeeper.submit_order(_order_request(strategy_id, backtest_run_id, **overrides))

    assert order.execution_status == "rejected"
    assert expected_code in order.rejection_codes
    assert ReviewRepository(db_session).list_failures()[-1].failure_type == "execution_gate_reject"


def test_gatekeeper_rejects_stale_data_blocking_event_and_veto(db_session) -> None:
    gatekeeper, strategy_id, backtest_run_id = _seed_gatekeeper_context(db_session)
    now = datetime.now(UTC).replace(microsecond=0)
    DataRepository(db_session).store_risk_event(
        RiskEvent(
            event_type=RiskEventType.EXCHANGE_INCIDENT,
            severity=RiskSeverity.HIGH,
            source="binance_status",
            description="exchange degraded",
            affected_scope=["ETH/USDT"],
            occurred_at=now,
            expires_at=now + timedelta(hours=1),
        )
    )
    DataRepository(db_session).store_ohlcv_bars(
        [
            {
                "symbol": "BTC/USDT",
                "exchange": "binance",
                "timeframe": "1h",
                "time": now - timedelta(hours=6),
                "open": Decimal("60000"),
                "high": Decimal("60100"),
                "low": Decimal("59900"),
                "close": Decimal("60050"),
                "volume": Decimal("12"),
            }
        ]
    )

    order = gatekeeper.submit_order(
        _order_request(
            strategy_id,
            backtest_run_id,
            symbol="ETH/USDT",
            veto_result=DecisionVetoResult(veto=True, veto_reason="manual veto"),
        )
    )

    assert order.execution_status == "rejected"
    assert "llm_veto" in order.rejection_codes
    assert "data_not_fresh" in order.rejection_codes
    assert "blocking_risk_event" in order.rejection_codes


def test_gatekeeper_rejection_appends_review_memory(db_session) -> None:
    gatekeeper, strategy_id, backtest_run_id = _seed_gatekeeper_context(db_session)

    gatekeeper.submit_order(
        _order_request(
            strategy_id,
            backtest_run_id,
            risk_state=_risk_state(symbol_exposure=0.10, requested_notional=500.0),
        )
    )

    strategy = StrategyRepository(db_session).get_strategy(strategy_id)
    failures = ReviewRepository(db_session).list_failures()
    assert strategy is not None
    assert failures
    assert any("max_symbol_exposure_exceeded" in reason for reason in strategy.failure_reasons)
    assert any(item["failure_summary"].startswith("Gatekeeper rejected") for item in strategy.iteration_history)


def test_gatekeeper_rejects_two_high_correlation_peers(db_session) -> None:
    gatekeeper, strategy_id, backtest_run_id = _seed_gatekeeper_context(db_session)

    order = gatekeeper.submit_order(
        _order_request(
            strategy_id,
            backtest_run_id,
            risk_state=_risk_state(
                high_correlation_peer_count=2,
                correlated_cluster_exposure=0.10,
                requested_notional=100.0,
            ),
        )
    )

    assert order.execution_status == "rejected"
    assert "correlated_exposure_limit_exceeded" in order.rejection_codes


def test_gatekeeper_rejects_correlated_cluster_and_net_directional_exposure(db_session) -> None:
    gatekeeper, strategy_id, backtest_run_id = _seed_gatekeeper_context(db_session)

    order = gatekeeper.submit_order(
        _order_request(
            strategy_id,
            backtest_run_id,
            risk_state=_risk_state(
                correlated_cluster_exposure=0.30,
                net_directional_exposure=0.36,
                requested_notional=1000.0,
            ),
        )
    )

    assert order.execution_status == "rejected"
    assert "correlated_cluster_exposure_exceeded" in order.rejection_codes
    assert "net_directional_exposure_exceeded" in order.rejection_codes


def test_gatekeeper_rejects_negative_net_edge_after_cost(db_session) -> None:
    gatekeeper, strategy_id, backtest_run_id = _seed_gatekeeper_context(db_session)

    rejected = gatekeeper.submit_order(
        _order_request(
            strategy_id,
            backtest_run_id,
            entry_context={
                "timeframe": "1h",
                "decision_pipeline": {"pipeline_status": "bet_taken"},
                "meta_label_win_rate": 0.40,
                "meta_label_average_win": 0.01,
                "meta_label_average_loss": 0.02,
                "round_trip_fee_rate": 0.002,
                "round_trip_slippage_rate": 0.001,
            },
        )
    )
    accepted = gatekeeper.submit_order(
        _order_request(
            strategy_id,
            backtest_run_id,
            entry_context={
                "timeframe": "1h",
                "decision_pipeline": {"pipeline_status": "bet_taken"},
                "meta_label_win_rate": 0.60,
                "meta_label_average_win": 0.03,
                "meta_label_average_loss": 0.01,
                "round_trip_fee_rate": 0.0016,
                "round_trip_slippage_rate": 0.0,
            },
        )
    )

    assert "net_edge_after_cost_negative" in rejected.rejection_codes
    assert rejected.entry_context["estimated_net_edge_after_cost"] < 0
    assert accepted.execution_status == "accepted"
    assert "net_edge_after_cost_negative" not in accepted.rejection_codes


def test_gatekeeper_rejects_missing_portfolio_correlation_for_new_order_but_not_close(db_session) -> None:
    gatekeeper, strategy_id, backtest_run_id = _seed_gatekeeper_context(db_session)
    unavailable = _risk_state(portfolio_correlation_available=False)

    opening = gatekeeper.submit_order(
        _order_request(strategy_id, backtest_run_id, risk_state=unavailable)
    )
    closing = gatekeeper.submit_order(
        _order_request(
            strategy_id,
            backtest_run_id,
            risk_state=unavailable,
            entry_context={"timeframe": "1h", "close_only_mode": True},
            stoploss_plan={},
        )
    )

    assert "portfolio_correlation_unavailable" in opening.rejection_codes
    assert "portfolio_correlation_unavailable" not in closing.rejection_codes


def test_portfolio_return_correlation_requires_full_60_bar_window() -> None:
    assert close_returns([100.0] * 60) is None
    left = [100.0 + index for index in range(61)]
    right = [200.0 + (index * 2) for index in range(61)]

    assert correlation(close_returns(left) or [], close_returns(right) or []) == pytest.approx(1.0)
