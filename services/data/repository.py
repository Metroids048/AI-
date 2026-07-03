"""Timeseries repository for A-level market data and risk events."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Index,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    insert,
    select,
    update,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from shared.models import Exchange, MarketExtras, OHLCVBar, RiskEvent, Timeframe

TIMESERIES_METADATA = MetaData()

ohlcv_bars = Table(
    "ohlcv_bars",
    TIMESERIES_METADATA,
    # SQLite tests create ordinary tables; Timescale owns hypertables in prod.
    # No primary key keeps this aligned with infra/timescale/init.sql.
    Column("time", DateTime(timezone=True), nullable=False),
    Column("symbol", String(30), nullable=False),
    Column("exchange", String(20), nullable=False),
    Column("timeframe", String(10), nullable=False),
    Column("open", Numeric),
    Column("high", Numeric),
    Column("low", Numeric),
    Column("close", Numeric),
    Column("volume", Numeric),
    Index(
        "uq_ohlcv_symbol_exchange_tf_time",
        "symbol",
        "exchange",
        "timeframe",
        "time",
        unique=True,
    ),
)

market_extras = Table(
    "market_extras",
    TIMESERIES_METADATA,
    Column("time", DateTime(timezone=True), nullable=False),
    Column("symbol", String(30), nullable=False),
    Column("funding_rate", Numeric),
    Column("open_interest", Numeric),
    Column("long_ratio", Numeric),
    Column("short_ratio", Numeric),
    Column("liquidation_usd", Numeric),
    Index("uq_market_extras_symbol_time", "symbol", "time", unique=True),
)

risk_events = Table(
    "risk_events",
    TIMESERIES_METADATA,
    Column("id", String(36), primary_key=True),
    Column("created_at", DateTime(timezone=True)),
    Column("source", String(50)),
    Column("level", String(20)),
    Column("event_type", String(40)),
    Column("description", Text),
    Column("affected_symbols", JSON),
    Column("expires_at", DateTime(timezone=True)),
    Column("resolution_status", String(30)),
)


def create_timeseries_schema(engine: Engine) -> None:
    """Create ordinary tables for local SQLite tests and dev smoke runs."""

    TIMESERIES_METADATA.create_all(engine)


def _as_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    decimal = Decimal(str(value))
    if decimal == decimal.to_integral():
        return decimal.quantize(Decimal("1"))
    return decimal.normalize()


def _as_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


class DataRepository:
    """Repository for persisted market bars, extras, and blocking risk events."""

    def __init__(self, session: Session):
        self.session = session

    def store_ohlcv_bars(self, bars: Iterable[OHLCVBar | dict]) -> int:
        rows = []
        for item in bars:
            bar = item if isinstance(item, OHLCVBar) else OHLCVBar(**item)
            rows.append(
                {
                    "time": bar.timestamp,
                    "symbol": bar.symbol,
                    "exchange": str(bar.exchange),
                    "timeframe": str(bar.timeframe),
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume": bar.volume,
                }
            )
        if rows:
            for row in rows:
                result = self.session.execute(
                    update(ohlcv_bars)
                    .where(
                        ohlcv_bars.c.time == row["time"],
                        ohlcv_bars.c.symbol == row["symbol"],
                        ohlcv_bars.c.exchange == row["exchange"],
                        ohlcv_bars.c.timeframe == row["timeframe"],
                    )
                    .values(
                        open=row["open"],
                        high=row["high"],
                        low=row["low"],
                        close=row["close"],
                        volume=row["volume"],
                    )
                )
                if getattr(result, "rowcount", 0) == 0:
                    self.session.execute(insert(ohlcv_bars), row)
            self.session.commit()
        return len(rows)

    def list_ohlcv_bars(
        self,
        *,
        symbol: str,
        timeframe: str,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        limit: int | None = None,
    ) -> list[OHLCVBar]:
        stmt = select(ohlcv_bars).where(
            ohlcv_bars.c.symbol == symbol,
            ohlcv_bars.c.timeframe == timeframe,
        )
        if start_at is not None:
            stmt = stmt.where(ohlcv_bars.c.time >= start_at)
        if end_at is not None:
            stmt = stmt.where(ohlcv_bars.c.time <= end_at)
        stmt = stmt.order_by(ohlcv_bars.c.time)
        if limit is not None:
            recent = stmt.order_by(None).order_by(ohlcv_bars.c.time.desc()).limit(limit).subquery()
            stmt = select(recent).order_by(recent.c.time)
        bars: list[OHLCVBar] = []
        for row in self.session.execute(stmt).all():
            timestamp = _as_aware(row.time)
            if timestamp is None:
                continue
            bars.append(
                OHLCVBar(
                    symbol=row.symbol,
                    exchange=Exchange(row.exchange),
                    timeframe=Timeframe(row.timeframe),
                    time=timestamp,
                    open=_as_decimal(row.open) or Decimal("0"),
                    high=_as_decimal(row.high) or Decimal("0"),
                    low=_as_decimal(row.low) or Decimal("0"),
                    close=_as_decimal(row.close) or Decimal("0"),
                    volume=_as_decimal(row.volume) or Decimal("0"),
                )
            )
        return bars

    def get_latest_ohlcv_bar(self, *, symbol: str, timeframe: str = "1h") -> OHLCVBar | None:
        bars = self.list_ohlcv_bars(symbol=symbol, timeframe=timeframe, limit=1)
        return bars[-1] if bars else None

    def store_market_extras(self, extras: Iterable[MarketExtras | dict]) -> int:
        rows = []
        for item in extras:
            extra = item if isinstance(item, MarketExtras) else MarketExtras(**item)
            rows.append(
                {
                    "time": extra.timestamp,
                    "symbol": extra.symbol,
                    "funding_rate": extra.funding_rate,
                    "open_interest": extra.open_interest,
                    "long_ratio": extra.long_ratio,
                    "short_ratio": extra.short_ratio,
                    "liquidation_usd": extra.liquidation_usd,
                }
            )
        if rows:
            for row in rows:
                result = self.session.execute(
                    update(market_extras)
                    .where(
                        market_extras.c.time == row["time"],
                        market_extras.c.symbol == row["symbol"],
                    )
                    .values(
                        funding_rate=row["funding_rate"],
                        open_interest=row["open_interest"],
                        long_ratio=row["long_ratio"],
                        short_ratio=row["short_ratio"],
                        liquidation_usd=row["liquidation_usd"],
                    )
                )
                if getattr(result, "rowcount", 0) == 0:
                    self.session.execute(insert(market_extras), row)
            self.session.commit()
        return len(rows)

    def list_market_extras(
        self,
        *,
        symbol: str,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        limit: int | None = None,
    ) -> list[MarketExtras]:
        stmt = select(market_extras).where(market_extras.c.symbol == symbol)
        if start_at is not None:
            stmt = stmt.where(market_extras.c.time >= start_at)
        if end_at is not None:
            stmt = stmt.where(market_extras.c.time <= end_at)
        stmt = stmt.order_by(market_extras.c.time)
        if limit is not None:
            recent = stmt.order_by(None).order_by(market_extras.c.time.desc()).limit(limit).subquery()
            stmt = select(recent).order_by(recent.c.time)
        extras: list[MarketExtras] = []
        for row in self.session.execute(stmt).all():
            timestamp = _as_aware(row.time)
            if timestamp is None:
                continue
            extras.append(
                MarketExtras(
                    symbol=row.symbol,
                    time=timestamp,
                    funding_rate=_as_decimal(row.funding_rate),
                    open_interest=_as_decimal(row.open_interest),
                    long_ratio=_as_decimal(row.long_ratio),
                    short_ratio=_as_decimal(row.short_ratio),
                    liquidation_usd=_as_decimal(row.liquidation_usd),
                )
            )
        return extras

    def get_latest_market_extras(self, *, symbol: str) -> MarketExtras | None:
        extras = self.list_market_extras(symbol=symbol, limit=1)
        return extras[-1] if extras else None

    def check_gaps(self, *, symbol: str, timeframe: str, start_at: datetime, end_at: datetime) -> dict[str, Any]:
        step = _timeframe_to_delta(timeframe)
        observed = {
            bar.timestamp
            for bar in self.list_ohlcv_bars(symbol=symbol, timeframe=timeframe, start_at=start_at, end_at=end_at)
        }
        missing: list[datetime] = []
        cursor = start_at
        while cursor <= end_at:
            if cursor not in observed:
                missing.append(cursor)
            cursor += step
        return {
            "has_gaps": bool(missing),
            "missing_timestamps": missing,
            "expected_interval_seconds": step.total_seconds(),
        }

    def check_freshness(
        self,
        *,
        symbol: str,
        timeframe: str,
        reference_time: datetime,
        max_delay: timedelta,
    ) -> dict[str, Any]:
        stmt = (
            select(ohlcv_bars.c.time)
            .where(ohlcv_bars.c.symbol == symbol, ohlcv_bars.c.timeframe == timeframe)
            .order_by(ohlcv_bars.c.time.desc())
            .limit(1)
        )
        latest = self.session.execute(stmt).scalar_one_or_none()
        latest = _as_aware(latest)
        delay = (reference_time - latest) if latest is not None else None
        return {
            "is_fresh": latest is not None and delay is not None and delay <= max_delay,
            "latest_timestamp": latest,
            "max_delay_seconds": max_delay.total_seconds(),
            "delay_seconds": delay.total_seconds() if delay is not None else None,
        }

    def store_risk_event(self, event: RiskEvent) -> RiskEvent:
        import uuid

        event_id = event.risk_event_id or str(uuid.uuid4())
        self.session.execute(
            insert(risk_events),
            {
                "id": event_id,
                "created_at": event.occurred_at or datetime.now(UTC),
                "source": event.source,
                "level": str(event.severity),
                "event_type": str(event.event_type),
                "description": event.description,
                "affected_symbols": event.affected_scope,
                "expires_at": event.expires_at,
                "resolution_status": str(event.resolution_status),
            },
        )
        self.session.commit()
        return event.model_copy(update={"risk_event_id": event_id})

    def get_risk_event(self, risk_event_id: str) -> RiskEvent | None:
        row = self.session.execute(select(risk_events).where(risk_events.c.id == risk_event_id)).first()
        return self._risk_event_from_row(row) if row else None

    def update_risk_event_resolution(self, *, risk_event_id: str, resolution_status: str) -> RiskEvent | None:
        from sqlalchemy import update

        self.session.execute(
            update(risk_events).where(risk_events.c.id == risk_event_id).values(resolution_status=resolution_status)
        )
        self.session.commit()
        return self.get_risk_event(risk_event_id)

    def list_risk_events(self, *, active_only: bool = False) -> list[RiskEvent]:
        stmt = select(risk_events).order_by(risk_events.c.created_at.desc())
        rows = self.session.execute(stmt).all()
        now = datetime.now(UTC)
        events: list[RiskEvent] = []
        for row in rows:
            event = self._risk_event_from_row(row)
            if active_only:
                expires_at = _as_aware(event.expires_at)
                if event.resolution_status not in {"detected", "acknowledged"}:
                    continue
                if expires_at is not None and expires_at < now:
                    continue
            events.append(event)
        return events

    def has_blocking_risk_event(self, *, scope: str, reference_time: datetime) -> bool:
        stmt = select(risk_events).where(
            risk_events.c.level.in_(["high", "critical"]),
            risk_events.c.resolution_status.in_(["detected", "acknowledged"]),
        )
        rows = self.session.execute(stmt).all()
        for row in rows:
            expires_at = _as_aware(row.expires_at)
            if expires_at is not None and expires_at < reference_time:
                continue
            affected = row.affected_symbols
            if affected is None or scope in affected:
                return True
        return False

    @staticmethod
    def _risk_event_from_row(row: Any) -> RiskEvent:
        return RiskEvent(
            risk_event_id=row.id,
            event_type=row.event_type,
            severity=row.level,
            source=row.source,
            description=row.description,
            affected_scope=row.affected_symbols,
            resolution_status=row.resolution_status,
            occurred_at=_as_aware(row.created_at),
            expires_at=_as_aware(row.expires_at),
        )


def _timeframe_to_delta(timeframe: str) -> timedelta:
    mapping = {
        "1m": timedelta(minutes=1),
        "5m": timedelta(minutes=5),
        "15m": timedelta(minutes=15),
        "1h": timedelta(hours=1),
        "4h": timedelta(hours=4),
        "1d": timedelta(days=1),
    }
    if timeframe not in mapping:
        raise ValueError(f"unsupported timeframe: {timeframe}")
    return mapping[timeframe]
