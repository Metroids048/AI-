from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from scripts.run_top20_technical_validation import _comparison_templates
from services.execution.bootstrap import AUTO_PAPER_TECHNICAL_KEY, AUTO_PAPER_TECHNICAL_RULES
from services.execution.decision_pipeline import DecisionPipelineResult
from services.validation.technical_replay import (
    EXIT_MODE_EXIT_LADDER,
    EXIT_MODE_FIXED_2R,
    ExitPolicy,
    HistoricalMarketDataView,
    TechnicalStrategyValidationService,
    compare_exit_policies,
)
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
    assert result.trades[0].mfe_r == 2.0
    assert result.trades[0].mae_r == 0.0
    assert result.trades[0].bars_to_mfe == result.trades[0].bars_held == 1


def test_replay_fills_at_next_bar_open_after_closed_bar_signal() -> None:
    symbol = "BTC/USDT"
    bars = _bars(symbol=symbol, timeframe="1h", count=100)
    signal_index = 80
    fill_index = signal_index + 1
    exit_index = signal_index + 2
    bars[signal_index].update(
        {
            "open": Decimal("99"),
            "high": Decimal("105"),
            "low": Decimal("98"),
            "close": Decimal("100"),
        }
    )
    bars[fill_index].update(
        {
            "open": Decimal("110"),
            "high": Decimal("110"),
            "low": Decimal("109"),
            "close": Decimal("110"),
        }
    )
    bars[exit_index].update(
        {
            "open": Decimal("110"),
            "high": Decimal("113"),
            "low": Decimal("109"),
            "close": Decimal("110"),
        }
    )
    data = {
        symbol: {
            "1h": bars,
            "15m": _bars(symbol=symbol, timeframe="15m"),
            "4h": _bars(symbol=symbol, timeframe="4h"),
        }
    }
    signal_at = bars[signal_index]["time"]
    service = TechnicalStrategyValidationService(
        pipeline_factory=lambda view: _ScheduledPipeline(view, {signal_at}),
        warmup_bars=80,
    )

    result = service.replay(strategy=_strategy(key="next-bar-open", timeframe="1h"), market_data=data)

    assert result.total_trades == 1
    trade = result.trades[0]
    assert trade.opened_at == bars[fill_index]["time"]
    assert trade.entry_price == 110.0
    assert trade.closed_at == bars[exit_index]["time"]
    assert trade.exit_reason == "takeprofit"


def test_replay_does_not_fill_signal_without_a_following_bar() -> None:
    symbol = "BTC/USDT"
    bars = _bars(symbol=symbol, timeframe="1h", count=100)
    signal_at = bars[-1]["time"]
    data = {
        symbol: {
            "1h": bars,
            "15m": _bars(symbol=symbol, timeframe="15m"),
            "4h": _bars(symbol=symbol, timeframe="4h"),
        }
    }
    service = TechnicalStrategyValidationService(
        pipeline_factory=lambda view: _ScheduledPipeline(view, {signal_at}),
        warmup_bars=80,
    )

    result = service.replay(strategy=_strategy(key="no-next-bar", timeframe="1h"), market_data=data)

    assert result.signal_count == 1
    assert result.total_trades == 0


def test_replay_does_not_fill_next_bar_outside_end_at() -> None:
    symbol = "BTC/USDT"
    bars = _bars(symbol=symbol, timeframe="1h", count=100)
    signal_at = bars[80]["time"]
    data = {
        symbol: {
            "1h": bars,
            "15m": _bars(symbol=symbol, timeframe="15m"),
            "4h": _bars(symbol=symbol, timeframe="4h"),
        }
    }
    service = TechnicalStrategyValidationService(
        pipeline_factory=lambda view: _ScheduledPipeline(view, {signal_at}),
        warmup_bars=80,
    )

    result = service.replay(
        strategy=_strategy(key="end-at-fill-boundary", timeframe="1h"),
        market_data=data,
        end_at=signal_at,
    )

    assert result.signal_count == 1
    assert result.total_trades == 0


def test_replay_closes_open_position_at_end_at_not_data_tail() -> None:
    symbol = "BTC/USDT"
    bars = _bars(symbol=symbol, timeframe="1h", count=100)
    signal_index = 80
    signal_at = bars[signal_index]["time"]
    for bar in bars[signal_index + 1 :]:
        bar.update(
            {
                "open": Decimal("100"),
                "high": Decimal("100"),
                "low": Decimal("100"),
                "close": Decimal("100"),
            }
        )
    end_at = bars[85]["time"]
    data = {
        symbol: {
            "1h": bars,
            "15m": _bars(symbol=symbol, timeframe="15m"),
            "4h": _bars(symbol=symbol, timeframe="4h"),
        }
    }
    service = TechnicalStrategyValidationService(
        pipeline_factory=lambda view: _ScheduledPipeline(view, {signal_at}),
        warmup_bars=80,
    )

    result = service.replay(
        strategy=_strategy(key="end-at-close-boundary", timeframe="1h"),
        market_data=data,
        end_at=end_at,
    )

    assert result.total_trades == 1
    assert result.trades[0].closed_at == end_at
    assert result.trades[0].exit_reason == "end_of_window"


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


