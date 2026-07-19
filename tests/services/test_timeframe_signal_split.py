from __future__ import annotations

from services.execution.decision_pipeline import _enabled_signals
from shared.models import StrategyContract, StrategyRules


def test_enabled_signals_uses_entry_subset_on_15m() -> None:
    strategy = StrategyContract(
        strategy_id="s1",
        strategy_key="k1",
        source="test",
        core_thesis="split",
        rules=StrategyRules(
            entry_rules={
                "direction_timeframe": "4h",
                "entry_timeframe": "15m",
                "direction_signals": ["dow_trend", "ema_trend", "adx"],
                "entry_signals": ["rsi", "macd", "price_action"],
                "enabled_signals": ["dow_trend", "rsi", "macd"],
            }
        ),
    )
    entry = _enabled_signals(strategy, timeframe="15m")
    direction = _enabled_signals(strategy, timeframe="4h")
    assert entry == {"rsi", "macd", "price_action"}
    assert direction == {"dow_trend", "ema_trend", "adx"}


def test_enabled_signals_falls_back_to_union_when_no_role_config() -> None:
    strategy = StrategyContract(
        strategy_id="s2",
        strategy_key="k2",
        source="test",
        core_thesis="legacy",
        rules=StrategyRules(entry_rules={"enabled_signals": ["ema_trend", "rsi"]}),
    )
    assert _enabled_signals(strategy, timeframe="15m") == {"ema_trend", "rsi"}


def test_enabled_signals_keeps_registered_pandas_ta_signals() -> None:
    strategy = StrategyContract(
        strategy_id="pandas-ta",
        strategy_key="pandas-ta",
        source="test",
        core_thesis="adapter wiring",
        rules=StrategyRules(
            entry_rules={"enabled_signals": ["pandas_ta_supertrend", "pandas_ta_stoch_rsi"]}
        ),
    )

    assert _enabled_signals(strategy, timeframe="15m") == {
        "pandas_ta_supertrend",
        "pandas_ta_stoch_rsi",
    }
