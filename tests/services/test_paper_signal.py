from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from services.data import DataRepository
from services.execution.bootstrap import LINK_VERIFICATION_RULES
from services.execution.net_edge import net_edge_rejection_codes
from services.execution.paper_signal import PaperSignalGenerator
from shared.models import (
    PaperRun,
    PaperRunStepRequest,
    PositionSnapshot,
    StrategyContract,
    StrategyRules,
    TradeSide,
)


def _strategy(*, position_rules: dict) -> StrategyContract:
    return StrategyContract(
        strategy_id="strategy-1",
        strategy_key="test_paper_signal_strategy",
        source="test",
        core_thesis="test",
        rules=StrategyRules(position_rules=position_rules),
    )


def _paper_run(*, execution_profile: dict | None = None) -> PaperRun:
    return PaperRun(
        strategy_id="strategy-1",
        execution_profile=execution_profile or {"account_equity": 10_000},
    )


class TestSizingSentinelLogging:
    def test_execution_profile_overrides_stale_strategy_risk_rules(self, db_session) -> None:
        strategy = _strategy(position_rules={"risk_per_trade": 0.01, "max_position_fraction": 0.12})
        paper_run = _paper_run(
            execution_profile={
                "account_equity": 10_000,
                "risk_per_trade": 0.05,
                "max_leverage": 40,
                "max_symbol_exposure": 0.35,
                "asset_risk_tiers": {
                    "core": {
                        "tier": "core",
                        "symbols": ["BTC/USDT", "ETH/USDT"],
                        "leverage": 40,
                        "max_position_fraction": 0.35,
                    }
                },
            }
        )
        generator = PaperSignalGenerator(data_repo=DataRepository(db_session))

        assert generator._requested_leverage(strategy=strategy, paper_run=paper_run, symbol="BTC/USDT") == 40
        assert generator._requested_notional(
            strategy=strategy,
            paper_run=paper_run,
            symbol="BTC/USDT",
            requested_leverage=40,
            reference_price=Decimal("100"),
            stoploss_price=Decimal("80"),
        ) == pytest.approx(2_500.0)

    def test_logs_warning_when_notional_falls_below_sane_fraction(self, caplog, db_session) -> None:
        strategy = _strategy(position_rules={"notional_usdt": 10.0})
        paper_run = _paper_run()
        generator = PaperSignalGenerator(data_repo=DataRepository(db_session))

        with caplog.at_level(logging.WARNING, logger="services.execution.paper_signal"):
            notional = generator._requested_notional(
                strategy=strategy,
                paper_run=paper_run,
                symbol="BTC/USDT",
                requested_leverage=1.0,
            )

        assert notional == 10.0
        assert any("sizing_sentinel_triggered" in record.message for record in caplog.records)

    def test_no_warning_when_notional_is_sane(self, caplog, db_session) -> None:
        strategy = _strategy(position_rules={"notional_usdt": 500.0})
        paper_run = _paper_run()
        generator = PaperSignalGenerator(data_repo=DataRepository(db_session))

        with caplog.at_level(logging.WARNING, logger="services.execution.paper_signal"):
            notional = generator._requested_notional(
                strategy=strategy,
                paper_run=paper_run,
                symbol="BTC/USDT",
                requested_leverage=1.0,
            )

        assert notional == 500.0
        assert not any("sizing_sentinel_triggered" in record.message for record in caplog.records)

    def test_risk_per_trade_leverage_sized_path_still_logs_when_tiny(self, caplog, db_session) -> None:
        # No reference_price/stoploss_price -> falls through to the
        # leverage-sized branch, which can legitimately collapse to near-zero
        # when risk_per_trade and leverage are both small.
        strategy = _strategy(position_rules={"risk_per_trade": 0.0001, "max_position_fraction": 0.2})
        paper_run = _paper_run()
        generator = PaperSignalGenerator(data_repo=DataRepository(db_session))

        with caplog.at_level(logging.WARNING, logger="services.execution.paper_signal"):
            notional = generator._requested_notional(
                strategy=strategy,
                paper_run=paper_run,
                symbol="BTC/USDT",
                requested_leverage=1.0,
            )

        assert notional == pytest.approx(1.0)
        assert any("sizing_sentinel_triggered" in record.message for record in caplog.records)

    def test_leverage_sized_path_is_capped_to_max_position_fraction(self, db_session) -> None:
        # Historical bug: risk_per_trade * leverage could request 80% equity and
        # gatekeeper then rejected every open as max_symbol_exposure_exceeded.
        strategy = _strategy(position_rules={"risk_per_trade": 0.04, "max_position_fraction": 0.2})
        paper_run = _paper_run(execution_profile={"account_equity": 10_000, "max_symbol_exposure": 0.2})
        generator = PaperSignalGenerator(data_repo=DataRepository(db_session))

        notional = generator._requested_notional(
            strategy=strategy,
            paper_run=paper_run,
            symbol="ARB/USDT",
            requested_leverage=20.0,
        )

        assert notional == pytest.approx(2_000.0)


