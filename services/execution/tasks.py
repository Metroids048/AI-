"""Celery tasks for paper-run orchestration."""

from __future__ import annotations

from datetime import UTC, datetime

from celery import shared_task

from services.data import DataRepository
from services.database import get_session_factory
from services.execution.gatekeeper import ExecutionGatekeeperService
from services.execution.gateway import configured_gateways
from services.execution.paper import PaperOrchestrationService
from services.execution.paper_runtime import PaperRuntimeService
from services.strategy_library import (
    AgentTaskRepository,
    ExecutionRepository,
    HypothesisRepository,
    NotificationRepository,
    PaperRunRepository,
    ReviewRepository,
    RiskProfileRepository,
    StrategyRepository,
    ValidationRepository,
)
from shared.models import NotificationOutboxItem, PaperRun, RiskEvent, RiskEventType, RiskProfile, RiskSeverity


@shared_task(name="services.execution.tasks.enqueue_paper_run", queue="paper_queue")
def enqueue_paper_run(run_payload: dict) -> str:
    session = get_session_factory()()
    try:
        prepared = PaperOrchestrationService().prepare_run(PaperRun(**run_payload))
        created = PaperRunRepository(session).create_paper_run(prepared)
        return created.paper_run_id or ""
    finally:
        session.close()


@shared_task(name="services.execution.tasks.run_paper_runtime_cycle", queue="paper_queue")
def run_paper_runtime_cycle(paper_run_id: str, request_payload: dict | None = None) -> dict:
    from shared.models import PaperRuntimeCycleRequest

    session = get_session_factory()()
    try:
        runtime = PaperRuntimeService(
            data_repo=DataRepository(session),
            execution_repo=ExecutionRepository(session),
            paper_repo=PaperRunRepository(session),
            strategy_repo=StrategyRepository(session),
            agent_repo=AgentTaskRepository(session),
            review_repo=ReviewRepository(session),
            notification_repo=NotificationRepository(session),
            gatekeeper=ExecutionGatekeeperService(
                data_repo=DataRepository(session),
                validation_repo=ValidationRepository(session),
                hypothesis_repo=HypothesisRepository(session),
                risk_profile_repo=RiskProfileRepository(session),
                execution_repo=ExecutionRepository(session),
                paper_repo=PaperRunRepository(session),
                review_repo=ReviewRepository(session),
            ),
            gateway=configured_gateways()[0],
        )
        result = runtime.run_cycle(
            paper_run_id=paper_run_id,
            request=PaperRuntimeCycleRequest(**(request_payload or {})),
        )
        return result.model_dump(mode="json")
    finally:
        session.close()


@shared_task(name="services.execution.tasks.run_all_paper_runtime_cycles", queue="paper_queue")
def run_all_paper_runtime_cycles(request_payload: dict | None = None) -> dict:
    """Run one cycle for every currently running PaperRun."""

    session = get_session_factory()()
    try:
        paper_repo = PaperRunRepository(session)
        runs = [run for run in paper_repo.list_paper_runs() if run.paper_status == "running"]
        results = []
        for run in runs:
            if run.paper_run_id is None:
                continue
            result = run_paper_runtime_cycle.run(run.paper_run_id, request_payload or {})
            results.append(result)
        return {"paper_runs": len(runs), "results": results}
    finally:
        session.close()


@shared_task(name="services.execution.tasks.risk_profile_sweep", queue="ops_queue")
def risk_profile_sweep() -> dict:
    """Evaluate running PaperRun metrics even when no new signal arrives."""

    session = get_session_factory()()
    try:
        paper_repo = PaperRunRepository(session)
        risk_repo = RiskProfileRepository(session)
        data_repo = DataRepository(session)
        profile = risk_repo.list_profiles()[0] if risk_repo.list_profiles() else None
        checked = 0
        events = 0
        for run in paper_repo.list_paper_runs():
            if run.paper_status != "running":
                continue
            checked += 1
            metrics = run.paper_metrics_summary
            account_equity = float(
                metrics.get("account_equity") or run.execution_profile.get("account_equity") or 10_000.0
            )
            equity_peak = float(metrics.get("equity_peak") or account_equity)
            drawdown = max(0.0, (equity_peak - account_equity) / max(equity_peak, 1.0))
            consecutive_losses = int(metrics.get("consecutive_losses", 0))
            api_failures = int(metrics.get("api_failures_window", 0))
            active_profile = profile or RiskProfile()
            reasons = []
            if drawdown >= active_profile.hard_stop_drawdown_limit:
                reasons.append("hard_stop_drawdown_breached")
            elif drawdown >= active_profile.drawdown_limit:
                reasons.append("drawdown_limit_breached")
            if consecutive_losses >= active_profile.consecutive_loss_limit:
                reasons.append("consecutive_loss_limit_breached")
            if api_failures >= active_profile.api_failure_limit:
                reasons.append("api_failure_limit_breached")
            if reasons:
                data_repo.store_risk_event(
                    RiskEvent(
                        event_type=RiskEventType.RISK_LIMIT_BREACH,
                        severity=RiskSeverity.HIGH,
                        source="risk_profile_sweep",
                        description=f"Paper run {run.paper_run_id} breached risk sweep: {', '.join(reasons)}",
                        affected_scope=run.candidate_symbols or run.symbol_scope,
                        recommended_action="pause_strategy",
                    )
                )
                events += 1
        return {"checked_paper_runs": checked, "risk_events": events}
    finally:
        session.close()


