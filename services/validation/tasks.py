"""Celery tasks for persisted backtest runs."""

from __future__ import annotations

from celery import shared_task

from services.data import DataRepository
from services.database import get_session_factory
from services.strategy_library import StrategyRepository, ValidationRepository
from services.validation import CarryBacktestApplicationService


@shared_task(name="services.validation.tasks.enqueue_backtest_run", queue="backtest_queue")
def enqueue_backtest_run(run_payload: dict) -> str:
    from shared.models import BacktestRun

    session = get_session_factory()()
    try:
        created = ValidationRepository(session).create_backtest_run(BacktestRun(**run_payload))
        return created.backtest_run_id or ""
    finally:
        session.close()


@shared_task(name="services.validation.tasks.enqueue_carry_backtest", queue="backtest_queue")
def enqueue_carry_backtest(request_payload: dict) -> str:
    from shared.models import CarryBacktestRequest

    session = get_session_factory()()
    try:
        created = CarryBacktestApplicationService(
            strategy_repo=StrategyRepository(session),
            validation_repo=ValidationRepository(session),
            data_repo=DataRepository(session),
        ).submit(CarryBacktestRequest(**request_payload))
        return created.backtest_run_id or ""
    finally:
        session.close()