def _store_bars(db_session, *, symbol: str, base_price: float, step: float) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    DataRepository(db_session).store_ohlcv_bars(
        [
            {
                "symbol": symbol,
                "exchange": "binance",
                "timeframe": "1h",
                "time": now - timedelta(hours=61 - index),
                "open": Decimal(str(base_price + index * step)),
                "high": Decimal(str(base_price + index * step)),
                "low": Decimal(str(base_price + index * step)),
                "close": Decimal(str(base_price + index * step)),
                "volume": Decimal("10"),
            }
            for index in range(61)
        ]
    )


def _position(*, symbol: str, side: TradeSide) -> PositionSnapshot:
    return PositionSnapshot(
        run_type="paper",
        run_id="run-1",
        symbol=symbol,
        side=side,
        quantity=1.0 if side == TradeSide.LONG else -1.0,
        entry_price=100.0,
        mark_price=100.0,
        unrealized_pnl=0.0,
        snapshot_time=datetime.now(UTC),
    )


class TestCorrelationThreshold:
    def test_default_threshold_applies_discount_and_logs(self, db_session, caplog) -> None:
        _store_bars(db_session, symbol="A/USDT", base_price=100.0, step=1.0)
        _store_bars(db_session, symbol="B/USDT", base_price=100.0, step=1.0)
        generator = PaperSignalGenerator(data_repo=DataRepository(db_session))
        strategy = _strategy(position_rules={"risk_per_trade": 0.01})
        paper_run = _paper_run(execution_profile={"account_equity": 10_000})

        with caplog.at_level(logging.WARNING, logger="services.execution.paper_signal"):
            risk_state = generator._build_risk_state(
                paper_run=paper_run,
                strategy=strategy,
                positions=[_position(symbol="B/USDT", side=TradeSide.LONG)],
                symbol="A/USDT",
                direction=TradeSide.LONG,
                requested_notional=1000.0,
                requested_leverage=1.0,
                reference_price=Decimal("100"),
                stoploss_price=Decimal("98"),
            )

        assert risk_state.high_correlation_peer_count == 1
        assert risk_state.correlation_risk_discount < 1.0
        assert any("correlation_risk_discount_triggered" in record.message for record in caplog.records)

    def test_custom_threshold_prevents_peer_detection_for_moderate_correlation(self, db_session) -> None:
        # A/USDT and B/USDT move together but not perfectly (mild divergence),
        # producing a correlation below 0.999 but still very high. A very
        # strict custom threshold (0.999) should suppress peer detection that
        # the default 0.70 threshold would otherwise trigger.
        now = datetime.now(UTC).replace(microsecond=0)
        DataRepository(db_session).store_ohlcv_bars(
            [
                {
                    "symbol": "A/USDT",
                    "exchange": "binance",
                    "timeframe": "1h",
                    "time": now - timedelta(hours=61 - index),
                    "open": Decimal(str(100.0 + index)),
                    "high": Decimal(str(100.0 + index)),
                    "low": Decimal(str(100.0 + index)),
                    "close": Decimal(str(100.0 + index)),
                    "volume": Decimal("10"),
                }
                for index in range(61)
            ]
        )
        DataRepository(db_session).store_ohlcv_bars(
            [
                {
                    "symbol": "B/USDT",
                    "exchange": "binance",
                    "timeframe": "1h",
                    "time": now - timedelta(hours=61 - index),
                    "open": Decimal(str(100.0 + index * 2 + (0.4 if index % 5 == 0 else 0))),
                    "high": Decimal(str(100.0 + index * 2 + (0.4 if index % 5 == 0 else 0))),
                    "low": Decimal(str(100.0 + index * 2 + (0.4 if index % 5 == 0 else 0))),
                    "close": Decimal(str(100.0 + index * 2 + (0.4 if index % 5 == 0 else 0))),
                    "volume": Decimal("10"),
                }
                for index in range(61)
            ]
        )
        generator = PaperSignalGenerator(data_repo=DataRepository(db_session))
        strategy = _strategy(position_rules={"risk_per_trade": 0.01})

        default_paper_run = _paper_run(execution_profile={"account_equity": 10_000})
        default_state = generator._build_risk_state(
            paper_run=default_paper_run,
            strategy=strategy,
            positions=[_position(symbol="B/USDT", side=TradeSide.LONG)],
            symbol="A/USDT",
            direction=TradeSide.LONG,
            requested_notional=1000.0,
            requested_leverage=1.0,
            reference_price=Decimal("100"),
            stoploss_price=Decimal("98"),
        )

        strict_paper_run = _paper_run(execution_profile={"account_equity": 10_000, "correlation_peer_threshold": 0.999})
        strict_state = generator._build_risk_state(
            paper_run=strict_paper_run,
            strategy=strategy,
            positions=[_position(symbol="B/USDT", side=TradeSide.LONG)],
            symbol="A/USDT",
            direction=TradeSide.LONG,
            requested_notional=1000.0,
            requested_leverage=1.0,
            reference_price=Decimal("100"),
            stoploss_price=Decimal("98"),
        )

        assert default_state.high_correlation_peer_count == 1
        assert strict_state.high_correlation_peer_count == 0
        assert strict_state.correlation_risk_discount == 1.0


