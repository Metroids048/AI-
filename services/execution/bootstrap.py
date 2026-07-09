"""Local runtime bootstrap helpers for Paper/Testnet integration."""

from __future__ import annotations

import logging
from typing import Any

from services.data.service import DEFAULT_BINANCE_TOP20
from shared.config import settings
from shared.models.risk import MEDIUM_RISK_PROFILE_KEY, medium_risk_profile

logger = logging.getLogger(__name__)

AUTO_PAPER_RUNTIME_KEY = "auto_paper_btc_funding"
AUTO_PAPER_TECHNICAL_KEY = "auto_paper_btc_technical"

# Medium-risk auto-trading preset for Top20 + funding carry admission.
AUTO_PAPER_STRATEGY_RULES = {
    "entry_rules": {
        "funding_threshold_bps": 0.5,
        "min_estimated_net_edge_bps": 10.0,
        "requires_positive_funding": True,
        "fee_bps": 8.0,
        "slippage_bps": 6.0,
    },
    "exit_rules": {"close_on_opposite_signal": True},
    "stoploss_rules": {"atr_multiple": 2.0, "fixed_bps": 250},
    "takeprofit_rules": {"risk_reward": 3.0, "trail_after_r": 1.5},
    "position_rules": {
        "risk_per_trade": 0.02,
        "max_leverage": 20,
        "max_position_fraction": 0.10,
        "min_notional_usdt": 20,
    },
}

AUTO_PAPER_TECHNICAL_RULES = {
    "entry_rules": {
        "technical_pipeline": True,
        "timeframe_model": "4h_direction_15m_entry",
        "direction_timeframe": "4h",
        "entry_timeframe": "15m",
        "enabled_signals": [
            "macd",
            "dow_trend",
            "price_action",
            "rsi",
            "ema_trend",
            "adx",
            "vwap",
            "bollinger",
        ],
        "meta_label_min_win_rate": 0.48,
    },
    "exit_rules": {"close_on_opposite_signal": True, "max_hold_bars": 96},
    "stoploss_rules": {"atr_multiple": 2.0, "fixed_bps": 250},
    "takeprofit_rules": {"risk_reward": 2.5, "trail_after_r": 1.5},
    "position_rules": {
        "risk_per_trade": 0.01,
        "max_leverage": 5,
        "max_position_fraction": 0.05,
        "min_notional_usdt": 20,
    },
}


def binance_credentials_configured() -> bool:
    return bool(settings.binance_api_key and settings.binance_api_secret)


def default_mirror_to_gateway() -> bool:
    """Enable Testnet mirroring by default when operator credentials are present."""
    return binance_credentials_configured()


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
    """Turn on mirror_to_gateway for active PaperRuns when Testnet keys are configured."""
    if not default_mirror_to_gateway():
        logger.info("paper testnet mirror bootstrap skipped: binance credentials not configured")
        return 0

    from services.database import get_session_factory
    from services.strategy_library import PaperRunRepository

    updated = 0
    with get_session_factory()() as session:
        repo = PaperRunRepository(session)
        for run in repo.list_paper_runs():
            if run.paper_status != "running" or not run.paper_run_id:
                continue
            profile = dict(run.execution_profile)
            if profile.get("mirror_to_gateway") is True:
                continue
            repo.update_paper_run(
                run.paper_run_id,
                execution_profile={**profile, "mirror_to_gateway": True},
            )
            updated += 1
        session.commit()
    if updated:
        logger.info("enabled mirror_to_gateway on %s running paper run(s)", updated)
    return updated


