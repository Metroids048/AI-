"""Celery task entrypoints for data ingestion."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
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


_HEARTBEAT_TIMEFRAMES = ("1m", "15m", "1h", "4h", "1d")
_HEARTBEAT_MAX_AGE_SECONDS = {
    "1m": 120,
    "15m": 20 * 60,
    "1h": 80 * 60,
    "4h": 5 * 60 * 60,
    "1d": 28 * 60 * 60,  # 28 hours tolerance for daily candles
}
_SECONDARY_TIMEFRAME_INDEX = 0


def _heartbeat_timeframes_to_refresh(
    *, data_repo: DataRepository, symbol: str, primary_timeframe: str, secondary_timeframe: str | None
) -> list[str]:
    """Refresh the primary feed every cycle and one due higher frame per cycle.

    The old behavior requested all higher frames for every symbol at once,
    creating a Testnet burst.  A secondary frame is now rotated across cycles:
    1m remains fresh for execution health while 1h/15m/4h restore gradually.
    """

    due = [primary_timeframe]
    now = datetime.now(UTC)
    if secondary_timeframe is None or secondary_timeframe == primary_timeframe:
        return due
    freshness = data_repo.check_freshness(
        symbol=symbol,
        timeframe=secondary_timeframe,
        reference_time=now,
        max_delay=timedelta(seconds=_HEARTBEAT_MAX_AGE_SECONDS[secondary_timeframe]),
    )
    if not freshness["is_fresh"]:
        due.append(secondary_timeframe)
    return due


def _rate_limit_retry_after_seconds(error: Exception) -> int | None:
    """Extract Binance's ban-until timestamp from an HTTP 418 response."""

    message = str(error)
    if "418" not in message or "banned until" not in message:
        return None
    match = re.search(r"banned until\s+(\d{13})", message)
    if match is None:
        return None
    ban_until_ms = int(match.group(1))
    remaining = (ban_until_ms / 1000) - datetime.now(UTC).timestamp()
    return max(1, int(remaining) + 1)


@shared_task(name="services.data.tasks.market_data_heartbeat", queue="ops_queue")
def market_data_heartbeat(symbols: list[str] | None = None, timeframe: str = "1m") -> dict:
    from services.data.binance import BinanceCcxtClient, resolve_usdm_public_rest_base

    session = get_session_factory()()
    try:
        data_repo = DataRepository(session)
        from services.data.service import DEFAULT_BINANCE_TOP20

        target_symbols = list(symbols or DEFAULT_BINANCE_TOP20)
        global _SECONDARY_TIMEFRAME_INDEX
        secondary_candidates = [candidate for candidate in _HEARTBEAT_TIMEFRAMES if candidate != timeframe]
        secondary_timeframe = secondary_candidates[_SECONDARY_TIMEFRAME_INDEX % len(secondary_candidates)]
        _SECONDARY_TIMEFRAME_INDEX += 1
        client = BinanceCcxtClient(usdm_base_url=resolve_usdm_public_rest_base())
        failures: dict[str, dict[str, str | int]] = {}
        retry_after_seconds: int | None = None
        # Refresh bars before stale checks.  The 1m feed is refreshed every
        # heartbeat; 15m/4h are refreshed only when they become stale.
        for symbol in target_symbols:
            for tf in _heartbeat_timeframes_to_refresh(
                data_repo=data_repo,
                symbol=symbol,
                primary_timeframe=timeframe,
                secondary_timeframe=secondary_timeframe,
            ):
                try:
                    bars = client.fetch_recent_usdm_ohlcv(symbol=symbol, timeframe=tf, limit=60)
                    data_repo.store_ohlcv_bars(bars)
                except Exception as exc:  # noqa: BLE001 - preserve exchange evidence for the ops UI
                    retry_after_seconds = _rate_limit_retry_after_seconds(exc)
                    failures[f"{symbol}:{tf}"] = {
                        "error": str(exc),
                        **({"retry_after_seconds": retry_after_seconds} if retry_after_seconds else {}),
                    }
                    if retry_after_seconds is not None:
                        # A ban applies to the full endpoint/IP, so continuing
                        # the loop only creates noise and can extend the ban.
                        break
            if retry_after_seconds is not None:
                break
        session.commit()
        result = MarketDataHeartbeatService(data_repo=data_repo).check_symbols(
            symbols=target_symbols,
            timeframe=timeframe,
        )
        if failures:
            result["refresh_failures"] = failures
        if retry_after_seconds is not None:
            result["status"] = "rate_limited"
            result["retry_after_seconds"] = retry_after_seconds
        result["secondary_timeframe"] = secondary_timeframe
        return result
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
