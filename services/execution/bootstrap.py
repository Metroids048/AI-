"""Local runtime bootstrap helpers for Paper/Testnet integration."""

from __future__ import annotations

import logging
from typing import Any

from services.data.service import DEFAULT_BINANCE_TOP20
from services.data.universe import fixed_top20_assets
from services.execution.risk_tiers import default_asset_risk_tiers
from shared.config import settings
from shared.models.risk import MEDIUM_RISK_PROFILE_KEY, medium_risk_profile

logger = logging.getLogger(__name__)

AUTO_PAPER_RUNTIME_KEY = "auto_paper_btc_funding"
AUTO_PAPER_TECHNICAL_KEY = "auto_paper_mature_templates"
OPERATOR_EXPERIENCE_STRATEGY_KEY = "operator_experience_4h_15m_v1"

# Medium-risk auto-trading preset for Top20 + funding carry admission.
AUTO_PAPER_STRATEGY_RULES: dict[str, Any] = {
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
        "risk_per_trade": 0.01,
        "max_leverage": 10,
        "max_position_fraction": 0.15,
        "min_notional_usdt": 20,
    },
}

AUTO_PAPER_TECHNICAL_RULES: dict[str, Any] = {
    "entry_rules": {
        "technical_pipeline": True,
        "strategy_lanes": ["trend_breakout", "mean_reversion", "volatility_filtered_breakout"],
        "timeframe_model": "validated_template_1h",
        "entry_timeframe": "1h",
        "enabled_signals": [
            "macd",
            "dow_trend",
            "ema_trend",
            "adx",
            "price_action",
            "rsi",
            "vwap",
            "bollinger",
        ],
        "meta_label_min_win_rate": 0.50,
    },
    "exit_rules": {"close_on_opposite_signal": True, "max_hold_bars": 96},
    "stoploss_rules": {"atr_multiple": 2.0, "fixed_bps": 250},
    "takeprofit_rules": {"risk_reward": 2.5, "trail_after_r": 1.5},
    "position_rules": {
        "risk_per_trade": 0.01,
        "max_leverage": 10,
        "max_position_fraction": 0.15,
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


def binance_credentials_configured() -> bool:
    return bool(settings.binance_api_key and settings.binance_api_secret)


def default_mirror_to_gateway() -> bool:
    """Default new auto runs to Binance simulation-first only in the safe testnet boundary."""
    return bool(
        binance_credentials_configured()
        and settings.binance_auto_execute
        and settings.binance_use_testnet
        and not settings.live_trading_enabled
    )


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

        universe_assets = [asset.model_dump(mode="json") for asset in fixed_top20_assets()]
        strategy_lanes = ["carry"] if strategy_lane == "carry" else list(rules["entry_rules"].get("strategy_lanes", []))
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
            "strategy_lanes": strategy_lanes,
            "account_equity": 10_000,
            "equity_peak": 10_000,
            "execution_mode": "binance_simulation_first" if default_mirror_to_gateway() else "paper_only",
            "mirror_to_gateway": default_mirror_to_gateway(),
            "risk_profile_id": risk_profile_id,
            "max_leverage": rules["position_rules"]["max_leverage"],
            "asset_risk_tiers": default_asset_risk_tiers(),
            "max_symbols": 20,
            "universe_mode": "fixed_top20",
            "universe_assets": universe_assets,
            "llm_veto_enabled": True,
            "market_intelligence_enabled": True,
        }
        if paper_run is None:
            paper_run = paper_repo.create_paper_run(
                PaperRun(
                    strategy_id=strategy.strategy_id,
                    symbol_scope=list(DEFAULT_BINANCE_TOP20),
                    candidate_symbols=list(DEFAULT_BINANCE_TOP20),
                    selection_basis="fixed_operator_top20",
                    gate_decision_ref=backtest.backtest_run_id,
                    execution_profile=execution_profile,
                    paper_status="running",
                )
            )
        else:
            profile = {**dict(paper_run.execution_profile), **execution_profile}
            paper_run = (
                paper_repo.update_paper_run(
                    paper_run.paper_run_id or "",
                    execution_profile=profile,
                    paper_status="running",
                    candidate_symbols=list(DEFAULT_BINANCE_TOP20),
                    symbol_scope=list(DEFAULT_BINANCE_TOP20),
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
    )


def bootstrap_auto_trading_technical_paper_run() -> str | None:
    """Ensure a running mature-template PaperRun exists for technical + LLM veto cycles."""
    if not binance_credentials_configured():
        logger.info("auto technical paper run bootstrap skipped: binance credentials not configured")
        return None

    risk_profile_id = bootstrap_medium_risk_profile()
    return _ensure_auto_paper_run(
        runtime_key=AUTO_PAPER_TECHNICAL_KEY,
        strategy_key=AUTO_PAPER_TECHNICAL_KEY,
        strategy_lane="directional",
        core_thesis=(
            "Local auto-cycle bootstrap strategy. Scans the fixed operator Binance USDT-M Top20 through "
            "mature template lanes: funding/carry, trend breakout, mean reversion, and "
            "volatility-filtered breakout. Operator 4h/15m experience logic is kept as a disabled "
            "research candidate, not the default auto lane."
        ),
        rules=AUTO_PAPER_TECHNICAL_RULES,
        risk_profile_id=risk_profile_id,
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


def bootstrap_seed_multi_timeframe_ohlcv() -> int:
    """Seed 15m/4h bars so directional auto-cycle gatekeeper freshness checks pass."""
    if not binance_credentials_configured():
        return 0

    from services.data.binance import BinanceCcxtClient
    from services.data.repository import DataRepository
    from services.data.service import DEFAULT_BINANCE_TOP20
    from services.database import get_session_factory

    written_total = 0
    timeframes = ("1m", "15m", "4h")
    with get_session_factory()() as session:
        repo = DataRepository(session)
        client = BinanceCcxtClient()
        for symbol in DEFAULT_BINANCE_TOP20:
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
    """Pause old manual and explicitly retired PaperRuns that duplicate active lanes."""
    from services.database import get_session_factory
    from services.strategy_library import PaperRunRepository

    paused = 0
    with get_session_factory()() as session:
        repo = PaperRunRepository(session)
        for run in repo.list_paper_runs():
            if run.paper_status != "running":
                continue
            runtime_key = run.execution_profile.get("auto_paper_runtime_key")
            if runtime_key and runtime_key != "auto_paper_btc_technical":
                continue
            repo.update_paper_run(run.paper_run_id or "", paper_status="paused")
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
    bootstrap_operator_experience_strategy()
    bootstrap_pause_legacy_paper_runs()
    bootstrap_clear_stale_blocking_risk_events()
    if seed_ohlcv:
        bootstrap_seed_multi_timeframe_ohlcv()
