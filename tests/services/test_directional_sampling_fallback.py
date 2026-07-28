from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from services.data.universe import AUTO_SIMULATION_EXECUTION_SYMBOLS, execution_scope_hash
from services.execution.decision_pipeline import (
    DecisionPipeline,
    DecisionPipelineResult,
    closed_bars_for_decision,
)
from services.execution.net_edge import net_edge_rejection_codes
from services.execution.paper_signal import PaperSignalGenerator, _sampling_min_notional_usdt
from services.strategy_library.candidates.registry import get_candidate
from shared.config import settings
from shared.models import (
    OHLCVBar,
    PaperRun,
    PaperRunStepRequest,
    StrategyContract,
    StrategyRules,
    TradeSide,
    TradeSignal,
)


def _bar(timeframe: str) -> OHLCVBar:
    return OHLCVBar(
        symbol="BTC/USDT",
        exchange="binance",
        timeframe=timeframe,
        timestamp=datetime(2026, 7, 24, 0, 0, tzinfo=UTC),
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100"),
        volume=Decimal("10"),
    )


def test_decision_bars_exclude_the_still_open_rest_candle() -> None:
    opened_at = datetime(2026, 7, 27, 10, 0, tzinfo=UTC)
    bars = [
        _bar("15m").model_copy(update={"timestamp": opened_at}),
        _bar("15m").model_copy(update={"timestamp": opened_at + timedelta(minutes=15)}),
    ]

    closed = closed_bars_for_decision(
        bars,
        timeframe="15m",
        decision_time=opened_at + timedelta(minutes=20),
    )

    assert [bar.timestamp for bar in closed] == [opened_at]


class _TimeframeRepo:
    def list_ohlcv_bars(self, *, symbol: str, timeframe: str, limit: int):  # noqa: ANN201
        del symbol, limit
        return [_bar(timeframe)]


def _signal(side: TradeSide, source: str) -> TradeSignal:
    return TradeSignal(
        symbol="BTC/USDT",
        side=side,
        source=source,
        signal_time=datetime(2026, 7, 24, 0, 0, tzinfo=UTC),
        reason=f"{source}_{side.value}",
        confidence=0.8,
    )


def _decision(*, should_trade: bool, reason: str, candidate_id: str = "trend_momentum_v1") -> DecisionPipelineResult:
    return DecisionPipelineResult(
        direction=TradeSide.LONG if should_trade else None,
        should_trade=should_trade,
        reason=reason,
        reference_price=Decimal("100"),
        bar_time=datetime(2026, 7, 24, 0, 0, tzinfo=UTC),
        signals=[_signal(TradeSide.LONG, "technical_macd")] if should_trade else [],
        ensemble=None,
        meta_label=None,
        veto_result=None,
        confidence_multiplier=1.0,
        atr=1.0,
        volatility_context={},
        trace={
            "pipeline_status": "bet_taken" if should_trade else reason,
            "strategy_lane": "directional",
            "candidate_id": candidate_id,
        },
    )


def _armed_profile(**overrides):  # noqa: ANN202
    profile = {
        "strategy_lane": "directional",
        "execution_mode": "binance_testnet",
        "mirror_to_gateway": True,
        "cost_gate_verified": True,
        "simulation_sampling_fallback_enabled": True,
        "acceptance_symbols": list(AUTO_SIMULATION_EXECUTION_SYMBOLS),
        "acceptance_scope_hash": execution_scope_hash(),
    }
    profile.update(overrides)
    return profile


def test_sampling_min_notional_uses_universe_asset_exchange_floor() -> None:
    strategy = StrategyContract(
        strategy_id="strategy-primary",
        strategy_key="auto_paper_mature_templates",
        source="candidate",
        core_thesis="primary",
        rules=StrategyRules(**get_candidate("trend_momentum_v1").get_config()),
    )
    run = PaperRun(
        paper_run_id="run-directional",
        strategy_id=strategy.strategy_id,
        execution_profile=_armed_profile(
            min_notional_usdt=20,
            universe_assets=[
                {"platform_symbol": "BTC/USDT", "min_notional": "50"},
                {"platform_symbol": "ETH/USDT", "min_notional": "20"},
            ],
        ),
    )

    assert _sampling_min_notional_usdt(paper_run=run, strategy=strategy, symbol="BTC/USDT") == 50.0
    assert _sampling_min_notional_usdt(paper_run=run, strategy=strategy, symbol="ETH/USDT") == 20.0