def test_exit_ladder_mode_records_partial_closes_and_hold_hours() -> None:
    symbol = "BTC/USDT"
    start = datetime(2026, 1, 1, tzinfo=UTC)
    bars_1h = []
    # Warmup flat, then signal bar, then climb through 1R and 1.5R.
    for index in range(100):
        if index <= 80:
            close = Decimal("100")
            high = Decimal("100.2")
            low = Decimal("99.8")
        elif index == 81:
            # Hit 1.0R = +1 (fixed_bps 100 → stop distance 1.0)
            close = Decimal("101.1")
            high = Decimal("101.2")
            low = Decimal("100.5")
        elif index == 82:
            # Hit 1.5R = +1.5
            close = Decimal("101.6")
            high = Decimal("101.7")
            low = Decimal("101.0")
        else:
            close = Decimal("101.2")
            high = Decimal("101.3")
            low = Decimal("100.9")
        bars_1h.append(
            {
                "symbol": symbol,
                "exchange": "binance",
                "timeframe": "1h",
                "time": start + timedelta(hours=index),
                "open": Decimal("100"),
                "high": high,
                "low": low,
                "close": close,
                "volume": Decimal("100"),
            }
        )
    data = {
        symbol: {
            "1h": bars_1h,
            "15m": _bars(symbol=symbol, timeframe="15m"),
            "4h": _bars(symbol=symbol, timeframe="4h"),
        }
    }
    signal_at = bars_1h[80]["time"]
    strategy = StrategyContract(
        strategy_id="ladder",
        strategy_key="ladder",
        source="test",
        core_thesis="ladder replay",
        rules=StrategyRules(
            entry_rules={
                "entry_timeframe": "1h",
                "enabled_signals": ["ema_trend"],
                "market_intelligence_enabled": False,
                "core_fee_bps": 10.0,
                "core_slippage_bps": 0.0,
            },
            stoploss_rules={"fixed_bps": 100},
            takeprofit_rules={
                "risk_reward": 2.0,
                "exit_ladder": [
                    {"r_multiple": 1.0, "close_fraction": 0.4},
                    {"r_multiple": 1.5, "close_fraction": 0.3},
                ],
                "remainder_trail_after_r": 2.5,
            },
            position_rules={},
        ),
    )
    result = TechnicalStrategyValidationService(
        pipeline_factory=lambda view: _ScheduledPipeline(view, {signal_at}),
        warmup_bars=80,
        exit_mode="exit_ladder",
    ).replay(strategy=strategy, market_data=data)

    reasons = [trade.exit_reason for trade in result.trades]
    assert "exit_ladder_1r" in reasons
    assert "exit_ladder_1.5r" in reasons
    assert result.ladder_level_hits.get("exit_ladder_1r", 0) >= 1
    assert result.average_hold_hours >= 0
    assert (
        abs(sum(trade.quantity_fraction for trade in result.trades) - 1.0) < 1e-6
        or sum(trade.quantity_fraction for trade in result.trades) >= 0.7
    )


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


def _climbing_bars(symbol: str) -> list[dict]:
    """Warmup flat, signal at bar 80, then climb through 1R and 1.5R and hold.

    Shares the shape used by the ExitLadder replay test so the ladder arm fires
    partial closes while the fixed-2R arm rides toward its single 2R target.
    """
    start = datetime(2026, 1, 1, tzinfo=UTC)
    bars: list[dict] = []
    for index in range(100):
        if index <= 80:
            high, low, close = Decimal("100.2"), Decimal("99.8"), Decimal("100")
        elif index == 81:
            high, low, close = Decimal("101.2"), Decimal("100.5"), Decimal("101.1")
        elif index == 82:
            high, low, close = Decimal("101.7"), Decimal("101.0"), Decimal("101.6")
        else:
            high, low, close = Decimal("101.3"), Decimal("100.9"), Decimal("101.2")
        bars.append(
            {
                "symbol": symbol,
                "exchange": "binance",
                "timeframe": "1h",
                "time": start + timedelta(hours=index),
                "open": Decimal("100"),
                "high": high,
                "low": low,
                "close": close,
                "volume": Decimal("100"),
            }
        )
    return bars


def _entry_only_config(key: str) -> StrategyContract:
    """A fixed entry config whose exit side is deliberately swapped by policies."""
    return StrategyContract(
        strategy_id=key,
        strategy_key=key,
        source="test",
        core_thesis="exit-policy A/B entry",
        rules=StrategyRules(
            entry_rules={
                "entry_timeframe": "1h",
                "enabled_signals": ["ema_trend"],
                "market_intelligence_enabled": False,
                "core_fee_bps": 10.0,
                "core_slippage_bps": 0.0,
            },
            stoploss_rules={"fixed_bps": 100},
            takeprofit_rules={"risk_reward": 99.0},  # replaced per policy; must not leak
            position_rules={},
        ),
    )