@shared_task(name="services.execution.tasks.refresh_volatility_asset_risk_tiers", queue="ops_queue")
def refresh_volatility_asset_risk_tiers(*, lookback_days: int = 30) -> dict:
    """Weekly: recompute Top20 ATR% tercile tiers and write into running PaperRuns."""

    from datetime import UTC, datetime, timedelta

    from services.data.service import DEFAULT_BINANCE_TOP20
    from services.execution.risk_tiers import (
        atr_pct_from_daily_bars,
        build_volatility_asset_risk_tiers,
        volatility_tier_meta,
    )

    session = get_session_factory()()
    try:
        data_repo = DataRepository(session)
        paper_repo = PaperRunRepository(session)
        end_at = datetime.now(UTC)
        start_at = end_at - timedelta(days=lookback_days + 5)
        scores: dict[str, float] = {}
        missing: list[str] = []
        for symbol in DEFAULT_BINANCE_TOP20:
            bars = data_repo.list_ohlcv_bars(
                symbol=symbol,
                timeframe="1d",
                start_at=start_at,
                end_at=end_at,
            )
            atr_pct = atr_pct_from_daily_bars(bars)
            if atr_pct is None:
                missing.append(symbol)
                continue
            scores[symbol] = atr_pct
        if not scores:
            return {
                "updated_paper_runs": 0,
                "scored_symbols": 0,
                "missing_symbols": missing,
                "skipped_reason": "insufficient_daily_bars",
            }
        tiers = build_volatility_asset_risk_tiers(scores)
        meta = volatility_tier_meta(scores, lookback_days=lookback_days)
        updated = 0
        for run in paper_repo.list_paper_runs():
            if run.paper_status != "running" or run.paper_run_id is None:
                continue
            profile = dict(run.execution_profile or {})
            profile["asset_risk_tiers"] = tiers
            profile["volatility_tier_meta"] = meta
            paper_repo.update_paper_run(run.paper_run_id, execution_profile=profile)
            updated += 1
        session.commit()
        return {
            "updated_paper_runs": updated,
            "scored_symbols": len(scores),
            "missing_symbols": missing,
            "tiers": {name: tiers[name]["symbols"] for name in ("vol_low", "vol_mid", "vol_high")},
        }
    finally:
        session.close()


@shared_task(name="services.execution.tasks.refresh_signal_edge_stats", queue="ops_queue")
def refresh_signal_edge_stats(
    *,
    strategy_key: str | None = None,
    days: int = 60,
    min_trade_samples: int = 30,
    max_age_days: int = 30,
    reuse_stored_data: bool = True,
) -> dict:
    """Weekly: replay real entry/exit rules over persisted OHLCV history and
    refresh the net_edge_after_cost real-edge-stats artifact so the gate stops
    relying solely on the raw-bar-return proxy.

    `reuse_stored_data` defaults to True since OHLCV ingestion already runs on
    its own schedule (services.data.tasks.enqueue_binance_ingestion) -- this
    task should not also hit Binance directly on every weekly run.
    """

    from scripts.compute_signal_edge_stats import compute_and_write_edge_stats
    from services.execution.bootstrap import AUTO_PAPER_TECHNICAL_KEY
    from services.strategy_library import NotificationRepository

    resolved_strategy_key = strategy_key or AUTO_PAPER_TECHNICAL_KEY

    result = compute_and_write_edge_stats(
        strategy_key=resolved_strategy_key,
        days=days,
        min_trade_samples=min_trade_samples,
        max_age_days=max_age_days,
        reuse_stored_data=reuse_stored_data,
    )

    session = get_session_factory()()
    try:
        today = datetime.now(UTC).date().isoformat()
        if result.accepted:
            event_type = "signal_edge_stats_refreshed"
            severity = "low"
            subject = f"Signal edge stats refreshed for {resolved_strategy_key}"
            body = (
                f"Weekly replay produced {result.total_trades} real trade samples "
                f"(win_rate={result.win_rate:.4f}, average_win={result.average_win:.6f}, "
                f"average_loss={result.average_loss:.6f}). net_edge_after_cost will use "
                f"this real-edge artifact instead of the raw-bar-return proxy."
            )
        else:
            event_type = "signal_edge_stats_rejected"
            severity = "medium"
            subject = f"Signal edge stats refresh rejected for {resolved_strategy_key}"
            body = (
                f"Weekly refresh found only {result.total_trades} real trade samples "
                f"(< required minimum {result.min_trade_samples}). net_edge_after_cost "
                "keeps using the raw-bar-return proxy for this strategy."
            )
        NotificationRepository(session).create_notification(
            NotificationOutboxItem(
                notification_id=f"{event_type}:{resolved_strategy_key}:{today}",
                event_type=event_type,
                severity=severity,
                subject=subject,
                body=body,
                source_ref=f"strategy:{resolved_strategy_key}",
            )
        )
        return {
            "accepted": result.accepted,
            "strategy_key": result.strategy_key,
            "total_trades": result.total_trades,
            "win_rate": result.win_rate,
            "average_win": result.average_win,
            "average_loss": result.average_loss,
            "artifact_path": result.artifact_path,
        }
    finally:
        session.close()
