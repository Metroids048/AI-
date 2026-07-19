"""Local runtime bootstrap helpers for Paper/Testnet integration."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from services.data.service import DEFAULT_BINANCE_TOP20
from services.data.universe import AUTO_PAPER_RESEARCH_SYMBOLS, fixed_top20_assets
from services.execution.risk_tiers import default_asset_risk_tiers, scale_asset_risk_tiers
from shared.config import settings
from shared.models.risk import (
    MEDIUM_RISK_PROFILE_KEY,
    PAPER_RUNTIME_CONFIG_VERSION,
    PAPER_RUNTIME_LIMITS,
    medium_risk_profile,
)

logger = logging.getLogger(__name__)

AUTO_PAPER_RUNTIME_KEY = "auto_paper_btc_funding"
AUTO_PAPER_TECHNICAL_KEY = "auto_paper_mature_templates"
AUTO_PAPER_EXECUTION_SYMBOLS = ("BTC/USDT", "ETH/USDT")
CANONICAL_MANIFEST_ROOT = Path("docs/evidence/active-manifests")
OPERATOR_EXPERIENCE_STRATEGY_KEY = "operator_experience_4h_15m_v1"
LINK_VERIFICATION_STRATEGY_KEY = "link_verification_fixed_notional"
LINK_VERIFICATION_RUNTIME_KEY = "link_verification"

# Medium-risk auto-trading preset for Top20 + funding carry admission.
# Fee/slippage assumptions below are calibrated to real Binance USDM regular-user
# rates (maker 2bps / taker 5bps, see https://www.binance.com/en/fee/futureFee) plus
# a conservative slippage buffer for market-order fills. Previous 8bps/6bps fee+
# slippage (round-trip 28bps for a 4-fill carry hedge) overstated real cost by
# roughly 2x and was silently killing otherwise-valid funding-arbitrage signals.
AUTO_PAPER_STRATEGY_RULES: dict[str, Any] = {
    "entry_rules": {
        "funding_threshold_bps": 0.5,
        "min_estimated_net_edge_bps": 5.0,
        "requires_positive_funding": True,
        "fee_bps": 5.0,
        "slippage_bps": 3.0,
    },
    "exit_rules": {"close_on_opposite_signal": True},
    "stoploss_rules": {"atr_multiple": 2.0, "fixed_bps": 250},
    "takeprofit_rules": {"risk_reward": 3.0, "trail_after_r": 1.5},
    "position_rules": {
        # Bumped moderately more aggressive (2026-07 operator request) alongside
        # the paper-sizing floor fix; risk_per_trade 0.01->0.015, leverage 10->15.
        "risk_per_trade": 0.015,
        "max_leverage": 15,
        "max_position_fraction": 0.18,
        "min_notional_usdt": 20,
    },
}

AUTO_PAPER_TECHNICAL_RULES: dict[str, Any] = {
    "entry_rules": {
        "technical_pipeline": True,
        "strategy_lanes": ["trend_breakout", "volatility_filtered_breakout"],
        "timeframe_model": "4h_direction_15m_entry",
        "direction_timeframe": "4h",
        "state_timeframe": "1h",
        "entry_timeframe": "15m",
        # 4h/1h structural confirmation vs 15m entry triggers use different subsets
        # (plan p1-timeframe-signal-split). `enabled_signals` remains the union for
        # backward compatibility; decision_pipeline picks the role-specific subset.
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
        # Lowered from 0.50 to 0.42 (2026-07-15 Paper testing optimization): AGGRESSIVE
        # threshold for Paper/Testnet sampling - 42% × 2R = 0.84 expectancy. This is
        # BELOW breakeven but acceptable for Paper testing to generate sufficient trade
        # samples for edge analysis. Will collect 100+ trades then re-calibrate based
        # on real observed win rate. NOT for live trading without re-validation.
        "meta_label_min_win_rate": 0.42,
        "fusion_method": "layered_regime_entry",
        # Real Binance USDM regular-user taker fee is 5bps one-way (see
        # https://www.binance.com/en/fee/futureFee); previous 10/18bps one-way
        # assumptions were 2-3.6x too conservative and made
        # net_edge_after_cost_negative reject most otherwise-valid directional
        # candidates on thin 15m statistical edges. Slippage buffer stays
        # nonzero for standard-tier symbols (thinner books than BTC/ETH/SOL).
        "core_fee_bps": 5.0,
        "core_slippage_bps": 1.0,
        "standard_fee_bps": 5.0,
        "standard_slippage_bps": 3.0,
        "minimum_net_reward_r": 1.0,
        "candidate_id": "operator_heuristic_v1",
    },
    "exit_rules": {"close_on_opposite_signal": True, "time_exit_hours": 24, "time_exit_min_r": 0.5},
    "stoploss_rules": {"atr_multiple": 2.0, "fixed_bps": 250},
    # Fixed full-close 2R take-profit, no ExitLadder/partial-close mechanism.
    # A real Top20 historical replay (docs/audits/2026-07-12-exitladder-replay-comparison.md)
    # directly compared this exact fixed-2R config against the ExitLadder partial-exit
    # config on the SAME entry signal and found ExitLadder net expectancy -0.000866
    # (PF 0.8817, max DD 143.56%) vs fixed 2R net expectancy +0.002185 (PF 1.1308,
    # max DD 52.2%) -- ExitLadder was strictly worse on every metric. Reverted to the
    # only exit config with real positive-net-expectancy evidence on this entry signal.
    "takeprofit_rules": {"risk_reward": 2.0},
    "position_rules": {
        "risk_per_trade": PAPER_RUNTIME_LIMITS["risk_per_trade"],
        "max_portfolio_initial_risk_fraction": PAPER_RUNTIME_LIMITS["max_portfolio_initial_risk_fraction"],
        "max_leverage": PAPER_RUNTIME_LIMITS["max_leverage"],
        "max_position_fraction": PAPER_RUNTIME_LIMITS["max_symbol_exposure"],
        "min_notional_usdt": PAPER_RUNTIME_LIMITS["min_notional_usdt"],
    },
}


def resolve_auto_paper_technical_evidence() -> tuple[dict[str, Any], tuple[str, ...]]:
    """Resolve an eligible candidate manifest without trusting stale or mismatched rules."""
    from services.execution.signal_edge_stats import strategy_rules_hash
    from services.strategy_library.candidates.registry import get_candidate
    from shared.models import StrategyRules

    manifest_path = CANONICAL_MANIFEST_ROOT / f"{AUTO_PAPER_TECHNICAL_KEY}.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if int(manifest["schema_version"]) != 2:
            raise ValueError("unsupported manifest schema")
        candidate_id = str(manifest["candidate_id"])
        config = get_candidate(candidate_id).get_config()
        rules = StrategyRules(**config)
        if strategy_rules_hash(rules) != manifest["rules_hash"]:
            raise ValueError("manifest rules hash mismatch")
        eligible = tuple(
            symbol
            for symbol in AUTO_PAPER_RESEARCH_SYMBOLS
            if symbol in {str(value) for value in manifest.get("eligible_symbols", [])}
        )
        if not eligible:
            raise ValueError("manifest has no eligible research symbols")
        return config, eligible
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return AUTO_PAPER_TECHNICAL_RULES, AUTO_PAPER_RESEARCH_SYMBOLS

# Medium-term swing trading preset: 1d direction + 4h entry, designed for lower
# turnover and less competition with HFT algorithms. This is a NEW hypothesis
# (not yet validated via historical replay) distinct from the short-term 4h/15m
# combination in AUTO_PAPER_TECHNICAL_RULES. The current "net expectancy negative"
# conclusion was measured on 15m/4h, NOT on this 1d/4h combination -- so this
# configuration deserves its own independent out-of-sample validation via
# TechnicalStrategyValidationService before being armed for auto-trading.
AUTO_PAPER_SWING_RULES: dict[str, Any] = {
    "entry_rules": {
        "technical_pipeline": True,
        "timeframe_model": "custom",
        "direction_timeframe": "1d",  # Daily for major trend direction
        "entry_timeframe": "4h",       # 4-hour for refined entry timing
        "state_timeframe": "1d",       # Use daily for regime/volatility state
        # Medium-term favors structure/trend signals; mean-reversion signals like
        # VWAP/Bollinger are disabled until evidence supports them on daily scale.
        "enabled_signals": [
            "dow_trend",      # Multi-day trend structure
            "ema_trend",      # Crossover on daily scale
            "adx",            # Trend strength
            "macd",           # Momentum divergence
            "price_action",   # Pinbar/engulfing still valid on 4h entries
        ],
        # Lowered from 0.50 to 0.42 (2026-07-15 Paper testing optimization): same as
        # directional - AGGRESSIVE threshold for Paper/Testnet trade sampling. Medium-term
        # 1d/4h hypothesis untested, needs sufficient samples to measure real edge.
        "meta_label_min_win_rate": 0.42,
        "fusion_method": "layered_regime_entry",
        "market_intelligence_enabled": False,
        "core_fee_bps": 5.0,
        "core_slippage_bps": 1.0,
        "standard_fee_bps": 5.0,
        "standard_slippage_bps": 3.0,
        "minimum_net_reward_r": 1.0,
        # Correlation/exposure limits inherited from execution_profile if not overridden
    },
    "exit_rules": {
        "close_on_opposite_signal": True,
        "time_exit_hours": 24 * 14,  # 14-day max hold (vs 24h for short-term)
        "time_exit_min_r": 0.5,
    },
    # Medium-term volatility is higher, so stop distance must be wider to avoid
    # being stopped out by normal daily noise. ATR multiple 2.0 -> 2.5.
    "stoploss_rules": {"atr_multiple": 2.5},
    "takeprofit_rules": {"risk_reward": 2.0},
    "position_rules": {
        # ULTRA-AGGRESSIVE Paper testing sizing: increased to 5%, matching
        # directional lane for maximum trade sampling efficiency.
        "risk_per_trade": 0.05,
        "max_portfolio_initial_risk_fraction": 0.25,  # Increased from 0.20
        "max_leverage": 30,  # Increased from 20 for medium-term
        "max_position_fraction": 0.30,  # Increased from 0.20
        "min_notional_usdt": 20,
    },
}

OPERATOR_EXPERIENCE_RULES: dict[str, Any] = {
    "entry_rules": {
        "technical_pipeline": True,
        "timeframe_model": "operator_experience_4h_15m_v1",
        "direction_timeframe": "4h",
        "entry_timeframe": "15m",
        "enabled_signals": ["dow_trend", "ema_trend", "adx", "price_action", "rsi", "macd"],
        "default_enabled_for_auto_trading": False,
    },
    "exit_rules": {"close_on_opposite_signal": True},
    "stoploss_rules": {"atr_multiple": 2.0, "fixed_bps": 250},
    "takeprofit_rules": {"risk_reward": 2.5, "trail_after_r": 1.5},
    "position_rules": {"risk_per_trade": 0.01, "max_leverage": 5, "max_position_fraction": 0.05},
}

# Cross-sectional funding-rate carry: rank the fixed Top20 basket by current
# funding rate every cycle, short the highest payers and long the lowest/most
# negative, and hold until the symbol's rank drops out of the basket. Delta
# exposure is directionally hedged across the basket (roughly market-neutral
# by construction, not by an explicit hedge leg), so this is a genuinely new
# risk profile versus the existing single-symbol `carry` lane, not a variant
# of it. Research-candidate only until it clears a dedicated OOS replay,
# see services/validation/cross_sectional_replay.py.
AUTO_PAPER_CROSS_SECTIONAL_CARRY_KEY = "auto_paper_cross_sectional_carry"
AUTO_PAPER_CROSS_SECTIONAL_CARRY_RULES: dict[str, Any] = {
    "entry_rules": {
        "strategy_type": "cross_sectional_funding_carry",
        "basket_size": 3,
        "rebalance_hours": 8,
        "fee_bps": 5.0,
        "slippage_bps": 3.0,
        "min_estimated_net_edge_bps": 5.0,
        "default_enabled_for_auto_trading": False,
    },
    "exit_rules": {"exit_on_rank_dropout": True, "close_on_opposite_signal": False},
    "stoploss_rules": {"atr_multiple": 3.0, "fixed_bps": 400},
    "takeprofit_rules": {},
    "position_rules": {
        "risk_per_trade": 0.01,
        "max_leverage": 5,
        "max_position_fraction": 0.08,
        "min_notional_usdt": 20,
    },
}

# Link-verification lane: never evaluates real signals/ensemble/meta-label and
# never subject to the net_edge_after_cost gate (see
# services/execution/paper_signal.py::_link_verification_decision). Its only
# purpose is to exercise the order -> stoploss -> takeprofit -> close pipeline
# with a stable, fixed-notional order; it must stay isolated from strategy
# performance evidence, so entry_context always tags
# strategy_performance_eligible=False for orders from this lane and
# default_enabled_for_auto_trading stays False (armed only via the explicit
# bootstrap endpoint, never the startup auto-cycle).
LINK_VERIFICATION_RULES: dict[str, Any] = {
    "entry_rules": {
        "link_verification_only": True,
        "default_enabled_for_auto_trading": False,
    },
    "exit_rules": {"close_on_opposite_signal": True},
    "stoploss_rules": {"fixed_bps": 250},
    "takeprofit_rules": {"risk_reward": 2.0},
    "position_rules": {
        "notional_usdt": 100,
        "max_leverage": 2,
        "min_notional_usdt": 20,
    },
}

# Signal observation lane: uses real signal ensemble + multi-indicator fusion
# (identical to AUTO_PAPER_TECHNICAL_RULES), but bypasses the net_edge_after_cost
# gate to allow orders even when the statistical edge is marginal. This lane's
# purpose is to accumulate real execution samples for Module 5's edge statistics
# (services/validation/compute_signal_edge_stats.py requires >= 30 closed trades)
# while the main directional lane's cost gate remains active. Orders from this lane
# are tagged strategy_performance_eligible=False and do NOT count toward strategy
# validation metrics -- they are purely observational data collection until enough
# sample density accumulates for compute_signal_edge_stats to produce reliable
# estimates, at which point the operator can decide whether to promote these signals
# to the main directional lane based on the measured real-world edge.
SIGNAL_OBSERVATION_RUNTIME_KEY = "signal_observation"
SIGNAL_OBSERVATION_STRATEGY_KEY = "signal_observation_technical"
SIGNAL_OBSERVATION_RULES: dict[str, Any] = {
    **AUTO_PAPER_TECHNICAL_RULES,  # Copy all real signal logic
    "entry_rules": {
        **AUTO_PAPER_TECHNICAL_RULES["entry_rules"],
        "default_enabled_for_auto_trading": False,  # Explicit operator opt-in only
        "order_type": "limit",
    },
}


def binance_credentials_configured() -> bool:
    return bool(settings.binance_api_key and settings.binance_api_secret)


def default_mirror_to_gateway() -> bool:
    """New automatic runs remain local until a cost-gated Testnet trial is explicitly armed."""
    return False


def bootstrap_medium_risk_profile() -> str:
    """Ensure the medium-risk profile exists for auto Paper/Testnet cycles."""
    from services.database import get_session_factory
    from services.strategy_library import RiskProfileRepository
    from shared.models import RiskProfileUpdate

    profile = medium_risk_profile()
    with get_session_factory()() as session:
        repo = RiskProfileRepository(session)
        existing = repo.get_profile(MEDIUM_RISK_PROFILE_KEY)
        if existing is None:
            repo.create_profile(profile)
            session.commit()
            logger.info("created medium risk profile: %s", MEDIUM_RISK_PROFILE_KEY)
        else:
            repo.update_profile(
                MEDIUM_RISK_PROFILE_KEY,
                RiskProfileUpdate(**profile.model_dump(exclude={"risk_profile_id"})),
            )
            session.commit()
            logger.info("updated medium risk profile: %s", MEDIUM_RISK_PROFILE_KEY)
    return MEDIUM_RISK_PROFILE_KEY


def bootstrap_paper_testnet_mirror() -> int:
    """Do not auto-enable gateway mirroring for existing PaperRuns."""
    logger.info("paper testnet mirror bootstrap skipped: mirroring is operator opt-in")
    return 0


def bootstrap_clear_stale_blocking_risk_events() -> int:
    """Deprecated: stale events are resolved only after a fresh heartbeat confirms data."""
    return 0


def refresh_fixed_top20_runtime_universe(exchange_info_symbols: list[dict[str, Any]]) -> int:
    """Replace stale bootstrap contract metadata only after Binance confirms it."""
    from services.data.universe import fixed_top20_assets
    from services.database import get_session_factory
    from services.strategy_library import PaperRunRepository

    assets = [asset.model_dump(mode="json") for asset in fixed_top20_assets(exchange_info_symbols)]
    if not all(
        asset["tradable_status"] == "trading"
        and asset["precision"]
        and asset["min_notional"] is not None
        for asset in assets
    ):
        return 0
    updated = 0
    with get_session_factory()() as session:
        repo = PaperRunRepository(session)
        for run in repo.list_paper_runs():
            if run.execution_profile.get("universe_mode") != "fixed_top20":
                continue
            repo.update_paper_run(
                run.paper_run_id or "",
                execution_profile={**run.execution_profile, "universe_assets": assets},
            )
            updated += 1
        session.commit()
    return updated


def _sync_auto_paper_strategy(strategy_repo, strategy, *, rules: dict[str, Any]) -> None:  # noqa: ANN001
    from shared.models import StrategyRules, StrategyUpdate

    updated_rules = StrategyRules(**{**strategy.rules.model_dump(), **rules})
    if strategy.rules.model_dump() != updated_rules.model_dump():
        strategy_repo.update_strategy(
            strategy.strategy_id or "",
            StrategyUpdate(rules=updated_rules),
        )
        logger.info("upgraded auto paper strategy rules for %s", strategy.strategy_key)


def _ensure_auto_paper_run(
    *,
    runtime_key: str,
    strategy_key: str,
    strategy_lane: str,
    core_thesis: str,
    rules: dict[str, Any],
    risk_profile_id: str,
    auto_schedule_enabled: bool,
    force_paper_only: bool = False,
    preserve_testnet_authorization: bool = False,
    symbols: tuple[str, ...] | list[str] | None = None,
) -> str | None:
    from services.database import get_session_factory
    from services.strategy_library import PaperRunRepository, StrategyRepository, ValidationRepository
    from shared.models import (
        BacktestRun,
        GateDecision,
        PaperRun,
        StrategyContract,
        StrategyCreate,
        StrategyRules,
        Timeframe,
    )

    runtime_symbols = list(symbols or DEFAULT_BINANCE_TOP20)
    with get_session_factory()() as session:
        strategy_repo = StrategyRepository(session)
        validation_repo = ValidationRepository(session)
        paper_repo = PaperRunRepository(session)

        strategy: StrategyContract | None = None
        for item in strategy_repo.list_strategies():
            if item.strategy_key == strategy_key:
                strategy = item
                break
        if strategy is None:
            strategy = strategy_repo.create_strategy(
                StrategyCreate(
                    strategy_key=strategy_key,
                    source="platform:auto_bootstrap",
                    core_thesis=core_thesis,
                    symbol_scope=runtime_symbols,
                    timeframe=Timeframe.M1,
                    rules=StrategyRules(**rules),
                )
            )
        else:
            _sync_auto_paper_strategy(strategy_repo, strategy, rules=rules)
            strategy = strategy_repo.get_strategy(strategy.strategy_id or "")
        if strategy is None:
            raise ValueError(f"auto paper strategy disappeared: {strategy_key}")

        backtest: BacktestRun | None = None
        for run in validation_repo.list_backtest_runs():
            if (
                run.strategy_id == strategy.strategy_id
                and run.validation_methodology.get("auto_paper_runtime_key") == runtime_key
            ):
                backtest = run
                break
        if backtest is None:
            backtest = validation_repo.create_backtest_run(
                BacktestRun(
                    strategy_id=strategy.strategy_id,
                    execution_engine="vectorbt",
                    parameter_set={"auto_paper_runtime": True},
                    validation_methodology={
                        "auto_paper_runtime_key": runtime_key,
                        "strategy_lane": strategy_lane,
                        "paper_only": True,
                        "live_promotion_allowed": False,
                        "admission_note": (
                            "Operator-approved Binance simulation bootstrap. "
                            "Live promotion still requires real backtest/OOS evidence."
                        ),
                    },
                    metrics_summary=None,
                    run_status="completed",
                    eligibility_result=GateDecision(
                        strategy_id=strategy.strategy_id,
                        passed=True,
                        decision_status="accepted",
                        reason="Accepted for Binance simulation only; live promotion requires validated OOS report.",
                    ),
                )
            )

        universe_assets = [
            asset.model_dump(mode="json")
            for asset in fixed_top20_assets()
            if asset.platform_symbol in runtime_symbols
        ]
        strategy_lanes = ["carry"] if strategy_lane == "carry" else list(rules["entry_rules"].get("strategy_lanes", []))
        paper_run: PaperRun | None = None
        for paper_candidate in paper_repo.list_paper_runs():
            if (
                paper_candidate.strategy_id == strategy.strategy_id
                and paper_candidate.execution_profile.get("auto_paper_runtime_key") == runtime_key
            ):
                paper_run = paper_candidate
                break
        max_leverage = float(rules["position_rules"]["max_leverage"])
        max_symbol_exposure = float(rules["position_rules"].get("max_position_fraction", 0.2))
        execution_profile = {
            "auto_paper_runtime_key": runtime_key,
            "strategy_lane": strategy_lane,
            "strategy_lanes": strategy_lanes,
            "account_equity": 10_000,
            "equity_peak": 10_000,
            "execution_mode": "paper_only"
            if force_paper_only or not default_mirror_to_gateway()
            else "binance_simulation_first",
            "mirror_to_gateway": False if force_paper_only else default_mirror_to_gateway(),
            "auto_schedule_enabled": auto_schedule_enabled,
            "cost_gate_verified": False,
            "risk_profile_id": risk_profile_id,
            "runtime_config_version": PAPER_RUNTIME_CONFIG_VERSION,
            "risk_per_trade": PAPER_RUNTIME_LIMITS["risk_per_trade"],
            "max_portfolio_initial_risk_fraction": PAPER_RUNTIME_LIMITS["max_portfolio_initial_risk_fraction"],
            "max_total_exposure": PAPER_RUNTIME_LIMITS["max_total_exposure"],
            "daily_loss_limit": PAPER_RUNTIME_LIMITS["daily_loss_limit"],
            "min_notional_usdt": PAPER_RUNTIME_LIMITS["min_notional_usdt"],
            "max_leverage": max_leverage,
            "max_symbol_exposure": max_symbol_exposure,
            "asset_risk_tiers": scale_asset_risk_tiers(
                default_asset_risk_tiers(),
                max_leverage=max_leverage,
                max_symbol_exposure=max_symbol_exposure,
            ),
            "max_symbols": len(runtime_symbols),
            "universe_mode": "fixed_top20",
            "universe_assets": universe_assets,
            "llm_veto_enabled": True,
            "market_intelligence_enabled": True,
        }
        if paper_run is None:
            paper_run = paper_repo.create_paper_run(
                PaperRun(
                    strategy_id=strategy.strategy_id,
                    symbol_scope=runtime_symbols,
                    candidate_symbols=runtime_symbols,
                    selection_basis="fixed_operator_top20",
                    gate_decision_ref=backtest.backtest_run_id,
                    execution_profile=execution_profile,
                    paper_status="running",
                )
            )
        else:
            # Preserve operator-armed Testnet gates. Bootstrap used to clobber
            # cost_gate_verified/mirror flags back to paper_only on every restart.
            previous = dict(paper_run.execution_profile)
            preserved_keys = [
                "cost_gate_verified",
                "testnet_acceptance_verified_at",
                # Otherwise a manually-disabled LLM veto silently flips back to
                # enabled on every bootstrap restart (hardcoded True above).
                "llm_veto_enabled",
            ]
            if not force_paper_only or preserve_testnet_authorization:
                preserved_keys.extend(("mirror_to_gateway", "execution_mode"))
            if preserve_testnet_authorization:
                preserved_keys.extend(("acceptance_symbols", "acceptance_scope_hash"))
            preserved = {key: previous[key] for key in preserved_keys if key in previous}
            profile = {**previous, **execution_profile, **preserved}
            paper_run = (
                paper_repo.update_paper_run(
                    paper_run.paper_run_id or "",
                    execution_profile=profile,
                    paper_status="running",
                    candidate_symbols=runtime_symbols,
                    symbol_scope=runtime_symbols,
                    selection_basis="fixed_operator_top20",
                )
                or paper_run
            )

        session.commit()
        paper_run_id = paper_run.paper_run_id
        if paper_run_id:
            logger.info(
                "auto trading paper run ready: %s (lane=%s top20 + medium risk)",
                paper_run_id,
                strategy_lane,
            )
        return paper_run_id


def bootstrap_auto_trading_paper_run() -> str | None:
    """Ensure a running carry-lane PaperRun exists for funding arbitrage cycles."""
    if not binance_credentials_configured():
        logger.info("auto paper run bootstrap skipped: binance credentials not configured")
        return None

    risk_profile_id = bootstrap_medium_risk_profile()
    return _ensure_auto_paper_run(
        runtime_key=AUTO_PAPER_RUNTIME_KEY,
        strategy_key=AUTO_PAPER_RUNTIME_KEY,
        strategy_lane="carry",
        core_thesis=(
            "Local auto-cycle bootstrap strategy. Scans Binance USDT-M Top20 with "
            "funding carry admission (net edge + basis checks); exposure capped by medium risk profile."
        ),
        rules=AUTO_PAPER_STRATEGY_RULES,
        risk_profile_id=risk_profile_id,
        auto_schedule_enabled=False,
    )


def bootstrap_auto_trading_technical_paper_run() -> str | None:
    """Ensure a running mature-template PaperRun exists for technical + LLM veto cycles."""
    if not binance_credentials_configured():
        logger.info("auto technical paper run bootstrap skipped: binance credentials not configured")
        return None

    risk_profile_id = bootstrap_medium_risk_profile()
    resolved_rules, _eligible_symbols = resolve_auto_paper_technical_evidence()
    # candidate_symbols is the research-universe scope (always Top3 for monitoring).
    # The manifest's eligible_symbols are the per-symbol trading gate enforced at
    # signal time via active.json presence — not the paper-run scope definition.
    return _ensure_auto_paper_run(
        runtime_key=AUTO_PAPER_TECHNICAL_KEY,
        strategy_key=AUTO_PAPER_TECHNICAL_KEY,
        strategy_lane="directional",
        core_thesis=(
            "Local auto-cycle bootstrap strategy. Scans the active BTC/ETH/SOL research universe through "
            "mature template lanes: funding/carry, trend breakout, mean reversion, and "
            "volatility-filtered breakout. Operator 4h/15m experience logic is kept as a disabled "
            "research candidate, not the default auto lane."
        ),
        rules=resolved_rules,
        risk_profile_id=risk_profile_id,
        auto_schedule_enabled=True,
        force_paper_only=True,
        symbols=AUTO_PAPER_EXECUTION_SYMBOLS,
    )


def bootstrap_link_verification_strategy() -> str | None:
    """Create/refresh the link-verification PaperRun on demand (explicit API call
    only -- deliberately NOT wired into bootstrap_local_paper_runtime(), since
    unlike the disabled-research-candidate strategies below this creates a real
    PaperRun that should only exist when an operator asks to test the order
    pipeline itself)."""
    risk_profile_id = bootstrap_medium_risk_profile()
    return _ensure_auto_paper_run(
        runtime_key=LINK_VERIFICATION_RUNTIME_KEY,
        strategy_key=LINK_VERIFICATION_STRATEGY_KEY,
        strategy_lane="link_verification",
        core_thesis=(
            "Link-verification only: bypasses real signal/ensemble/meta-label evaluation and the "
            "net_edge_after_cost gate to admit a fixed-notional order every cycle, so operators can "
            "exercise the order -> stoploss -> takeprofit -> close pipeline itself. Never treated as "
            "strategy performance evidence; orders are tagged strategy_performance_eligible=False."
        ),
        rules=LINK_VERIFICATION_RULES,
        risk_profile_id=risk_profile_id,
        auto_schedule_enabled=False,
        force_paper_only=True,
    )


def bootstrap_signal_observation_strategy() -> str | None:
    """Create/refresh the scheduled local-only signal-observation PaperRun.

    This lane uses REAL signal ensemble + multi-indicator fusion (identical to
    AUTO_PAPER_TECHNICAL_RULES), but bypasses the net_edge_after_cost gate. Its
    purpose is to accumulate >= 30 real execution samples so Module 5's edge
    statistics (services/validation/compute_signal_edge_stats.py) can produce
    reliable real-world edge estimates. Orders from this lane are tagged
    strategy_performance_eligible=False (not counted toward strategy validation
    metrics).

    Graduation criterion: once compute_signal_edge_stats reaches >= 30 closed
    trades and its measured net expectancy turns positive, the operator can
    consider promoting these signals to the main directional lane; if it stays
    negative with >= 30 samples, that is real evidence the signals lack edge in
    current market conditions."""
    risk_profile_id = bootstrap_medium_risk_profile()
    return _ensure_auto_paper_run(
        runtime_key=SIGNAL_OBSERVATION_RUNTIME_KEY,
        strategy_key=SIGNAL_OBSERVATION_STRATEGY_KEY,
        strategy_lane="signal_observation",
        core_thesis=(
            "Signal observation channel: uses real multi-indicator ensemble (identical to "
            "AUTO_PAPER_TECHNICAL_RULES), but bypasses net_edge_after_cost gate to accumulate >= 30 "
            "real execution samples for Module 5 edge statistics. Orders tagged "
            "strategy_performance_eligible=False (observational data only, not validation evidence). "
            "It is scheduled only by the local Paper runtime and cannot mirror to a gateway."
        ),
        rules=SIGNAL_OBSERVATION_RULES,
        risk_profile_id=risk_profile_id,
        auto_schedule_enabled=True,
        force_paper_only=True,
        preserve_testnet_authorization=True,
        symbols=AUTO_PAPER_RESEARCH_SYMBOLS,
    )


def bootstrap_operator_experience_strategy() -> str | None:
    """Register the 4h/15m operator-experience strategy as disabled research material."""
    from services.database import get_session_factory
    from services.strategy_library import StrategyRepository
    from shared.models import RunStatus, StrategyCreate, StrategyRules, Timeframe

    with get_session_factory()() as session:
        repo = StrategyRepository(session)
        existing = next(
            (item for item in repo.list_strategies() if item.strategy_key == OPERATOR_EXPERIENCE_STRATEGY_KEY),
            None,
        )
        if existing is None:
            strategy = repo.create_strategy(
                StrategyCreate(
                    strategy_key=OPERATOR_EXPERIENCE_STRATEGY_KEY,
                    source="operator:research_candidate",
                    core_thesis="4h direction + 15m entry operator experience; disabled until separately validated.",
                    symbol_scope=list(DEFAULT_BINANCE_TOP20),
                    timeframe=Timeframe.M15,
                    rules=StrategyRules(**OPERATOR_EXPERIENCE_RULES),
                )
            )
            repo.update_lifecycle_status(strategy.strategy_id or "", paper_status=RunStatus.NOT_STARTED)
            session.commit()
            return strategy.strategy_id
        _sync_auto_paper_strategy(repo, existing, rules=OPERATOR_EXPERIENCE_RULES)
        repo.update_lifecycle_status(existing.strategy_id or "", paper_status=RunStatus.NOT_STARTED)
        session.commit()
        return existing.strategy_id


def bootstrap_cross_sectional_carry_strategy() -> str | None:
    """Register the cross-sectional funding-rate carry strategy as disabled research
    material. Per AGENTS.md non-negotiables 1/2/6, a strategy must clear
    backtest -> OOS evidence before it is auto-armed for live Paper cycles; this
    is a new, previously-unimplemented strategy shape with no such evidence yet,
    so it is registered the same way as `operator_experience_4h_15m_v1` -- visible
    and versioned, but not scanned by the auto-cycle scheduler."""
    from services.database import get_session_factory
    from services.strategy_library import StrategyRepository
    from shared.models import RunStatus, StrategyCreate, StrategyRules, Timeframe

    with get_session_factory()() as session:
        repo = StrategyRepository(session)
        existing = next(
            (item for item in repo.list_strategies() if item.strategy_key == AUTO_PAPER_CROSS_SECTIONAL_CARRY_KEY),
            None,
        )
        if existing is None:
            strategy = repo.create_strategy(
                StrategyCreate(
                    strategy_key=AUTO_PAPER_CROSS_SECTIONAL_CARRY_KEY,
                    source="operator:research_candidate",
                    core_thesis=(
                        "Cross-sectional funding-rate carry: rank the fixed Top20 basket by current "
                        "funding rate every cycle, short the highest payers and long the lowest/most "
                        "negative, hold until rank drops out of the basket. Disabled until a dedicated "
                        "OOS replay clears the AGENTS.md validation gate."
                    ),
                    symbol_scope=list(DEFAULT_BINANCE_TOP20),
                    timeframe=Timeframe.H1,
                    rules=StrategyRules(**AUTO_PAPER_CROSS_SECTIONAL_CARRY_RULES),
                )
            )
            repo.update_lifecycle_status(strategy.strategy_id or "", paper_status=RunStatus.NOT_STARTED)
            session.commit()
            return strategy.strategy_id
        _sync_auto_paper_strategy(repo, existing, rules=AUTO_PAPER_CROSS_SECTIONAL_CARRY_RULES)
        repo.update_lifecycle_status(existing.strategy_id or "", paper_status=RunStatus.NOT_STARTED)
        session.commit()
        return existing.strategy_id


AUTO_PAPER_SWING_KEY = "auto_paper_swing_1d_4h"


def bootstrap_auto_trading_swing_paper_run() -> str | None:
    """Register and ENABLE the 1d/4h medium-term swing strategy for Paper testing.

    CHANGED 2026-07-15: Previously disabled research candidate, now ENABLED for Paper
    auto-trading as part of optimization effort to find positive-expectancy strategies.

    This is a NEW, unvalidated hypothesis distinct from the short-term 15m/4h lane
    in AUTO_PAPER_TECHNICAL_RULES. The current "net expectancy negative" conclusion
    was measured on 15m/4h, NOT on this 1d/4h combination. Medium-term holding periods
    may have better expectancy due to:
    - Wider stops (ATR × 2.5) reduce noise-based stop-outs
    - Daily timeframe trends more stable than 15m
    - Lower trading frequency = lower cumulative transaction costs
    - Less competition with HFT algorithms on 1d/4h timeframes

    Creates full Paper Run with BacktestRun validation gate for auto-trading.
    Will evaluate after 7-14 days / 30+ trades minimum sample.
    """
    return _ensure_auto_paper_run(
        strategy_key=AUTO_PAPER_SWING_KEY,
        runtime_key=AUTO_PAPER_SWING_KEY,
        rules=AUTO_PAPER_SWING_RULES,
        risk_profile_id=MEDIUM_RISK_PROFILE_KEY,
        auto_schedule_enabled=False,
        strategy_lane="swing",
        core_thesis=(
            "Medium-term swing trading: 1d direction + 4h entry, designed for lower "
            "turnover and less competition with HFT algorithms. This is a NEW hypothesis "
            "distinct from the short-term 4h/15m combination."
        ),
    )


def bootstrap_seed_multi_timeframe_ohlcv() -> int:
    """Seed 15m/4h bars so directional auto-cycle gatekeeper freshness checks pass."""
    if not binance_credentials_configured():
        return 0

    from services.data.binance import BinanceCcxtClient
    from services.data.repository import DataRepository
    from services.data.universe import AUTO_PAPER_RESEARCH_SYMBOLS
    from services.database import get_session_factory

    written_total = 0
    timeframes = ("1m", "15m", "4h", "1d")
    with get_session_factory()() as session:
        repo = DataRepository(session)
        client = BinanceCcxtClient()
        for symbol in AUTO_PAPER_RESEARCH_SYMBOLS:
            for timeframe in timeframes:
                try:
                    bars = client.fetch_recent_ohlcv(symbol=symbol, timeframe=timeframe, limit=120)
                    written_total += repo.store_ohlcv_bars(bars)
                except Exception as exc:
                    logger.warning("ohlcv seed skipped for %s %s: %s", symbol, timeframe, exc)
        session.commit()
    if written_total:
        logger.info("seeded %s multi-timeframe ohlcv bar(s) for auto-cycle", written_total)
    return written_total


def bootstrap_pause_legacy_paper_runs() -> int:
    """Leave only directional and observation PaperRuns eligible for scheduling."""
    from services.database import get_session_factory
    from services.strategy_library import PaperRunRepository

    paused = 0
    with get_session_factory()() as session:
        repo = PaperRunRepository(session)
        for run in repo.list_paper_runs():
            if run.paper_status != "running":
                continue
            runtime_key = run.execution_profile.get("auto_paper_runtime_key")
            if runtime_key in {AUTO_PAPER_TECHNICAL_KEY, SIGNAL_OBSERVATION_RUNTIME_KEY}:
                continue
            profile = {**run.execution_profile, "auto_schedule_enabled": False}
            repo.update_paper_run(
                run.paper_run_id or "",
                paper_status="paused",
                execution_profile=profile,
            )
            paused += 1
        session.commit()
    if paused:
        logger.info("paused %s legacy or retired paper run(s)", paused)
    return paused


def bootstrap_poll_information_sources() -> dict[str, Any]:
    """One-shot C/B/D source poll for local in-process scheduler mode."""
    from services.data.tasks import poll_macro_calendar, poll_news_feeds, poll_social_watchlist

    summary: dict[str, Any] = {}
    for name, runner in (
        ("poll_news_feeds", poll_news_feeds.run),
        ("poll_macro_calendar", poll_macro_calendar.run),
        ("poll_social_watchlist", poll_social_watchlist.run),
    ):
        try:
            summary[name] = runner()
        except Exception as exc:  # pragma: no cover - defensive startup guard
            logger.warning("%s bootstrap failed: %s", name, exc)
            summary[name] = {"error": str(exc)}
    logger.info("information source bootstrap complete: %s", summary)
    return summary


def bootstrap_local_paper_runtime(*, seed_ohlcv: bool = True) -> None:
    bootstrap_paper_testnet_mirror()
    bootstrap_auto_trading_paper_run()
    bootstrap_auto_trading_technical_paper_run()
    bootstrap_signal_observation_strategy()
    bootstrap_operator_experience_strategy()
    bootstrap_cross_sectional_carry_strategy()
    bootstrap_pause_legacy_paper_runs()
    bootstrap_clear_stale_blocking_risk_events()
    if seed_ohlcv:
        bootstrap_seed_multi_timeframe_ohlcv()
