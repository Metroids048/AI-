"""Celery tasks for paper-run orchestration."""

from __future__ import annotations

from celery import shared_task

from services.data import DataRepository
from services.database import get_session_factory
from services.execution.gatekeeper import ExecutionGatekeeperService
from services.execution.paper import PaperOrchestrationService
from services.execution.paper_runtime import PaperRuntimeService
from services.strategy_library import (
    ExecutionRepository,
    HypothesisRepository,
    PaperRunRepository,
    ReviewRepository,
    RiskProfileRepository,
    StrategyRepository,
    ValidationRepository,
)


@shared_task(name="services.execution.tasks.enqueue_paper_run", queue="paper_queue")
def enqueue_paper_run(run_payload: dict) -> str:
    from shared.models import PaperRun

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
