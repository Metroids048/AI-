"""Read-side market queries for the Paper trading console."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from shared.models import MarketSnapshot, OhlcvSeriesResponse

from .repository import DataRepository


class MarketQueryService:
    """Build console-friendly market read models from persisted timeseries data."""

    def __init__(self, data_repo: DataRepository):
        self.data_repo = data_repo

    def get_ohlcv_series(self, *, symbol: str, timeframe: str, limit: int = 300) -> OhlcvSeriesResponse:
        safe_limit = max(1, min(limit, 1000))
        candles = self.data_repo.list_ohlcv_bars(
            symbol=symbol,
            timeframe=timeframe,
            limit=safe_limit,
        )
        return OhlcvSeriesResponse(
            symbol=symbol,
            timeframe=timeframe,
            data_status="ok" if candles else "empty",
            candles=candles,
        )

    def get_snapshot(
        self,
        *,
        symbol: str,
        perp_symbol: str,
        timeframe: str = "1h",
        reference_time: datetime | None = None,
    ) -> MarketSnapshot:
        reference = reference_time or datetime.now(UTC)
        spot_bar = self.data_repo.get_latest_ohlcv_bar(symbol=symbol, timeframe=timeframe)
        perp_bar = self.data_repo.get_latest_ohlcv_bar(symbol=perp_symbol, timeframe=timeframe)
        extras = self.data_repo.get_latest_market_extras(symbol=perp_symbol)

        basis_bps: float | None = None
        if spot_bar is not None and perp_bar is not None and spot_bar.close != Decimal("0"):
            basis_bps = float((perp_bar.close - spot_bar.close) / spot_bar.close * Decimal("10000"))

        latest_at = max(
            [item.timestamp for item in (spot_bar, perp_bar) if item is not None],
            default=None,
        )
        freshness = self._freshness(symbol=symbol, timeframe=timeframe, reference_time=reference)
        status = "ok" if spot_bar is not None and perp_bar is not None else "empty"
        if status == "ok" and not freshness["is_fresh"]:
            status = "stale"

        return MarketSnapshot(
            symbol=symbol,
            perp_symbol=perp_symbol,
            data_status=status,
            spot_last_price=spot_bar.close if spot_bar is not None else None,
            perp_last_price=perp_bar.close if perp_bar is not None else None,
            basis_bps=basis_bps,
            funding_rate=extras.funding_rate if extras is not None else None,
            next_funding_at=self._next_funding_time(extras.timestamp) if extras is not None else None,
            latest_bar_at=latest_at,
            data_freshness=freshness,
        )

    def _freshness(self, *, symbol: str, timeframe: str, reference_time: datetime) -> dict:
        return self.data_repo.check_freshness(
            symbol=symbol,
            timeframe=timeframe,
            reference_time=reference_time,
            max_delay=timedelta(hours=2),
        )

    @staticmethod
    def _next_funding_time(last_funding_at: datetime) -> datetime:
        return last_funding_at + timedelta(hours=8)
