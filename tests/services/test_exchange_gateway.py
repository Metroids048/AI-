from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from services.data import DataRepository
from services.execution.live import LiveExecutionService
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
    BacktestReport,
    BacktestRun,
    BacktestEngine,
    ExchangeAccountSnapshot,
    ExchangeGatewayCapability,
    ExecutionOrderRequest,
    ExecutionRiskState,
    GateDecision,
    HypothesisRecord,
    LiveRun,
    RiskProfile,
    StrategyCreate,
    StrategyRules,
    TradeSide,
)


class StubBinanceGateway:
    capability = ExchangeGatewayCapability(
        gateway_name="binance_usdt_perpetual",
        exchange="binance",
        market_type="usdt_perpetual",
        supports_account_sync=True,
        supports_positions_sync=True,
        supports_order_submit=True,
        supports_order_cancel=True,
        supports_reconciliation=True,
    )

    def sync_account(self, *, live_run_id: str) -> ExchangeAccountSnapshot:
        return ExchangeAccountSnapshot(
            snapshot_id="acct-snap-1",
            live_run_id=live_run_id,
            exchange="binance",
            wallet_balance=1200.0,
            available_balance=1000.0,
            margin_balance=1180.0,
            unrealized_pnl=12.0,
            open_position_count=0,
        )

    def submit_order(self, *, live_run_id: str, order_request: ExecutionOrderRequest) -> dict:
        return {
            "gateway_order_id": "binance-order-1",
            "gateway_status": "acknowledged",
            "symbol": order_request.symbol,
        }

    def cancel_order(self, *, gateway_order_id: str) -> dict:
        return {"gateway_order_id": gateway_order_id, "gateway_status": "cancelled"}

    def reconcile(self, *, live_run_id: str) -> dict:
        return {
            "live_run_id": live_run_id,
            "reconciliation_status": "ok",
            "open_order_count": 0,
            "position_mismatches": [],
        }


def _seed_context(db_session) -> tuple[str, str, str]:
    strategy = StrategyRepository(db_session).create_strategy(
        StrategyCreate(
            strategy_key="live_gateway_strategy",
            source="manual",
            core_thesis="gateway flow should preserve gatekeeper admission",
            rules=StrategyRules(
                stoploss_rules={"price": 59000},
                takeprofit_rules={"price": 62000},
                position_rules={"risk_per_trade": 0.01},
            ),
        )
    )
    hypothesis = HypothesisRepository(db_session).create_hypothesis(
        HypothesisRecord(
            strategy_id=strategy.strategy_id,
            title="Live gateway admission hypothesis",
            statement="Validated carry logic should survive benchmark/OOS/pod risk gates before live submission.",
            benchmark_plan={"benchmarks": ["passive_hold", "randomized_control"]},
            acceptance_criteria={"min_deflated_sharpe": 1.0},
            status="approved",
        )
    )
    backtest = ValidationRepository(db_session).create_backtest_run(
        BacktestRun(
            strategy_id=strategy.strategy_id,
            execution_engine="freqtrade",
            validation_methodology={
                "hypothesis_id": hypothesis.hypothesis_id,
                "benchmark_passed": True,
                "oos_passed": True,
                "pod_risk_passed": True,
            },
            metrics_summary=BacktestReport(
                strategy_id=strategy.strategy_id,
                engine=BacktestEngine.FREQTRADE,
                sharpe=1.8,
                deflated_sharpe=1.4,
                profit_factor=1.6,
                max_drawdown=0.09,
                win_rate=0.56,
                expectancy=0.11,
                validation_windows=[{"window_id": "oos-1", "passed": True, "sharpe": 1.2, "expectancy": 0.03}],
            ),
            eligibility_result=GateDecision(
                strategy_id=strategy.strategy_id,
                passed=True,
                decision_status="accepted",
                reason="fully admitted",
                failed_thresholds=[],
            ),
        )
    )
    live_run = ExecutionRepository(db_session).create_live_run(
        LiveRun(
            strategy_id=strategy.strategy_id,
            live_status="queued",
            risk_profile_ref=RiskProfileRepository(db_session).create_profile(RiskProfile()).risk_profile_id,
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
                "volume": Decimal("10"),
            }
        ]
    )
    return strategy.strategy_id, backtest.backtest_run_id, live_run.live_run_id


def test_live_execution_service_runs_account_order_cancel_and_reconcile(db_session) -> None:
    strategy_id, backtest_run_id, live_run_id = _seed_context(db_session)
    service = LiveExecutionService(
        data_repo=DataRepository(db_session),
        validation_repo=ValidationRepository(db_session),
        risk_profile_repo=RiskProfileRepository(db_session),
        execution_repo=ExecutionRepository(db_session),
        paper_repo=PaperRunRepository(db_session),
        review_repo=ReviewRepository(db_session),
        gateway=StubBinanceGateway(),
    )

    snapshot = service.sync_account(live_run_id=live_run_id)
    submitted = service.submit_live_order(
        live_run_id=live_run_id,
        order_request=ExecutionOrderRequest(
            strategy_id=strategy_id,
            symbol="BTC/USDT",
            direction=TradeSide.LONG,
            entry_context={"timeframe": "1h"},
            stoploss_plan={"price": 59000},
            takeprofit_plan={"price": 62000},
            validation_backtest_run_id=backtest_run_id,
            risk_state=ExecutionRiskState(
                account_equity=10_000.0,
                equity_peak=10_000.0,
                requested_notional=100.0,
                requested_leverage=1.0,
            ),
        ),
    )
    cancelled = service.cancel_live_order(live_run_id=live_run_id, order_execution_id=submitted.order_execution_id)
    reconciliation = service.reconcile_live_run(live_run_id=live_run_id)

    assert snapshot.wallet_balance == 1200.0
    assert submitted.execution_status == "accepted"
    assert submitted.gateway_order_id == "binance-order-1"
    assert cancelled.gateway_status == "cancelled"
    assert reconciliation.reconciliation_status == "ok"
