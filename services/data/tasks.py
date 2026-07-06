"""Celery task entrypoints for data ingestion."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from celery import shared_task

from services.data.binance import BinanceBackfillService
from services.data.heartbeat import MarketDataHeartbeatService
from services.data.macro_calendar import MacroCalendarService
from services.data.news import NewsIngestionService
from services.data.repository import DataRepository
from services.data.social import SocialIngestionService
from services.database import get_session_factory
from services.strategy_library import AgentTaskRepository, IngestionRepository, ReviewRepository, StrategyRepository
from shared.models import IngestionJob

from .service import IngestionService


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    raise TypeError(f"unsupported datetime value: {value!r}")


def _result_summary(results) -> dict:
    return {
        "results": [
            {
                "symbol": item.symbol,
                "timeframe": item.timeframe,
                "rows_fetched": item.rows_fetched,
                "rows_written": item.rows_written,
                "start_at": item.start_at.isoformat(),
                "end_at": item.end_at.isoformat(),
            }
            for item in results
        ],
        "rows_written_total": sum(item.rows_written for item in results),
    }


@shared_task(name="services.data.tasks.enqueue_binance_ingestion", queue="ingestion_queue")
def enqueue_binance_ingestion(job_payload: dict, *, client=None) -> dict:
    """Prepare, persist, and execute first-tranche Binance market-data jobs."""

    session = get_session_factory()()
    try:
        job = IngestionService().prepare_job(IngestionJob(**job_payload))
        ingestion_repo = IngestionRepository(session)
        created = ingestion_repo.create_job(job)
        if created.ingestion_job_id is None:
            raise RuntimeError("persisted ingestion job is missing ingestion_job_id")
        job_id = created.ingestion_job_id
        if created.job_type not in {
            "binance_ohlcv_backfill",
            "binance_funding_backfill",
            "binance_live_market_collector",
        }:
            return created.model_dump(mode="json")

        ingestion_repo.update_job(job_id, job_status="running")
        input_window = created.input_window or {}
        try:
            if created.job_type == "binance_live_market_collector":
                updated = ingestion_repo.update_job(
                    job_id,
                    job_status="running",
                    execution_summary={
                        **created.execution_summary,
                        "collector": "binance_public_ws",
                        "note": "Run BinanceLiveMarketCollector as a long-lived worker process.",
                    },
                )
                return updated.model_dump(mode="json") if updated else created.model_dump(mode="json")

            service = BinanceBackfillService(
                data_repo=DataRepository(session),
                client=client,
            )
            start_at = _parse_datetime(input_window.get("start_at"))
            end_at = _parse_datetime(input_window.get("end_at"))
            if created.job_type == "binance_ohlcv_backfill":
                symbols = created.target_symbols or ["BTC/USDT", "BTC/USDT:USDT"]
                results = service.backfill_ohlcv(
                    symbols=symbols,
                    timeframe=input_window.get("timeframe", "1h"),
                    start_at=start_at,
                    end_at=end_at,
                )
            else:
                symbols = created.target_symbols or ["BTC/USDT:USDT"]
                results = service.backfill_funding(
                    symbols=symbols,
                    start_at=start_at,
                    end_at=end_at,
                )
            updated = ingestion_repo.update_job(
                job_id,
                job_status="succeeded",
                output_ref="timescale://ohlcv_bars,market_extras",
                execution_summary={
                    **created.execution_summary,
                    **_result_summary(results),
                },
            )
            return updated.model_dump(mode="json") if updated else created.model_dump(mode="json")
        except Exception as exc:
            ingestion_repo.update_job(
                job_id,
                job_status="failed_fetch",
                error_summary=str(exc),
            )
            raise
    finally:
        session.close()


@shared_task(name="services.data.tasks.market_data_heartbeat", queue="ops_queue")
def market_data_heartbeat(symbols: list[str] | None = None, timeframe: str = "1m") -> dict:
    session = get_session_factory()()
    try:
        return MarketDataHeartbeatService(data_repo=DataRepository(session)).check_symbols(
            symbols=symbols or ["BTC/USDT"],
            timeframe=timeframe,
        )
    finally:
        session.close()


@shared_task(name="services.data.tasks.poll_news_feeds", queue="ingestion_queue")
def poll_news_feeds() -> dict:
    session = get_session_factory()()
    try:
        service = NewsIngestionService(
            data_repo=DataRepository(session),
            agent_repo=AgentTaskRepository(session),
            strategy_repo=StrategyRepository(session),
            review_repo=ReviewRepository(session),
        )
        return service.poll_configured_feeds()
    finally:
        session.close()


@shared_task(name="services.data.tasks.poll_macro_calendar", queue="ingestion_queue")
def poll_macro_calendar() -> dict:
    session = get_session_factory()()
    try:
        return MacroCalendarService(data_repo=DataRepository(session)).poll_configured_sources()
    finally:
        session.close()


@shared_task(name="services.data.tasks.poll_social_watchlist", queue="ingestion_queue")
def poll_social_watchlist() -> dict:
    session = get_session_factory()()
    try:
        news_service = NewsIngestionService(
            data_repo=DataRepository(session),
            agent_repo=AgentTaskRepository(session),
            strategy_repo=StrategyRepository(session),
            review_repo=ReviewRepository(session),
        )
        return SocialIngestionService(news_service=news_service).poll_twitter_watchlist()
    finally:
        session.close()
