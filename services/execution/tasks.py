"""Celery tasks for paper-run orchestration."""

from __future__ import annotations

from celery import shared_task

from services.data import DataRepository
from services.database import get_session_factory
from services.execution.gatekeeper import ExecutionGatekeeperService
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
from shared.models import PaperRun, RiskEvent, RiskEventType, RiskProfile, RiskSeverity


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
