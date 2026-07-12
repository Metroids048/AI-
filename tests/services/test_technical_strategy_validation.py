from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from scripts.run_top20_technical_validation import _comparison_templates
from services.execution.bootstrap import AUTO_PAPER_TECHNICAL_KEY, AUTO_PAPER_TECHNICAL_RULES
from services.execution.decision_pipeline import DecisionPipelineResult
from services.validation.technical_replay import HistoricalMarketDataView, TechnicalStrategyValidationService
from shared.models import StrategyContract, StrategyRules, Timeframe, TradeSide


class _ScheduledPipeline:
    def __init__(self, view, signal_times: set[datetime]) -> None:  # noqa: ANN001
        self.view = view
        self.signal_times = signal_times

    def evaluate(self, *, symbol: str, timeframe: str, **_: object) -> DecisionPipelineResult:
        timestamp = self.view.cutoff
        assert timestamp is not None
        should_trade = timestamp in self.signal_times
        return DecisionPipelineResult(
            direction=TradeSide.LONG if should_trade else None,
            should_trade=should_trade,
            reason="scheduled_signal" if should_trade else "no_signal",
            reference_price=Decimal("100"),
            bar_time=timestamp,
            signals=[],
            ensemble=None,
            meta_label=None,
            veto_result=None,
            confidence_multiplier=1.0 if should_trade else 0.0,
            atr=1.0,
            volatility_context={},
            trace={"pipeline_status": "scheduled_signal" if should_trade else "no_signal"},
        )

def _bars(*, symbol: str, timeframe: str, count: int = 100) -> list[dict]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    step = {"15m": timedelta(minutes=15), "1h": timedelta(hours=1), "4h": timedelta(hours=4)}[timeframe]
    return [
        {
            "symbol": symbol,
            "exchange": "binance",
            "timeframe": timeframe,
            "time": start + step * index,
            "open": Decimal("100"),
            "high": Decimal("104"),
            "low": Decimal("100"),
            "close": Decimal("100"),
            "volume": Decimal("100"),
        }
        for index in range(count)
    ]


def _strategy(*, key: str, timeframe: str, direction_timeframe: str | None = None) -> StrategyContract:
    entry_rules: dict[str, object] = {
        "entry_timeframe": timeframe,
        "enabled_signals": ["ema_trend"],
        "market_intelligence_enabled": False,
        "core_fee_bps": 10.0,
        "core_slippage_bps": 0.0,
    }
    if direction_timeframe:
        entry_rules["direction_timeframe"] = direction_timeframe
        entry_rules["timeframe_model"] = "operator_experience_4h_15m_v1"
    return StrategyContract(
        strategy_id=key,
        strategy_key=key,
        source="test",
        core_thesis="deterministic replay test",
        rules=StrategyRules(
            entry_rules=entry_rules,
            stoploss_rules={"fixed_bps": 100},
            takeprofit_rules={"risk_reward": 2.0},
            position_rules={},
        ),
    )


def test_replay_uses_live_pipeline_and_deducts_round_trip_costs() -> None:
    symbol = "BTC/USDT"
    data = {
        symbol: {
            "1h": _bars(symbol=symbol, timeframe="1h"),
            "15m": _bars(symbol=symbol, timeframe="15m"),
            "4h": _bars(symbol=symbol, timeframe="4h"),
        }
    }
    signal_at = data[symbol]["1h"][80]["time"]
    service = TechnicalStrategyValidationService(
        pipeline_factory=lambda view: _ScheduledPipeline(view, {signal_at}),
        warmup_bars=80,
    )

    result = service.replay(strategy=_strategy(key="one-hour", timeframe="1h"), market_data=data)

    assert result.total_trades == 1
    assert result.total_fee_bps == 20.0
    assert result.total_slippage_bps == 0.0
    assert result.trades[0].exit_reason == "takeprofit"
    assert result.trades[0].gross_return > result.trades[0].net_return
    assert result.net_expectancy > 0


