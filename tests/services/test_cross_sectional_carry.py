from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from services.data import DataRepository
from services.execution.bootstrap import (
    AUTO_PAPER_CROSS_SECTIONAL_CARRY_KEY,
    AUTO_PAPER_CROSS_SECTIONAL_CARRY_RULES,
    bootstrap_cross_sectional_carry_strategy,
)
from services.execution.cross_sectional import compute_funding_rank_snapshot
from services.execution.gatekeeper import ExecutionGatekeeperService
from services.execution.paper_runtime import PaperRuntimeService
from services.execution.paper_signal import PaperSignalGenerator
from services.strategy_library import (
    AgentTaskRepository,
    ExecutionRepository,
    HypothesisRepository,
    NotificationRepository,
    PaperRunRepository,
    ReviewRepository,
    RiskProfileRepository,
    StrategyRepository,
    ValidationRepository,
)
from shared.models import (
    BacktestRun,
    GateDecision,
    OrderExecution,
    PaperRun,
    PaperRunStepRequest,
    PaperRuntimeCycleRequest,
    PositionSnapshot,
    RunStatus,
    StrategyCreate,
    TradeSide,
)


def _store_funding(db_session, *, symbol: str, funding_rate: Decimal) -> None:
    DataRepository(db_session).store_market_extras(
        [
            {
                "symbol": symbol,
                "time": datetime.now(UTC).replace(microsecond=0),
                "funding_rate": funding_rate,
            }
        ]
    )


def _store_bar(db_session, *, symbol: str, close: float, timeframe: str = "1h") -> None:
    DataRepository(db_session).store_ohlcv_bars(
        [
            {
                "symbol": symbol,
                "exchange": "binance",
                "timeframe": timeframe,
                "time": datetime.now(UTC).replace(microsecond=0),
                "open": Decimal(str(close)),
                "high": Decimal(str(close)),
                "low": Decimal(str(close)),
                "close": Decimal(str(close)),
                "volume": Decimal("10"),
            }
        ]
    )


class TestComputeFundingRankSnapshot:
    def test_ranks_by_funding_rate_and_assigns_basket_sides(self, db_session) -> None:
        rates = {
            "A/USDT": Decimal("0.0010"),
            "B/USDT": Decimal("0.0005"),
            "C/USDT": Decimal("0.0000"),
            "D/USDT": Decimal("-0.0005"),
            "E/USDT": Decimal("-0.0010"),
        }
        for symbol, rate in rates.items():
            _store_funding(db_session, symbol=symbol, funding_rate=rate)

        snapshot = compute_funding_rank_snapshot(
            data_repo=DataRepository(db_session),
            symbols=list(rates.keys()),
            basket_size=2,
        )

        assert snapshot["A/USDT"].basket_side == "short_candidate"
        assert snapshot["A/USDT"].rank == 1
        assert snapshot["B/USDT"].basket_side == "short_candidate"
        assert snapshot["B/USDT"].rank == 2
        assert snapshot["C/USDT"].basket_side is None
        assert snapshot["D/USDT"].basket_side == "long_candidate"
        assert snapshot["E/USDT"].basket_side == "long_candidate"
        assert snapshot["E/USDT"].rank == 5
        assert snapshot["A/USDT"].total_ranked == 5

    def test_excludes_symbols_missing_funding_data_fail_closed(self, db_session) -> None:
        _store_funding(db_session, symbol="A/USDT", funding_rate=Decimal("0.001"))
        # B/USDT has no market_extras row at all.

        snapshot = compute_funding_rank_snapshot(
            data_repo=DataRepository(db_session),
            symbols=["A/USDT", "B/USDT"],
            basket_size=1,
        )

        assert "A/USDT" in snapshot
        assert "B/USDT" not in snapshot

    def test_basket_size_spanning_the_whole_universe_puts_everyone_on_a_side(self, db_session) -> None:
        for symbol, rate in {"A/USDT": Decimal("0.001"), "B/USDT": Decimal("-0.001")}.items():
            _store_funding(db_session, symbol=symbol, funding_rate=rate)

        snapshot = compute_funding_rank_snapshot(
            data_repo=DataRepository(db_session),
            symbols=["A/USDT", "B/USDT"],
            basket_size=1,
        )

        assert snapshot["A/USDT"].basket_side == "short_candidate"
        assert snapshot["B/USDT"].basket_side == "long_candidate"


