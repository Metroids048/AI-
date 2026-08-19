"""Market-data freshness heartbeat shared by scheduler and health checks."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from services.data.repository import DataRepository
from shared.config import settings
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
        reference_time: datetime | None = None,
    ) -> dict:
        max_delay = timedelta(seconds=max_delay_seconds or settings.market_data_stale_seconds)
        now = reference_time or datetime.now(UTC)
        freshness = self.data_repo.check_freshness(
            symbol=symbol,
            timeframe=timeframe,
            reference_time=now,
            max_delay=max_delay,
        )
        active_for_symbol = [
            event
            for event in self.data_repo.list_active_risk_events_by_type(event_type=RiskEventType.DATA_STALE)
            if event.affected_scope == [symbol] and event.risk_event_id is not None
        ]
        if not freshness["is_fresh"]:
            if active_for_symbol:
                # An unresolved event for this exact symbol already blocks new entries;
                # writing another row every heartbeat cycle only inflates the audit trail
                # without changing gatekeeper behavior (see decisions-log data_stale storm fix).
                freshness["risk_event_id"] = active_for_symbol[0].risk_event_id
                freshness["duplicate_event_suppressed"] = True
            else:
                # 数据陈旧事件应该在6小时后自动过期
                expires_at = now + timedelta(hours=6)
                event = self.data_repo.store_risk_event(
                    RiskEvent(
                        event_type=RiskEventType.DATA_STALE,
                        severity=RiskSeverity.HIGH,
                        source="market_data_heartbeat",
                        description=f"{symbol} {timeframe} market data is stale",
                        affected_scope=[symbol],
                        recommended_action="pause_strategy",
                        occurred_at=now,
                        expires_at=expires_at,
                    )
                )
                freshness["risk_event_id"] = event.risk_event_id
        else:
            resolved = 0
            for event in active_for_symbol:
                risk_event_id = event.risk_event_id
                if risk_event_id is None:
                    continue
                self.data_repo.update_risk_event_resolution(
                    risk_event_id=risk_event_id,
                    resolution_status="resolved",
                )
                resolved += 1
            freshness["resolved_stale_event_count"] = resolved
        return freshness

    def check_symbols(
        self,
        *,
        symbols: list[str],
        timeframe: str = "1m",
        max_delay_seconds: int | None = None,
        reference_time: datetime | None = None,
    ) -> dict:
        results = {
            symbol: self.check_symbol(
                symbol=symbol,
                timeframe=timeframe,
                max_delay_seconds=max_delay_seconds,
                reference_time=reference_time,
            )
            for symbol in symbols
        }
        return {
            "checked_symbols": symbols,
            "timeframe": timeframe,
            "stale_symbols": [symbol for symbol, result in results.items() if not result["is_fresh"]],
            "results": results,
        }