def test_relaxed_candidate_accepts_entry_plus_one_higher_timeframe(monkeypatch) -> None:
    strategy = StrategyContract(
        strategy_id="strategy-relaxed",
        strategy_key="auto_paper_mature_templates",
        source="candidate",
        core_thesis="relaxed MTF",
        rules=StrategyRules(**get_candidate("operator_heuristic_v2_relaxed").get_config()),
    )
    pipeline = DecisionPipeline(data_repo=_TimeframeRepo())
    responses = iter(
        [
            [_signal(TradeSide.LONG, "technical_ema_trend"), _signal(TradeSide.LONG, "technical_adx")],
            [_signal(TradeSide.SHORT, "technical_ema_trend"), _signal(TradeSide.SHORT, "technical_adx")],
        ]
    )
    monkeypatch.setattr(pipeline, "_technical_signals", lambda **_kwargs: next(responses))

    result = pipeline._multi_timeframe_confirmation(
        strategy=strategy,
        symbol="BTC/USDT",
        timeframe="15m",
        main_signals=[_signal(TradeSide.LONG, "technical_macd")],
        enabled_signals={"macd"},
    )

    assert result["passed"] is True
    assert result["status"] == "entry_plus_one_higher_confirmed"
    assert {signal.side for signal in result["direction_signals"]} == {TradeSide.LONG}


def test_relaxed_candidate_rejects_when_no_higher_timeframe_matches(monkeypatch) -> None:
    strategy = StrategyContract(
        strategy_id="strategy-relaxed",
        strategy_key="auto_paper_mature_templates",
        source="candidate",
        core_thesis="relaxed MTF",
        rules=StrategyRules(**get_candidate("operator_heuristic_v2_relaxed").get_config()),
    )
    pipeline = DecisionPipeline(data_repo=_TimeframeRepo())
    responses = iter(
        [
            [_signal(TradeSide.SHORT, "technical_ema_trend"), _signal(TradeSide.SHORT, "technical_adx")],
            [_signal(TradeSide.SHORT, "technical_ema_trend"), _signal(TradeSide.SHORT, "technical_adx")],
        ]
    )
    monkeypatch.setattr(pipeline, "_technical_signals", lambda **_kwargs: next(responses))

    result = pipeline._multi_timeframe_confirmation(
        strategy=strategy,
        symbol="BTC/USDT",
        timeframe="15m",
        main_signals=[_signal(TradeSide.LONG, "technical_macd")],
        enabled_signals={"macd"},
    )

    assert result["passed"] is False
    assert result["status"] == "entry_plus_one_higher_disagreed"


def test_directional_testnet_run_uses_sampling_fallback_after_primary_starvation(monkeypatch) -> None:
    monkeypatch.setattr(settings, "binance_use_testnet", True)
    monkeypatch.setattr(settings, "live_trading_enabled", False)
    monkeypatch.setattr(settings, "binance_auto_execute", True)
    strategy = StrategyContract(
        strategy_id="strategy-primary",
        strategy_key="auto_paper_mature_templates",
        source="candidate",
        core_thesis="primary",
        rules=StrategyRules(**get_candidate("trend_momentum_v1").get_config()),
    )
    generator = PaperSignalGenerator(data_repo=_TimeframeRepo())
    calls: list[tuple[str, bool]] = []

    def evaluate(**kwargs):  # noqa: ANN202
        candidate_id = str(kwargs["strategy"].rules.entry_rules.get("candidate_id"))
        calls.append((candidate_id, kwargs["enable_decision_veto"]))
        return _decision(should_trade=False, reason="multi_timeframe_disagreement")

    monkeypatch.setattr(generator.decision_pipeline, "evaluate", evaluate)
    monkeypatch.setattr(
        generator,
        "_sampling_lane_decision",
        lambda **_kwargs: _decision(
            should_trade=True,
            reason="testnet_sampling_lane_signal",
            candidate_id="testnet_sampling_lane_v1",
        ),
    )
    run = PaperRun(
        paper_run_id="run-directional",
        strategy_id=strategy.strategy_id,
        execution_profile=_armed_profile(),
    )

    result = generator._decision_for_strategy(
        strategy=strategy,
        symbol="BTC/USDT",
        timeframe="15m",
        request=PaperRunStepRequest(symbol="BTC/USDT", timeframe="15m", enable_decision_veto=True),
        paper_run=run,
    )

    assert calls == [("trend_momentum_v1", True)]
    assert result.should_trade is True
    assert result.trace["decision_variant"] == "simulation_sampling_fallback"
    assert result.trace["primary_candidate_id"] == "trend_momentum_v1"
    assert result.trace["primary_rejection_reason"] == "multi_timeframe_disagreement"
    assert result.trace["candidate_id"] == "testnet_sampling_lane_v1"
    assert result.trace["evidence_class"] == "NON_PROMOTABLE_PIPELINE_SAMPLE"
    assert result.trace["testnet_sampling_mode"] is True