class TestCrossSectionalDecision:
    def _strategy(self, db_session):
        return StrategyRepository(db_session).create_strategy(
            StrategyCreate(
                strategy_key="test_cross_sectional",
                source="test",
                core_thesis="test cross sectional carry",
                rules=AUTO_PAPER_CROSS_SECTIONAL_CARRY_RULES,
            )
        )

    def test_generates_short_order_for_basket_short_candidate(self, db_session) -> None:
        strategy = self._strategy(db_session)
        _store_bar(db_session, symbol="A/USDT", close=100.0)
        generator = PaperSignalGenerator(data_repo=DataRepository(db_session))
        paper_run = PaperRun(
            strategy_id=strategy.strategy_id,
            execution_profile={"strategy_lane": "cross_sectional_carry", "account_equity": 10_000},
        )

        # Round-trip cost is 2*(fee_bps+slippage_bps)=16bps (one open + one close
        # taker fill); funding_rate_bps must clear that plus the 5bps min-edge
        # floor to admit the trade.
        order = generator.generate_order(
            paper_run=paper_run,
            strategy=strategy,
            request=PaperRunStepRequest(
                symbol="A/USDT",
                timeframe="1h",
                cross_sectional_rank={
                    "basket_side": "short_candidate",
                    "funding_rate_bps": 30.0,
                    "rank": 1,
                    "total_ranked": 20,
                },
            ),
            positions=[],
        )

        assert order.direction == TradeSide.SHORT
        assert order.entry_context["paper_order_should_trade"] is True
        decision_trace = order.entry_context["decision_pipeline"]
        assert decision_trace["strategy_lane"] == "cross_sectional_carry"
        assert decision_trace["basket_side"] == "short_candidate"

    def test_rejects_when_outside_basket(self, db_session) -> None:
        strategy = self._strategy(db_session)
        _store_bar(db_session, symbol="C/USDT", close=100.0)
        generator = PaperSignalGenerator(data_repo=DataRepository(db_session))
        paper_run = PaperRun(
            strategy_id=strategy.strategy_id,
            execution_profile={"strategy_lane": "cross_sectional_carry", "account_equity": 10_000},
        )

        order = generator.generate_order(
            paper_run=paper_run,
            strategy=strategy,
            request=PaperRunStepRequest(
                symbol="C/USDT",
                timeframe="1h",
                cross_sectional_rank={
                    "basket_side": None,
                    "funding_rate_bps": 0.5,
                    "rank": 10,
                    "total_ranked": 20,
                },
            ),
            positions=[],
        )

        assert order.entry_context["paper_order_should_trade"] is False
        decision_trace = order.entry_context["decision_pipeline"]
        assert "outside_funding_basket" in decision_trace["rejection_reasons"]

    def test_rejects_when_missing_rank_snapshot(self, db_session) -> None:
        strategy = self._strategy(db_session)
        _store_bar(db_session, symbol="A/USDT", close=100.0)
        generator = PaperSignalGenerator(data_repo=DataRepository(db_session))
        paper_run = PaperRun(
            strategy_id=strategy.strategy_id,
            execution_profile={"strategy_lane": "cross_sectional_carry", "account_equity": 10_000},
        )

        order = generator.generate_order(
            paper_run=paper_run,
            strategy=strategy,
            request=PaperRunStepRequest(symbol="A/USDT", timeframe="1h", cross_sectional_rank=None),
            positions=[],
        )

        assert order.entry_context["paper_order_should_trade"] is False
        decision_trace = order.entry_context["decision_pipeline"]
        assert "missing_funding_rank_snapshot" in decision_trace["rejection_reasons"]

    def test_rejects_when_funding_edge_below_cost_threshold(self, db_session) -> None:
        strategy = self._strategy(db_session)
        _store_bar(db_session, symbol="A/USDT", close=100.0)
        generator = PaperSignalGenerator(data_repo=DataRepository(db_session))
        paper_run = PaperRun(
            strategy_id=strategy.strategy_id,
            execution_profile={"strategy_lane": "cross_sectional_carry", "account_equity": 10_000},
        )

        # Funding rate 1bps vs round-trip cost 2*(5+3)=16bps -> deeply negative edge.
        order = generator.generate_order(
            paper_run=paper_run,
            strategy=strategy,
            request=PaperRunStepRequest(
                symbol="A/USDT",
                timeframe="1h",
                cross_sectional_rank={
                    "basket_side": "short_candidate",
                    "funding_rate_bps": 1.0,
                    "rank": 1,
                    "total_ranked": 20,
                },
            ),
            positions=[],
        )

        assert order.entry_context["paper_order_should_trade"] is False
        decision_trace = order.entry_context["decision_pipeline"]
        assert "net_edge_after_cost_negative" in decision_trace["rejection_reasons"]


