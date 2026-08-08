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
    lifecycle_state: str = "BASELINE_ONLY"
    execution_eligible: bool = False

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
        # Focused candidates intentionally use fewer than the layered fusion
        # quorum of three direction sources.  The shared multi-timeframe
        # confirmation still runs first; weighted voting then evaluates the
        # reduced signal set instead of making these candidates impossible to
        # trade by construction.
        "fusion_method": "weighted_vote",
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


def _trend_momentum_v2_enriched_config() -> dict[str, Any]:
    """Enrich v1 entry signals to reduce scarcity, 2026-08-07.

    v1 used only MACD at 15m entry, producing zero signals in the armed testnet
    run over 48h. This candidate retains v1's 4h EMA+ADX trend filter (proven
    directional edge) but expands 15m entry to include price_action, bollinger,
    and dow_trend — a subset of what operator_heuristic_v1 uses and the
    observation run validates. Goal: let the primary candidate produce tradable
    signals without lowering risk gates or bypassing the ADR-locked sampling lane.
    """
    return _focused_candidate_config(
        candidate_id="trend_momentum_v2_enriched",
        direction_signals=["ema_trend", "adx"],
        entry_signals=["macd", "price_action", "dow_trend", "bollinger"],
    )


TREND_MOMENTUM_V2_ENRICHED = StrategyCandidate(
    candidate_id="trend_momentum_v2_enriched",
    source="evidence_simplification",
    hypothesis=(
        "EMA+ADX 4h trend filter with enriched 15m entry signals (MACD, price_action, "
        "dow_trend, bollinger) to address v1 signal scarcity while retaining directional edge"
    ),
    version="2.0.0",
    created_at=datetime(2026, 8, 7),
    market="BTC/USDT,ETH/USDT",
    timeframe="15m",
    config_factory=_trend_momentum_v2_enriched_config,
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
            "candidate_id": "pandas_ta_broad_screen_v1",
            "meta_label_min_win_rate": 0.50,
            "fusion_method": "weighted_vote",
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

    The relaxed fusion method accepts a clear 2-of-3 direction majority while
    retaining the existing entry-signal voting and all downstream risk gates.
    """
    config = _operator_heuristic_v1_config()
    config["entry_rules"]["candidate_id"] = "operator_heuristic_v2_relaxed"
    config["entry_rules"]["fusion_method"] = "layered_regime_entry_relaxed"
    # The entry trigger must agree with at least one higher timeframe.  The
    # previous implementation changed only the ensemble quorum while an
    # earlier hard three-timeframe gate still rejected the decision first, so
    # this candidate never actually implemented its documented 2-of-3 policy.
    config["entry_rules"]["mtf_confirmation_mode"] = "entry_plus_one_higher"
    return config


OPERATOR_HEURISTIC_V2_RELAXED = StrategyCandidate(
    candidate_id="operator_heuristic_v2_relaxed",
    source="operator_experience_improved",
    hypothesis=(
        "Relaxing triple-timeframe confirmation from unanimous (3/3) to majority (2/3) "
        "increases signal density without sacrificing net expectancy, addressing the "
        "漏斗过滤过严 issue identified in module 13 funnel analysis"
    ),
    version="2.0.1",
    created_at=datetime(2026, 7, 15),
    market="BTC/USDT,ETH/USDT",
    timeframe="15m",
    config_factory=_operator_heuristic_v2_relaxed_config,
)


def _trend_pullback_v1_config() -> dict[str, Any]:
    config = _operator_heuristic_v1_config()
    config["entry_rules"] = {
        **config["entry_rules"],
        "candidate_id": "trend_pullback_v1",
        "research_only": True,
        "market_regime_timeframe": "1h",
        "trend_timeframe": "4h",
        "entry_timeframe": "15m",
        "minimum_score": 70,
        "score_weights": {
            "trend_quality": 35,
            "pullback_quality": 25,
            "macd_recovery_quality": 20,
            "volume_quality": 10,
            "relative_strength_quality": 10,
        },
        "regime_adx_minimum": 22,
        "high_volatility_atr_percentile": 90,
        "pullback_ema20_atr_band": 0.5,
    }
    config["exit_rules"] = {
        "close_on_opposite_regime": True,
        "no_progress_bars": 10,
        "range_or_high_volatility_blocks_entry_only": True,
    }
    config["stoploss_rules"] = {
        **config["stoploss_rules"],
        "composition_mode": "tighter_of_atr_and_fixed",
    }
    config["takeprofit_rules"] = {"risk_reward": 2.0}
    return config


TREND_PULLBACK_V1 = StrategyCandidate(
    candidate_id="trend_pullback_v1",
    source="time_series_momentum_research",
    hypothesis=(
        "A confirmed 1h market regime, aligned 4h trend and deterministic 15m EMA pullback "
        "can improve entry timing without changing the active strategy before OOS validation"
    ),
    version="1.0.0-research",
    created_at=datetime(2026, 7, 20),
    market="BTC/USDT,ETH/USDT",
    timeframe="15m",
    config_factory=_trend_pullback_v1_config,
)


def _failed_breakout_reversal_v1_config() -> dict[str, Any]:
    """Research contract for the point-in-time proposal generator.

    This remains deliberately separate from the active manifest and legacy
    execution path until independent validation completes.
    """

    config = _operator_heuristic_v1_config()
    config["entry_rules"] = {
        **config["entry_rules"],
        "candidate_id": "failed_breakout_reversal_v1",
        "research_only": True,
        "proposal_generator": "failed_breakout_reversal_v1",
        "primary_structure_boundary": "donchian_24",
        "entry_timeframe": "15m",
    }
    return config


FAILED_BREAKOUT_REVERSAL_V1 = StrategyCandidate(
    candidate_id="failed_breakout_reversal_v1",
    source="structure_reversal_research",
    hypothesis=(
        "A 15m sweep beyond one confirmed Donchian-24 boundary that closes back inside "
        "and receives next-bar confirmation can identify bounded reversal setups."
    ),
    version="1.0.0-research",
    created_at=datetime(2026, 7, 30),
    market="BTC/USDT,ETH/USDT",
    timeframe="15m",
    config_factory=_failed_breakout_reversal_v1_config,
    lifecycle_state="RESEARCH_ONLY",
    execution_eligible=False,
)


def _trend_pullback_v2_config() -> dict[str, Any]:
    config = _operator_heuristic_v1_config()
    config["entry_rules"] = {
        **config["entry_rules"],
        "candidate_id": "trend_pullback_v2",
        "research_only": True,
        "proposal_generator": "trend_pullback_v2",
        "entry_timeframe": "15m",
    }
    return config


TREND_PULLBACK_V2 = StrategyCandidate(
    candidate_id="trend_pullback_v2",
    source="time_series_momentum_research",
    hypothesis=(
        "A trend-aligned 15m EMA pullback with volume contraction and next-bar confirmation "
        "can improve entry timing without a higher-timeframe hard veto."
    ),
    version="2.0.0-research",
    created_at=datetime(2026, 7, 30),
    market="BTC/USDT,ETH/USDT",
    timeframe="15m",
    config_factory=_trend_pullback_v2_config,
    lifecycle_state="RESEARCH_ONLY",
    execution_eligible=False,
)


def _range_sweep_reversion_v1_config() -> dict[str, Any]:
    config = _operator_heuristic_v1_config()
    config["entry_rules"] = {
        **config["entry_rules"],
        "candidate_id": "range_sweep_reversion_v1",
        "research_only": True,
        "proposal_generator": "range_sweep_reversion_v1",
        "primary_structure_boundary": "donchian_24",
        "entry_timeframe": "15m",
    }
    return config


RANGE_SWEEP_REVERSION_V1 = StrategyCandidate(
    candidate_id="range_sweep_reversion_v1",
    source="liquidity_sweep_research",
    hypothesis=(
        "A confirmed 15m liquidity sweep beyond a stable Donchian-24 range boundary that "
        "closes back inside the range can produce bounded mean-reversion proposals."
    ),
    version="1.0.0-research",
    created_at=datetime(2026, 7, 30),
    market="BTC/USDT,ETH/USDT",
    timeframe="15m",
    config_factory=_range_sweep_reversion_v1_config,
    lifecycle_state="RESEARCH_ONLY",
    execution_eligible=False,
)


# ============================================================================
# Registry
# ============================================================================

CANDIDATE_REGISTRY: dict[str, StrategyCandidate] = {
    "operator_heuristic_v1": OPERATOR_HEURISTIC_V1,
    "trend_momentum_v1": TREND_MOMENTUM_V1,
    "trend_momentum_v2_enriched": TREND_MOMENTUM_V2_ENRICHED,
    "trend_breakout_v1": TREND_BREAKOUT_V1,
    "pandas_ta_broad_screen_v1": PANDAS_TA_BROAD_SCREEN,
    "operator_heuristic_v2_relaxed": OPERATOR_HEURISTIC_V2_RELAXED,
    "trend_pullback_v1": TREND_PULLBACK_V1,
    "failed_breakout_reversal_v1": FAILED_BREAKOUT_REVERSAL_V1,
    "trend_pullback_v2": TREND_PULLBACK_V2,
    "range_sweep_reversion_v1": RANGE_SWEEP_REVERSION_V1,
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