def test_comparison_requires_all_promotion_gates_and_never_mutates_strategy() -> None:
    symbol = "BTC/USDT"
    data = {
        symbol: {
            "1h": _bars(symbol=symbol, timeframe="1h", count=400),
            "15m": _bars(symbol=symbol, timeframe="15m", count=1600),
            "4h": _bars(symbol=symbol, timeframe="4h", count=100),
        }
    }
    baseline_signal = data[symbol]["1h"][340]["time"]
    candidate_signal = data[symbol]["15m"][1360]["time"]

    def pipeline_factory(view):
        return _ScheduledPipeline(view, {baseline_signal, candidate_signal})

    baseline = _strategy(key="validated-template", timeframe="1h")
    candidate = _strategy(key="operator-experience", timeframe="15m", direction_timeframe="4h")
    report = TechnicalStrategyValidationService(
        pipeline_factory=pipeline_factory,
        warmup_bars=80,
        walk_forward_windows=2,
    ).compare(baseline=baseline, candidate=candidate, market_data=data)

    assert report.baseline.strategy_key == "validated-template"
    assert report.candidate.strategy_key == "operator-experience"
    assert len(report.walk_forward_windows) == 2
    assert report.promotion.allowed is False
    assert "validation_thresholds" in report.promotion.failed_reasons
    assert candidate.rules.entry_rules.get("default_enabled_for_auto_trading") is None
    markdown = report.to_markdown()
    assert "# Top20 1h Baseline vs Current 4h/1h/15m Entry Prescreen" in markdown
    assert "Entry-signal prescreen only; no automatic promotion." in markdown
    assert "- Symbols (1): BTC/USDT" in markdown
    assert "- Data issues: none" in markdown
    assert "Operator candidate" not in markdown


def test_comparison_records_missing_timeframe_without_treating_it_as_a_pass() -> None:
    symbol = "BTC/USDT"
    data = {symbol: {"1h": _bars(symbol=symbol, timeframe="1h")}}
    service = TechnicalStrategyValidationService(warmup_bars=80)

    result = service.replay(
        strategy=_strategy(key="operator-experience", timeframe="15m", direction_timeframe="4h"),
        market_data=data,
    )

    assert result.total_trades == 0
    assert set(result.data_issues) == {"BTC/USDT: missing 15m", "BTC/USDT: missing 4h"}


def test_comparison_templates_use_shared_simple_exits_without_mutating_runtime_rules() -> None:
    runtime_rules_before = deepcopy(AUTO_PAPER_TECHNICAL_RULES)

    baseline, candidate = _comparison_templates()

    assert baseline.strategy_key == "validated_template_1h"
    assert baseline.timeframe == Timeframe.H1
    assert baseline.rules.entry_rules["entry_timeframe"] == "1h"
    assert "direction_timeframe" not in baseline.rules.entry_rules
    assert "state_timeframe" not in baseline.rules.entry_rules
    assert "timeframe_model" not in baseline.rules.entry_rules

    assert candidate.strategy_key == AUTO_PAPER_TECHNICAL_KEY
    assert candidate.timeframe == Timeframe.M15
    assert candidate.rules.entry_rules["direction_timeframe"] == "4h"
    assert candidate.rules.entry_rules["state_timeframe"] == "1h"
    assert candidate.rules.entry_rules["entry_timeframe"] == "15m"

    assert baseline.rules.stoploss_rules == candidate.rules.stoploss_rules
    assert baseline.rules.takeprofit_rules == candidate.rules.takeprofit_rules == {"risk_reward": 2.0}
    assert baseline.rules.exit_rules == candidate.rules.exit_rules == {}
    assert runtime_rules_before == AUTO_PAPER_TECHNICAL_RULES


def test_historical_view_reuses_slices_until_the_replay_time_changes() -> None:
    symbol = "BTC/USDT"
    bars = _bars(symbol=symbol, timeframe="15m", count=4)
    view = HistoricalMarketDataView({symbol: {"15m": bars}})
    view.set_cutoff(bars[1]["time"])

    first = view.list_ohlcv_bars(symbol=symbol, timeframe="15m", limit=10)
    repeated = view.list_ohlcv_bars(symbol=symbol, timeframe="15m", limit=10)

    assert first is repeated
    assert [bar.timestamp for bar in first] == [bars[0]["time"], bars[1]["time"]]

    view.set_cutoff(bars[2]["time"])
    advanced = view.list_ohlcv_bars(symbol=symbol, timeframe="15m", limit=10)

    assert advanced is not first
    assert advanced[-1].timestamp == bars[2]["time"]


def test_default_replay_distributes_independent_symbols_without_data_loss() -> None:
    symbols = ("BTC/USDT", "ETH/USDT")
    data = {symbol: {"1h": _bars(symbol=symbol, timeframe="1h", count=100)} for symbol in symbols}

    metrics = TechnicalStrategyValidationService(warmup_bars=80, max_workers=2).replay(
        strategy=_strategy(key="validated-template", timeframe="1h"),
        market_data=data,
    )

    assert metrics.strategy_key == "validated-template"
    assert metrics.data_issues == []
