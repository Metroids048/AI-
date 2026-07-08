"""Market-data freshness heartbeat shared by scheduler and health checks."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from shared.config import settings
from services.data.repository import DataRepository
from shared.models import RiskEvent, RiskEventType, RiskSeverity


class MarketDataHeartbeatService:
    """Evaluate A-level data freshness and write stale-data RiskEvents."""

    def __init__(self, *, data_repo: DataRepository) -> None:
        self.data_repo = data_repo

    def check_symbol(
        self,
        *,
        symbol: str,
        timeframe: str = "1m",
        max_delay_seconds: int | None = None,
    ) -> dict:
        max_delay = timedelta(seconds=max_delay_seconds or settings.market_data_stale_seconds)
        now = datetime.now(UTC)
        freshness = self.data_repo.check_freshness(
            symbol=symbol,
            timeframe=timeframe,
            reference_time=now,
            max_delay=max_delay,
        )
        if not freshness["is_fresh"]:
            event = self.data_repo.store_risk_event(
                RiskEvent(
                    event_type=RiskEventType.DATA_STALE,
                    severity=RiskSeverity.HIGH,
                    source="market_data_heartbeat",
                    description=f"{symbol} {timeframe} market data is stale",
                    affected_scope=[symbol],
                    recommended_action="pause_strategy",
                    occurred_at=now,
                )
            )
            freshness["risk_event_id"] = event.risk_event_id
        return freshness

    def check_symbols(
        self,
        *,
        symbols: list[str],
        timeframe: str = "1m",
        max_delay_seconds: int | None = None,
    ) -> dict:
        results = {
            symbol: self.check_symbol(symbol=symbol, timeframe=timeframe, max_delay_seconds=max_delay_seconds)
            for symbol in symbols
        }
        return {
            "checked_symbols": symbols,
            "timeframe": timeframe,
            "stale_symbols": [symbol for symbol, result in results.items() if not result["is_fresh"]],
            "results": results,
        }