@pytest.mark.parametrize(
    ("execution_mode", "use_testnet", "live_enabled"),
    [
        ("local_paper", True, False),
        ("binance_testnet", False, False),
        ("binance_testnet", True, True),
    ],
)
def test_sampling_fallback_never_runs_outside_safe_testnet(
    monkeypatch, execution_mode: str, use_testnet: bool, live_enabled: bool
) -> None:
    monkeypatch.setattr(settings, "binance_use_testnet", use_testnet)
    monkeypatch.setattr(settings, "live_trading_enabled", live_enabled)
    monkeypatch.setattr(settings, "binance_auto_execute", True)
    strategy = StrategyContract(
        strategy_id="strategy-primary",
        strategy_key="auto_paper_mature_templates",
        source="candidate",
        core_thesis="primary",
        rules=StrategyRules(**get_candidate("trend_momentum_v1").get_config()),
    )
    generator = PaperSignalGenerator(data_repo=_TimeframeRepo())
    calls = 0

    def evaluate(**_kwargs):  # noqa: ANN202
        nonlocal calls
        calls += 1
        return _decision(should_trade=False, reason="technical_signals_insufficient")

    monkeypatch.setattr(generator.decision_pipeline, "evaluate", evaluate)
    run = PaperRun(
        paper_run_id="run-directional",
        strategy_id=strategy.strategy_id,
        execution_profile=_armed_profile(
            execution_mode=execution_mode,
            mirror_to_gateway=execution_mode == "binance_testnet",
            cost_gate_verified=execution_mode == "binance_testnet",
        ),
    )

    result = generator._decision_for_strategy(
        strategy=strategy,
        symbol="BTC/USDT",
        timeframe="15m",
        request=PaperRunStepRequest(symbol="BTC/USDT", timeframe="15m", enable_decision_veto=True),
        paper_run=run,
    )

    assert calls == 1
    assert result.should_trade is False


def test_sampling_fallback_rejects_symbol_outside_exact_btc_eth_scope(monkeypatch) -> None:
    monkeypatch.setattr(settings, "binance_use_testnet", True)
    monkeypatch.setattr(settings, "live_trading_enabled", False)
    monkeypatch.setattr(settings, "binance_auto_execute", True)
    primary = _decision(should_trade=False, reason="technical_signals_insufficient")
    run = PaperRun(paper_run_id="run-directional", strategy_id="strategy-primary", execution_profile=_armed_profile())

    assert PaperSignalGenerator._sampling_fallback_allowed(paper_run=run, primary=primary, symbol="SOL/USDT") is False


def test_sampling_fallback_rejects_mismatched_acceptance_scope(monkeypatch) -> None:
    monkeypatch.setattr(settings, "binance_use_testnet", True)
    monkeypatch.setattr(settings, "live_trading_enabled", False)
    monkeypatch.setattr(settings, "binance_auto_execute", True)
    primary = _decision(should_trade=False, reason="technical_signals_insufficient")
    run = PaperRun(
        paper_run_id="run-directional",
        strategy_id="strategy-primary",
        execution_profile=_armed_profile(
            acceptance_symbols=["BTC/USDT", "ETH/USDT", "SOL/USDT"],
            acceptance_scope_hash="wrong",
        ),
    )

    assert PaperSignalGenerator._sampling_fallback_allowed(paper_run=run, primary=primary, symbol="BTC/USDT") is False


