"""Celery tasks for paper-run orchestration."""

from __future__ import annotations

from celery import shared_task

from services.database import get_session_factory
from services.execution.paper import PaperOrchestrationService
from services.strategy_library import PaperRunRepository


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