def test_compare_exit_policies_isolates_exit_mechanics_on_one_entry() -> None:
    symbol = "BTC/USDT"
    data = {
        symbol: {
            "1h": _climbing_bars(symbol),
            "15m": _bars(symbol=symbol, timeframe="15m"),
            "4h": _bars(symbol=symbol, timeframe="4h"),
        }
    }
    signal_at = data[symbol]["1h"][80]["time"]
    entry_config = _entry_only_config("layered-entry")
    entry_rules_before = deepcopy(entry_config.rules.entry_rules)
    fixed = ExitPolicy(
        name="Fixed 2R",
        exit_mode=EXIT_MODE_FIXED_2R,
        exit_rules={},
        takeprofit_rules={"risk_reward": 2.0},
    )
    ladder = ExitPolicy(
        name="ExitLadder",
        exit_mode=EXIT_MODE_EXIT_LADDER,
        exit_rules={"close_on_opposite_signal": True},
        takeprofit_rules={
            "risk_reward": 2.0,
            "exit_ladder": [
                {"r_multiple": 1.0, "close_fraction": 0.4},
                {"r_multiple": 1.5, "close_fraction": 0.3},
            ],
            "remainder_trail_after_r": 2.5,
        },
    )

    report = compare_exit_policies(
        entry_config=entry_config,
        exit_policy_a=fixed,
        exit_policy_b=ladder,
        market_data=data,
        title="ExitLadder vs Fixed 2R Replay (same layered entry)",
        pipeline_factory=lambda view: _ScheduledPipeline(view, {signal_at}),
        warmup_bars=80,
    )

    # Same entry signal on both arms.
    assert report.policy_a.signal_count == report.policy_b.signal_count == 1
    assert report.policy_a_name == "Fixed 2R"
    assert report.policy_b_name == "ExitLadder"
    assert report.policy_a.exit_mode == EXIT_MODE_FIXED_2R
    assert report.policy_b.exit_mode == EXIT_MODE_EXIT_LADDER
    # Only the ladder arm books partial ladder closes.
    assert report.policy_a.ladder_level_hits == {}
    assert report.policy_b.ladder_level_hits.get("exit_ladder_1r", 0) >= 1
    # The input entry config is never mutated (immutability rule).
    assert entry_config.rules.entry_rules == entry_rules_before
    assert entry_config.rules.takeprofit_rules == {"risk_reward": 99.0}
    markdown = report.to_markdown()
    assert "# ExitLadder vs Fixed 2R Replay (same layered entry)" in markdown
    assert "| Metric | Fixed 2R | ExitLadder |" in markdown


def test_compare_exit_policies_filters_symbols_and_rejects_bad_exit_mode() -> None:
    data = {
        "BTC/USDT": {"1h": _bars(symbol="BTC/USDT", timeframe="1h")},
        "ETH/USDT": {"1h": _bars(symbol="ETH/USDT", timeframe="1h")},
    }
    fixed = ExitPolicy(
        name="Fixed 2R", exit_mode=EXIT_MODE_FIXED_2R, exit_rules={}, takeprofit_rules={"risk_reward": 2.0}
    )
    report = compare_exit_policies(
        entry_config=_entry_only_config("layered-entry"),
        exit_policy_a=fixed,
        exit_policy_b=fixed,
        market_data=data,
        symbols=["BTC/USDT"],
        warmup_bars=80,
        max_workers=1,
    )
    assert report.methodology["symbols"] == ["BTC/USDT"]

    try:
        ExitPolicy(name="bad", exit_mode="not_a_mode", exit_rules={}, takeprofit_rules={})
    except ValueError as error:
        assert "unsupported exit_mode" in str(error)
    else:  # pragma: no cover - explicit failure path
        raise AssertionError("ExitPolicy should reject an unknown exit_mode")


def test_historical_view_stubs_market_extras_so_the_real_pipeline_does_not_crash() -> None:
    # Regression guard: the production DecisionPipeline builds meta-label
    # features via _latest_funding_bps -> data_repo.get_latest_market_extras().
    # The read-only replay view must answer that call (with None) rather than
    # raising AttributeError. Every other test injects a fake pipeline, so this
    # is the only one that exercises the real evaluate() path end to end.
    symbol = "BTC/USDT"
    view = HistoricalMarketDataView({symbol: {"1h": _bars(symbol=symbol, timeframe="1h", count=120)}})
    assert view.get_latest_market_extras(symbol=symbol) is None

    data = {
        symbol: {
            "1h": _bars(symbol=symbol, timeframe="1h", count=120),
            "15m": _bars(symbol=symbol, timeframe="15m", count=480),
            "4h": _bars(symbol=symbol, timeframe="4h", count=120),
        }
    }
    # No pipeline_factory -> real DecisionPipeline over the historical view.
    metrics = TechnicalStrategyValidationService(warmup_bars=80, max_workers=1).replay(
        strategy=_strategy(key="real-pipeline", timeframe="1h"),
        market_data=data,
    )
    assert metrics.data_issues == []