def test_sampling_fallback_exposes_stable_ineligibility_reason(monkeypatch) -> None:
    monkeypatch.setattr(settings, "binance_use_testnet", True)
    monkeypatch.setattr(settings, "live_trading_enabled", False)
    monkeypatch.setattr(settings, "binance_auto_execute", True)
    primary = _decision(should_trade=False, reason="technical_signals_insufficient")
    run = PaperRun(
        paper_run_id="run-directional",
        strategy_id="strategy-primary",
        execution_profile=_armed_profile(cost_gate_verified=False),
    )

    assert (
        PaperSignalGenerator._sampling_fallback_ineligibility_reason(
            paper_run=run,
            primary=primary,
            symbol="BTC/USDT",
        )
        == "SAMPLING_COST_GATE_NOT_VERIFIED"
    )


def test_sampling_fallback_is_separated_from_primary_strategy_performance(monkeypatch) -> None:
    monkeypatch.setattr(settings, "binance_use_testnet", True)
    monkeypatch.setattr(settings, "live_trading_enabled", False)
    monkeypatch.setattr(settings, "binance_auto_execute", True)
    strategy = StrategyContract(
        strategy_id="strategy-primary",
        strategy_key="auto_paper_mature_templates",
        source="candidate",
        core_thesis="primary",
        rules=StrategyRules(**get_candidate("trend_momentum_v1").get_config()),
    )
    generator = PaperSignalGenerator(data_repo=_TimeframeRepo())
    monkeypatch.setattr(
        generator.decision_pipeline,
        "evaluate",
        lambda **_kwargs: _decision(
            should_trade=False,
            reason="multi_timeframe_disagreement",
        ),
    )
    monkeypatch.setattr(
        generator,
        "_sampling_lane_decision",
        lambda **_kwargs: _decision(
            should_trade=True,
            reason="testnet_sampling_lane_signal",
            candidate_id="testnet_sampling_lane_v1",
        ),
    )
    run = PaperRun(
        paper_run_id="run-directional",
        strategy_id=strategy.strategy_id,
        execution_profile={**_armed_profile(), "account_equity": 10_000},
    )

    order = generator.generate_order(
        paper_run=run,
        strategy=strategy,
        request=PaperRunStepRequest(symbol="BTC/USDT", timeframe="15m", enable_decision_veto=False),
        positions=[],
    )

    assert order.entry_context["decision_variant"] == "simulation_sampling_fallback"
    assert order.entry_context["strategy_performance_eligible"] is False
    assert order.entry_context["sampling_performance_eligible"] is True
    assert order.entry_context["order_type"] == "market"


def test_testnet_sampling_bypasses_only_prevalidation_edge_gate(monkeypatch) -> None:
    context = {
        "testnet_sampling_mode": True,
        "decision_variant": "simulation_sampling_fallback",
        "strategy_lane": "directional",
        "execution_scope_hash": execution_scope_hash(),
        "decision_pipeline": {"pipeline_status": "bet_taken"},
        "meta_label_win_rate": None,
        "meta_label_average_win": None,
        "meta_label_average_loss": None,
        "round_trip_fee_rate": None,
        "round_trip_slippage_rate": None,
    }
    monkeypatch.setattr(settings, "binance_use_testnet", True)
    monkeypatch.setattr(settings, "live_trading_enabled", False)
    monkeypatch.setattr(settings, "binance_auto_execute", True)
    assert net_edge_rejection_codes(context) == []

    monkeypatch.setattr(settings, "binance_use_testnet", False)
    assert net_edge_rejection_codes(context) == ["missing_meta_edge_stats"]


