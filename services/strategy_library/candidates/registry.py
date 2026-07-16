"""Candidate strategy registry: transform single hard-coded config into competing candidates.

This module implements the "候选策略注册表" (Candidate Strategy Registry) from the
multi-candidate competition framework. Each candidate is a self-contained, independently
testable strategy configuration that can be fed to the existing technical_replay.py
backtest engine for fair comparison.

Design principles:
1. Each candidate is a function returning a StrategyRules-compatible dict
2. Candidates are versioned and tagged with source/hypothesis metadata
3. All candidates share the same interface, making them drop-in compatible with validation
4. The registry enables A/B comparison, leaderboard ranking, and walk-forward validation
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class StrategyCandidate:
    """Metadata + config factory for a single strategy candidate.

    Attributes:
        candidate_id: Unique identifier (e.g. "operator_heuristic_v1")
        source: Origin of this strategy (e.g. "operator_experience", "pandas_ta_screen")
        hypothesis: Core thesis behind this candidate
        version: Semantic version string
        created_at: When this candidate was added
        market: Target market (e.g. "BTC/USDT")
        timeframe: Primary entry timeframe
        config_factory: Callable that returns StrategyRules-compatible dict
    """

    candidate_id: str
    source: str
    hypothesis: str
    version: str
    created_at: datetime
    market: str
    timeframe: str
    config_factory: Any  # Callable[[], dict[str, Any]]

    def get_config(self) -> dict[str, Any]:
        """Return the strategy configuration dict."""
        return self.config_factory()


# ============================================================================
# Candidate 1: Operator Heuristic v1 (Baseline)
# ============================================================================


def _operator_heuristic_v1_config() -> dict[str, Any]:
    """Current AUTO_PAPER_TECHNICAL_RULES as a versioned baseline candidate.

    This is the "操作员经验版" that has been running since 2026-07. It represents
    the hand-tuned combination of 10 indicators with 15m/1h/4h triple-confirmation.

    This candidate is the baseline for all comparisons. It will NOT be modified;
    any improvements become new v2/v3/etc candidates.
    """
    return {
        "entry_rules": {
            "technical_pipeline": True,
            "strategy_lanes": ["trend_breakout", "volatility_filtered_breakout"],
            "timeframe_model": "4h_direction_15m_entry",
            "direction_timeframe": "4h",
            "state_timeframe": "1h",
            "entry_timeframe": "15m",
            "direction_signals": ["dow_trend", "ema_trend", "adx", "mtf_ma"],
            "entry_signals": ["macd", "price_action", "rsi", "vwap", "bollinger", "fvg"],
            "enabled_signals": [
                "macd",
                "dow_trend",
                "ema_trend",
                "adx",
                "price_action",
                "rsi",
                "vwap",
                "bollinger",
                "fvg",
                "mtf_ma",
            ],
            "meta_label_min_win_rate": 0.42,
            "candidate_id": "operator_heuristic_v1",
            "fusion_method": "layered_regime_entry",
            "core_fee_bps": 5.0,
            "core_slippage_bps": 1.0,
            "standard_fee_bps": 5.0,
            "standard_slippage_bps": 3.0,
            "minimum_net_reward_r": 1.0,
        },
        "exit_rules": {
            "close_on_opposite_signal": True,
            "time_exit_hours": 24,
            "time_exit_min_r": 0.5,
        },
        "stoploss_rules": {
            "atr_multiple": 2.0,
            "fixed_bps": 250,
        },
        "takeprofit_rules": {
            "risk_reward": 2.0,
        },
        "position_rules": {
            "risk_per_trade": 0.05,
            "max_portfolio_initial_risk_fraction": 0.25,
            "max_leverage": 40,
            "max_position_fraction": 0.35,
            "min_notional_usdt": 20,
        },
    }


OPERATOR_HEURISTIC_V1 = StrategyCandidate(
    candidate_id="operator_heuristic_v1",
    source="operator_experience",
    hypothesis=(
        "Triple-timeframe confirmation (15m entry, 1h state, 4h direction) with 10 "
        "hand-selected technical indicators can filter out false signals and achieve "
        "positive net expectancy on BTC/USDT perpetual futures"
    ),
    version="1.0.0",
    created_at=datetime(2026, 7, 15),
    market="BTC/USDT",
    timeframe="15m",
    config_factory=_operator_heuristic_v1_config,
)


def _focused_candidate_config(
    *, candidate_id: str, direction_signals: list[str], entry_signals: list[str]
) -> dict[str, Any]:
    config = _operator_heuristic_v1_config()
    config["entry_rules"] = {
        **config["entry_rules"],
        "candidate_id": candidate_id,
        "direction_signals": direction_signals,
        "entry_signals": entry_signals,
        "enabled_signals": [*direction_signals, *entry_signals],
    }
    return config


def _trend_momentum_v1_config() -> dict[str, Any]:
    return _focused_candidate_config(
        candidate_id="trend_momentum_v1",
        direction_signals=["ema_trend", "adx"],
        entry_signals=["macd"],
    )


TREND_MOMENTUM_V1 = StrategyCandidate(
    candidate_id="trend_momentum_v1",
    source="evidence_simplification",
    hypothesis="EMA direction plus ADX state and MACD entry can retain trend edge with fewer votes",
    version="1.0.0",
    created_at=datetime(2026, 7, 16),
    market="BTC/USDT,ETH/USDT,SOL/USDT",
    timeframe="15m",
    config_factory=_trend_momentum_v1_config,
)


def _trend_breakout_v1_config() -> dict[str, Any]:
    return _focused_candidate_config(
        candidate_id="trend_breakout_v1",
        direction_signals=["dow_trend", "adx"],
        entry_signals=["price_action", "fvg"],
    )


TREND_BREAKOUT_V1 = StrategyCandidate(
    candidate_id="trend_breakout_v1",
    source="evidence_simplification",
    hypothesis="Dow direction plus ADX state and price-action/FVG entries can isolate breakout edge",
    version="1.0.0",
    created_at=datetime(2026, 7, 16),
    market="BTC/USDT,ETH/USDT,SOL/USDT",
    timeframe="15m",
    config_factory=_trend_breakout_v1_config,
)


# ============================================================================
# Candidate 2: pandas_ta Broad Screen (Data-Driven)
# ============================================================================


def _pandas_ta_broad_screen_config() -> dict[str, Any]:
    """Data-driven indicator selection from pandas_ta's 150+ indicator library.

    Hypothesis: Instead of hand-picking 10 "classic" indicators (MACD/RSI/etc),
    run each pandas_ta indicator independently on historical data, measure its
    standalone net expectancy, and only include indicators with positive marginal value.

    This candidate starts with a minimal set (SuperTrend + Stoch RSI) as a proof of
    concept. The full "broad screen" would be: run all 150 indicators individually,
    rank by net expectancy, keep top N uncorrelated ones.

    For now, we use a subset to validate the approach works.
    """
    return {
        "entry_rules": {
            "technical_pipeline": True,
            "strategy_lanes": ["trend_breakout"],
            "timeframe_model": "4h_direction_15m_entry",
            "direction_timeframe": "4h",
            "state_timeframe": "1h",
            "entry_timeframe": "15m",
            # Start with just 2 pandas_ta indicators as proof of concept
            "enabled_signals": [
                "pandas_ta_supertrend",
                "pandas_ta_stoch_rsi",
            ],
            "meta_label_min_win_rate": 0.50,
            "fusion_method": "layered_regime_entry",
            "core_fee_bps": 5.0,
            "core_slippage_bps": 1.0,
            "standard_fee_bps": 5.0,
            "standard_slippage_bps": 3.0,
            "minimum_net_reward_r": 1.0,
        },
        "exit_rules": {
            "close_on_opposite_signal": True,
            "time_exit_hours": 24,
            "time_exit_min_r": 0.5,
        },
        "stoploss_rules": {
            "atr_multiple": 2.0,
            "fixed_bps": 250,
        },
        "takeprofit_rules": {
            "risk_reward": 2.0,
        },
        "position_rules": {
            "risk_per_trade": 0.025,
            "max_portfolio_initial_risk_fraction": 0.15,
            "max_leverage": 25,
            "max_position_fraction": 0.20,
            "min_notional_usdt": 20,
        },
    }


PANDAS_TA_BROAD_SCREEN = StrategyCandidate(
    candidate_id="pandas_ta_broad_screen_v1",
    source="data_driven_screening",
    hypothesis=(
        "Empirically screen pandas_ta's 150+ indicators by standalone historical net "
        "expectancy, select only those with positive marginal value, eliminate the "
        "assumption that 'classic' indicators (MACD/RSI) are necessarily optimal"
    ),
    version="1.0.0",
    created_at=datetime(2026, 7, 15),
    market="BTC/USDT",
    timeframe="15m",
    config_factory=_pandas_ta_broad_screen_config,
)


# ============================================================================
# Candidate 3: Operator Heuristic v2 (Relaxed Confirmation)
# ============================================================================


def _operator_heuristic_v2_relaxed_config() -> dict[str, Any]:
    """Relax the triple-timeframe confirmation to allow 2-out-of-3 agreement.

    Hypothesis: The漏斗分析 (funnel analysis from module 13) showed that requiring
    ALL three timeframes to agree (15m/1h/4h) may be too strict, causing the system
    to miss valid entries. This candidate tests whether "any 2 out of 3 timeframes
    agree" can increase sample size while maintaining positive net expectancy.

    Implementation note: This requires modifying the signal fusion logic to accept
    majority vote instead of unanimous agreement. For now, this is a placeholder
    config identical to v1 -- the actual relaxed logic will be implemented in
    services/execution/paper_signal.py's fusion method.
    """
    config = _operator_heuristic_v1_config()
    # TODO: Change fusion_method to "layered_regime_entry_relaxed" once implemented
    config["entry_rules"]["fusion_method"] = "layered_regime_entry"  # Placeholder
    return config


OPERATOR_HEURISTIC_V2_RELAXED = StrategyCandidate(
    candidate_id="operator_heuristic_v2_relaxed",
    source="operator_experience_improved",
    hypothesis=(
        "Relaxing triple-timeframe confirmation from unanimous (3/3) to majority (2/3) "
        "increases signal density without sacrificing net expectancy, addressing the "
        "漏斗过滤过严 issue identified in module 13 funnel analysis"
    ),
    version="2.0.0",
    created_at=datetime(2026, 7, 15),
    market="BTC/USDT",
    timeframe="15m",
    config_factory=_operator_heuristic_v2_relaxed_config,
)


# ============================================================================
# Registry
# ============================================================================

CANDIDATE_REGISTRY: dict[str, StrategyCandidate] = {
    "operator_heuristic_v1": OPERATOR_HEURISTIC_V1,
    "trend_momentum_v1": TREND_MOMENTUM_V1,
    "trend_breakout_v1": TREND_BREAKOUT_V1,
}


def get_candidate(candidate_id: str) -> StrategyCandidate:
    """Retrieve a candidate by ID.

    Raises:
        KeyError: If candidate_id not found
    """
    if candidate_id not in CANDIDATE_REGISTRY:
        available = ", ".join(CANDIDATE_REGISTRY.keys())
        raise KeyError(f"Unknown candidate: {candidate_id}. Available: {available}")
    return CANDIDATE_REGISTRY[candidate_id]


def list_candidates() -> list[str]:
    """Return all registered candidate IDs."""
    return list(CANDIDATE_REGISTRY.keys())