class TestRankDropoutClose:
    def test_run_cycle_closes_position_on_rank_dropout(self, db_session) -> None:
        strategy = StrategyRepository(db_session).create_strategy(
            StrategyCreate(
                strategy_key="test_cross_sectional_dropout",
                source="test",
                core_thesis="test rank dropout close",
                rules=AUTO_PAPER_CROSS_SECTIONAL_CARRY_RULES,
            )
        )
        backtest = ValidationRepository(db_session).create_backtest_run(
            BacktestRun(
                strategy_id=strategy.strategy_id,
                execution_engine="test",
                eligibility_result=GateDecision(
                    strategy_id=strategy.strategy_id,
                    passed=True,
                    decision_status="accepted",
                    reason="test",
                ),
            )
        )
        # 7 symbols with basket_size=3 (from AUTO_PAPER_CROSS_SECTIONAL_CARRY_RULES)
        # puts the middle symbol (rank 4 of 7) outside both the top-3 short side
        # and bottom-3 long side -- i.e. genuinely dropped out of the basket, not
        # just missing data.
        candidate_symbols = ["A/USDT", "B/USDT", "C/USDT", "D/USDT", "E/USDT", "F/USDT", "G/USDT"]
        paper_run = PaperRunRepository(db_session).create_paper_run(
            PaperRun(
                strategy_id=strategy.strategy_id,
                gate_decision_ref=backtest.backtest_run_id,
                candidate_symbols=candidate_symbols,
                execution_profile={
                    "strategy_lane": "cross_sectional_carry",
                    "account_equity": 10_000,
                    "equity_peak": 10_000,
                    "max_symbols": len(candidate_symbols),
                },
                paper_status="running",
            )
        )
        funding_rates = [
            ("A/USDT", Decimal("0.0030")),
            ("B/USDT", Decimal("0.0020")),
            ("C/USDT", Decimal("0.0010")),
            ("D/USDT", Decimal("0.0000")),  # rank 4 of 7 -> outside a basket_size=3 basket
            ("E/USDT", Decimal("-0.0010")),
            ("F/USDT", Decimal("-0.0020")),
            ("G/USDT", Decimal("-0.0030")),
        ]
        for symbol, rate in funding_rates:
            _store_funding(db_session, symbol=symbol, funding_rate=rate)
            _store_bar(db_session, symbol=symbol, close=100.0, timeframe="1h")
            _store_bar(db_session, symbol=symbol, close=100.0, timeframe="1m")
        execution_repo = ExecutionRepository(db_session)
        # D/USDT was originally opened while it ranked inside the basket; funding
        # has since normalized to rank 4 of 7, outside a basket_size=3 basket.
        execution_repo.create_order(
            OrderExecution(
                strategy_id=strategy.strategy_id,
                symbol="D/USDT",
                direction=TradeSide.SHORT,
                execution_status="filled",
                stoploss_present=True,
                close_only_mode=False,
                entry_context={"reference_price": "100", "requested_notional": 100, "quantity": 1, "timeframe": "1h"},
                stoploss_plan={"price": 400.0},
                takeprofit_plan={},
                validation_backtest_run_id=backtest.backtest_run_id,
                paper_run_id=paper_run.paper_run_id,
            )
        )
        execution_repo.create_position_snapshot(
            PositionSnapshot(
                run_type="paper",
                run_id=paper_run.paper_run_id or "",
                symbol="D/USDT",
                side=TradeSide.SHORT,
                quantity=-1.0,
                entry_price=100.0,
                mark_price=100.0,
                unrealized_pnl=0.0,
                snapshot_time=datetime.now(UTC) - timedelta(minutes=5),
            )
        )

        runtime = PaperRuntimeService(
            data_repo=DataRepository(db_session),
            execution_repo=execution_repo,
            paper_repo=PaperRunRepository(db_session),
            strategy_repo=StrategyRepository(db_session),
            agent_repo=AgentTaskRepository(db_session),
            review_repo=ReviewRepository(db_session),
            notification_repo=NotificationRepository(db_session),
            gatekeeper=ExecutionGatekeeperService(
                data_repo=DataRepository(db_session),
                validation_repo=ValidationRepository(db_session),
                hypothesis_repo=HypothesisRepository(db_session),
                risk_profile_repo=RiskProfileRepository(db_session),
                execution_repo=execution_repo,
                paper_repo=PaperRunRepository(db_session),
                review_repo=ReviewRepository(db_session),
            ),
        )

        result = runtime.run_cycle(
            paper_run_id=paper_run.paper_run_id or "",
            request=PaperRuntimeCycleRequest(symbols=candidate_symbols, timeframe="1h", enable_decision_veto=False),
        )

        assert result.closed_positions == 1
        assert "D/USDT" not in result.open_position_symbols
        dropout_action = next(action for action in result.actions if action.symbol == "D/USDT")
        assert dropout_action.action == "close_short"


class TestBootstrapRegistration:
    def test_registers_disabled_research_strategy(self, db_session) -> None:
        strategy_id = bootstrap_cross_sectional_carry_strategy()

        strategy = StrategyRepository(db_session).get_strategy(strategy_id or "")
        assert strategy is not None
        assert strategy.strategy_key == AUTO_PAPER_CROSS_SECTIONAL_CARRY_KEY
        assert strategy.paper_status is RunStatus.NOT_STARTED

    def test_idempotent_on_second_call(self, db_session) -> None:
        first_id = bootstrap_cross_sectional_carry_strategy()
        second_id = bootstrap_cross_sectional_carry_strategy()

        assert first_id == second_id
        assert len(StrategyRepository(db_session).list_strategies()) == 1