class TestLinkVerificationLane:
    def _strategy(self) -> StrategyContract:
        return StrategyContract(
            strategy_id="link-verification-strategy",
            strategy_key="test_link_verification",
            source="test",
            core_thesis="link verification only",
            rules=StrategyRules(**LINK_VERIFICATION_RULES),
        )

    def test_generates_fixed_long_order_regardless_of_signal_data(self, db_session) -> None:
        # No OHLCV bars stored at all -- a real directional/carry lane would
        # reject or error on missing signal data; link_verification must not.
        generator = PaperSignalGenerator(data_repo=DataRepository(db_session))
        strategy = self._strategy()
        paper_run = PaperRun(
            strategy_id=strategy.strategy_id,
            execution_profile={"strategy_lane": "link_verification", "account_equity": 10_000},
        )

        order = generator.generate_order(
            paper_run=paper_run,
            strategy=strategy,
            request=PaperRunStepRequest(symbol="BTC/USDT", timeframe="1h"),
            positions=[],
        )

        assert order.direction == TradeSide.LONG
        assert order.entry_context["paper_order_should_trade"] is True
        assert order.entry_context["strategy_lane"] == "link_verification"
        assert order.entry_context["requested_notional"] == 100.0

    def test_orders_are_tagged_ineligible_for_strategy_performance(self, db_session) -> None:
        generator = PaperSignalGenerator(data_repo=DataRepository(db_session))
        strategy = self._strategy()
        paper_run = PaperRun(
            strategy_id=strategy.strategy_id,
            execution_profile={"strategy_lane": "link_verification", "account_equity": 10_000},
        )

        order = generator.generate_order(
            paper_run=paper_run,
            strategy=strategy,
            request=PaperRunStepRequest(symbol="BTC/USDT", timeframe="1h"),
            positions=[],
        )

        assert order.entry_context["strategy_performance_eligible"] is False

    def test_recognized_via_entry_rules_flag_without_paper_run_lane(self, db_session) -> None:
        # _is_link_verification_strategy must also work when the caller only sets
        # entry_rules.link_verification_only (no paper_run.execution_profile lane).
        generator = PaperSignalGenerator(data_repo=DataRepository(db_session))
        strategy = self._strategy()
        paper_run = PaperRun(strategy_id=strategy.strategy_id, execution_profile={"account_equity": 10_000})

        order = generator.generate_order(
            paper_run=paper_run,
            strategy=strategy,
            request=PaperRunStepRequest(symbol="BTC/USDT", timeframe="1h"),
            positions=[],
        )

        assert order.entry_context["strategy_lane"] == "link_verification"

    def test_never_rejected_by_the_net_edge_after_cost_gate(self, db_session) -> None:
        generator = PaperSignalGenerator(data_repo=DataRepository(db_session))
        strategy = self._strategy()
        paper_run = PaperRun(
            strategy_id=strategy.strategy_id,
            execution_profile={"strategy_lane": "link_verification", "account_equity": 10_000},
        )

        order = generator.generate_order(
            paper_run=paper_run,
            strategy=strategy,
            request=PaperRunStepRequest(symbol="BTC/USDT", timeframe="1h"),
            positions=[],
        )

        assert net_edge_rejection_codes(order.entry_context) == []