class _SyntheticTrendRepo:
    def __init__(self) -> None:
        self.frames = {
            "4h": self._bars(timeframe="4h", base=100.0, step=0.20),
            "1h": self._bars(timeframe="1h", base=200.0, step=-0.10),
            "15m": self._bars(timeframe="15m", base=100.0, step=0.03, final_acceleration=0.50),
        }

    @staticmethod
    def _bars(*, timeframe: str, base: float, step: float, final_acceleration: float = 0.0) -> list[OHLCVBar]:
        minutes = {"15m": 15, "1h": 60, "4h": 240}[timeframe]
        start = datetime(2026, 1, 1, tzinfo=UTC)
        price = base
        bars: list[OHLCVBar] = []
        for index in range(240):
            increment = step + (final_acceleration if index >= 233 else 0.0)
            open_price = price
            close_price = price + increment
            price = close_price
            bars.append(
                OHLCVBar(
                    symbol="BTC/USDT",
                    exchange="binance",
                    timeframe=timeframe,
                    timestamp=start + timedelta(minutes=minutes * index),
                    open=Decimal(str(open_price)),
                    high=Decimal(str(max(open_price, close_price) + 0.5)),
                    low=Decimal(str(min(open_price, close_price) - 0.5)),
                    close=Decimal(str(close_price)),
                    volume=Decimal("100"),
                )
            )
        return bars

    def list_ohlcv_bars(self, *, symbol: str, timeframe: str, limit: int):  # noqa: ANN201
        del symbol
        return self.frames.get(timeframe, [])[-limit:]

    def get_latest_ohlcv_bar(self, *, symbol: str, timeframe: str):  # noqa: ANN201
        del symbol
        bars = self.frames.get(timeframe, [])
        return bars[-1] if bars else None


class _SamplingBandRepo:
    def list_ohlcv_bars(self, *, symbol: str, timeframe: str, limit: int):  # noqa: ANN201
        del symbol
        start = datetime(2026, 1, 1, tzinfo=UTC)
        price = 100.0
        bars: list[OHLCVBar] = []
        increments = [0.32, -0.20] * 58 + [0.15, 0.18, 0.20, 0.22]
        for index, increment in enumerate(increments):
            open_price = price
            price += increment
            bars.append(
                OHLCVBar(
                    symbol="BTC/USDT",
                    exchange="binance",
                    timeframe=timeframe,
                    timestamp=start + timedelta(minutes=15 * index),
                    open=Decimal(str(open_price)),
                    high=Decimal(str(max(open_price, price) + 0.2)),
                    low=Decimal(str(min(open_price, price) - 0.2)),
                    close=Decimal(str(price)),
                    volume=Decimal("100"),
                )
            )
        return bars[-limit:]


def test_sampling_lane_uses_locked_ema_macd_rsi_atr_rule() -> None:
    generator = PaperSignalGenerator(data_repo=_SamplingBandRepo())

    result = generator._sampling_lane_decision(
        symbol="BTC/USDT",
        timeframe="15m",
        primary=_decision(
            should_trade=False,
            reason="technical_signals_insufficient",
        ),
    )

    assert result.should_trade is True
    assert result.direction is TradeSide.LONG
    assert result.trace["evidence_class"] == "NON_PROMOTABLE_PIPELINE_SAMPLE"
    metrics = result.trace["sampling_metrics"]
    assert metrics["close"] > metrics["ema50"]
    assert metrics["macd_histogram"] > 0
    assert 50 <= metrics["rsi14"] <= 72
    assert metrics["atr14"] > 0

    def get_latest_market_extras(self, *, symbol: str):  # noqa: ANN201
        del symbol
        return None


def test_real_indicator_sampling_rejects_when_sampling_rsi_band_is_not_met(monkeypatch) -> None:
    monkeypatch.setattr(settings, "binance_use_testnet", True)
    monkeypatch.setattr(settings, "live_trading_enabled", False)
    monkeypatch.setattr(settings, "binance_auto_execute", True)
    monkeypatch.setattr(settings, "market_intelligence_enabled", False)
    strategy = StrategyContract(
        strategy_id="strategy-primary-real-indicators",
        strategy_key="auto_paper_mature_templates",
        source="candidate",
        core_thesis="primary real indicators",
        rules=StrategyRules(**get_candidate("trend_momentum_v1").get_config()),
    )
    generator = PaperSignalGenerator(data_repo=_SyntheticTrendRepo())
    run = PaperRun(
        paper_run_id="run-directional-real-indicators",
        strategy_id=strategy.strategy_id,
        execution_profile=_armed_profile(),
    )

    result = generator._decision_for_strategy(
        strategy=strategy,
        symbol="BTC/USDT",
        timeframe="15m",
        request=PaperRunStepRequest(symbol="BTC/USDT", timeframe="15m", enable_decision_veto=False),
        paper_run=run,
    )

    assert result.should_trade is False
    assert result.trace["sampling_fallback_attempted"] is True
    assert result.trace["sampling_fallback_rejection_reason"] in {
        "SAMPLING_RULES_NOT_ALIGNED",
        "SAMPLING_INDICATORS_UNAVAILABLE",
    }