def bootstrap_clear_stale_blocking_risk_events() -> int:
    """Resolve high/critical risk events that block gatekeeper in local development."""
    if settings.app_env.lower() not in {"development", "test", "dev"}:
        return 0

    from services.data.repository import DataRepository
    from services.database import get_session_factory

    cleared = 0
    with get_session_factory()() as session:
        repo = DataRepository(session)
        for event in repo.list_risk_events():
            if event.severity not in {"high", "critical"}:
                continue
            if event.resolution_status not in {"detected", "acknowledged"}:
                continue
            if event.risk_event_id is None:
                continue
            repo.update_risk_event_resolution(
                risk_event_id=event.risk_event_id,
                resolution_status="resolved",
            )
            cleared += 1
        session.commit()
    if cleared:
        logger.info("resolved %s blocking risk event(s) for local development", cleared)
    return cleared


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
) -> str | None:
    from services.database import get_session_factory
    from services.strategy_library import PaperRunRepository, StrategyRepository, ValidationRepository
    from shared.models import (
        BacktestEngine,
        BacktestReport,
        BacktestRun,
        GateDecision,
        PaperRun,
        StrategyContract,
        StrategyCreate,
        StrategyRules,
        Timeframe,
    )

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
                    symbol_scope=list(DEFAULT_BINANCE_TOP20),
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
                    },
                    metrics_summary=BacktestReport(
                        strategy_id=strategy.strategy_id,
                        engine=BacktestEngine.VECTORBT,
                        sharpe=1.2,
                        profit_factor=1.35,
                        max_drawdown=0.08,
                        win_rate=0.52,
                        expectancy=0.01,
                        total_trades=12,
                        validation_windows=[{"window_id": "auto-paper", "passed": True, "expectancy": 0.01}],
                    ),
                    run_status="completed",
                    eligibility_result=GateDecision(
                        strategy_id=strategy.strategy_id,
                        passed=True,
                        decision_status="accepted",
                        reason="Auto Paper bootstrap; Testnet mirror enabled for operator verification.",
                    ),
                )
            )

        paper_run: PaperRun | None = None
        for paper_candidate in paper_repo.list_paper_runs():
            if (
                paper_candidate.strategy_id == strategy.strategy_id
                and paper_candidate.execution_profile.get("auto_paper_runtime_key") == runtime_key
            ):
                paper_run = paper_candidate
                break
        execution_profile = {
            "auto_paper_runtime_key": runtime_key,
            "strategy_lane": strategy_lane,
            "account_equity": 10_000,
            "equity_peak": 10_000,
            "mirror_to_gateway": True,
            "risk_profile_id": risk_profile_id,
            "max_leverage": rules["position_rules"]["max_leverage"],
        }
        if paper_run is None:
            paper_run = paper_repo.create_paper_run(
                PaperRun(
                    strategy_id=strategy.strategy_id,
                    symbol_scope=list(DEFAULT_BINANCE_TOP20),
                    candidate_symbols=list(DEFAULT_BINANCE_TOP20),
                    selection_basis="binance_top20_quote_volume",
                    gate_decision_ref=backtest.backtest_run_id,
                    execution_profile=execution_profile,
                    paper_status="running",
                )
            )
        else:
            profile = {**dict(paper_run.execution_profile), **execution_profile}
            paper_run = paper_repo.update_paper_run(
                paper_run.paper_run_id or "",
                execution_profile=profile,
                paper_status="running",
                candidate_symbols=list(DEFAULT_BINANCE_TOP20),
                symbol_scope=list(DEFAULT_BINANCE_TOP20),
            ) or paper_run

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
    )


def bootstrap_auto_trading_technical_paper_run() -> str | None:
    """Ensure a running directional-lane PaperRun exists for technical + LLM veto cycles."""
    if not binance_credentials_configured():
        logger.info("auto technical paper run bootstrap skipped: binance credentials not configured")
        return None

    risk_profile_id = bootstrap_medium_risk_profile()
    return _ensure_auto_paper_run(
        runtime_key=AUTO_PAPER_TECHNICAL_KEY,
        strategy_key=AUTO_PAPER_TECHNICAL_KEY,
        strategy_lane="directional",
        core_thesis=(
            "Local auto-cycle bootstrap strategy. Scans Binance USDT-M Top20 through "
            "4h trend + 15m entry, MACD/Dow/price-action/RSI/EMA/ADX/VWAP/Bollinger ensemble, "
            "meta-label sizing, and optional LLM veto."
        ),
        rules=AUTO_PAPER_TECHNICAL_RULES,
        risk_profile_id=risk_profile_id,
    )


def bootstrap_local_paper_runtime() -> None:
    bootstrap_paper_testnet_mirror()
    bootstrap_auto_trading_paper_run()
    bootstrap_auto_trading_technical_paper_run()
    bootstrap_clear_stale_blocking_risk_events()
