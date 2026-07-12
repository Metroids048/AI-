"""Read-side market queries for the Paper trading console."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import SupportsFloat

from shared.models import (
    FundingArbitrageSignal,
    MarketOrderBookResponse,
    MarketSnapshot,
    MarketTradesResponse,
    MarketUniverseItem,
    OhlcvSeriesResponse,
)

from .binance import BinanceCcxtClient, BinanceUniverseSelector
from .repository import DataRepository
from .service import DEFAULT_BINANCE_TOP20
from .universe import fixed_top20_market_items


class MarketQueryService:
    """Build console-friendly market read models from persisted timeseries data."""

    def __init__(self, data_repo: DataRepository, *, binance_client: BinanceCcxtClient | None = None):
        self.data_repo = data_repo
        self.binance_client = binance_client

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
            source="persisted_market_data",
            candles=candles,
        )

    def get_live_ohlcv_series(self, *, symbol: str, timeframe: str, limit: int = 300) -> OhlcvSeriesResponse:
        if self.binance_client is None:
            return self.get_ohlcv_series(symbol=symbol, timeframe=timeframe, limit=limit)
        safe_limit = max(1, min(limit, 1000))
        try:
            candles = self.binance_client.fetch_recent_ohlcv(symbol=symbol, timeframe=timeframe, limit=safe_limit)
        except Exception:
            return self.get_ohlcv_series(symbol=symbol, timeframe=timeframe, limit=safe_limit)
        self.data_repo.store_ohlcv_bars(candles)
        return OhlcvSeriesResponse(
            symbol=symbol,
            timeframe=timeframe,
            data_status="ok" if candles else "empty",
            source="binance_public_rest",
            candles=candles,
        )

    def get_market_universe(
        self,
        *,
        limit: int = 20,
        tickers: list[dict] | None = None,
        mode: str = "dynamic",
        exchange_info_symbols: list[dict] | None = None,
    ) -> list[MarketUniverseItem]:
        safe_limit = max(1, min(limit, 50))
        if mode == "fixed_top20":
            return fixed_top20_market_items(exchange_info_symbols)[: min(safe_limit, 20)]
        source = "fallback_default_top20"
        selected = DEFAULT_BINANCE_TOP20[:safe_limit]
        payload_by_symbol: dict[str, dict] = {}
        if tickers:
            selector = BinanceUniverseSelector()
            selected_from_tickers = selector.select_top_usdm_symbols(tickers, limit=safe_limit)
            if selected_from_tickers:
                selected = selected_from_tickers
                source = "binance_usdm_24h_ticker"
                payload_by_symbol = {
                    _usdm_raw_to_platform_symbol(str(item.get("symbol", ""))): item
                    for item in tickers
                    if str(item.get("symbol", "")).endswith("USDT")
                }
        return [
            MarketUniverseItem(
                symbol=symbol,
                perp_symbol=f"{symbol}:USDT",
                quote_volume=_float_or_none(payload_by_symbol.get(symbol, {}).get("quoteVolume")),
                last_price=_decimal_or_none(payload_by_symbol.get(symbol, {}).get("lastPrice")),
                price_change_percent=_float_or_none(payload_by_symbol.get(symbol, {}).get("priceChangePercent")),
                source=source,
            )
            for symbol in selected
        ]

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

    def get_live_snapshot(
        self,
        *,
        symbol: str,
        perp_symbol: str,
        timeframe: str = "1h",
        reference_time: datetime | None = None,
    ) -> MarketSnapshot:
        if self.binance_client is not None:
            try:
                self.data_repo.store_ohlcv_bars(
                    self.binance_client.fetch_recent_ohlcv(symbol=symbol, timeframe=timeframe, limit=2)
                )
                self.data_repo.store_ohlcv_bars(
                    self.binance_client.fetch_recent_ohlcv(symbol=perp_symbol, timeframe=timeframe, limit=2)
                )
                premium = self.binance_client.fetch_premium_index(symbol=perp_symbol)
                if premium is not None:
                    self.data_repo.store_market_extras([premium])
            except Exception:
                pass
        return self.get_snapshot(
            symbol=symbol,
            perp_symbol=perp_symbol,
            timeframe=timeframe,
            reference_time=reference_time,
        )

    def get_order_book(self, *, symbol: str, limit: int = 20) -> MarketOrderBookResponse:
        if self.binance_client is None:
            return MarketOrderBookResponse(symbol=symbol, source="disabled")
        try:
            return self.binance_client.fetch_live_order_book(symbol=symbol, limit=limit)
        except Exception:
            return MarketOrderBookResponse(symbol=symbol, source="binance_public_rest_error")

    def get_recent_trades(self, *, symbol: str, limit: int = 50) -> MarketTradesResponse:
        if self.binance_client is None:
            return MarketTradesResponse(symbol=symbol, source="disabled")
        try:
            return self.binance_client.fetch_live_trades(symbol=symbol, limit=limit)
        except Exception:
            return MarketTradesResponse(symbol=symbol, source="binance_public_rest_error")

    def get_funding_arbitrage_signal(
        self,
        *,
        symbol: str,
        perp_symbol: str,
        timeframe: str = "1h",
        fee_bps: float = 8.0,
        slippage_bps: float = 6.0,
    ) -> FundingArbitrageSignal:
        snapshot = (
            self.get_live_snapshot(symbol=symbol, perp_symbol=perp_symbol, timeframe=timeframe)
            if self.binance_client is not None
            else self.get_snapshot(symbol=symbol, perp_symbol=perp_symbol, timeframe=timeframe)
        )
        funding_bps = float(snapshot.funding_rate * Decimal("10000")) if snapshot.funding_rate is not None else None
        basis_bps = snapshot.basis_bps
        # A hedged carry round trip has four fills: spot/perpetual entry and exit.
        # Basis is mark-to-market risk, not realized funding income.
        round_trip_cost_bps = 4 * (fee_bps + slippage_bps)
        estimated_net = (
            funding_bps - round_trip_cost_bps
            if funding_bps is not None
            else None
        )
        rejection_reasons: list[str] = []
        if snapshot.data_status != "ok":
            rejection_reasons.append(f"market_data_{snapshot.data_status}")
        if funding_bps is None:
            rejection_reasons.append("missing_funding_rate")
        elif funding_bps <= 0:
            rejection_reasons.append("non_positive_funding")
        if estimated_net is None or estimated_net <= 0:
            rejection_reasons.append("negative_net_edge")
        if basis_bps is not None and basis_bps < 0:
            rejection_reasons.append("perp_discount_basis_risk")

        should_enter = not rejection_reasons
        return FundingArbitrageSignal(
            symbol=symbol,
            perp_symbol=perp_symbol,
            funding_rate=snapshot.funding_rate,
            funding_bps=funding_bps,
            basis_bps=basis_bps,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
            round_trip_cost_bps=round_trip_cost_bps,
            estimated_net_edge_bps=estimated_net,
            should_enter_paper=should_enter,
            rejection_reasons=list(dict.fromkeys(rejection_reasons)),
            recommended_strategy_template={
                "source": "binance_funding_arbitrage",
                "core_thesis": "Capture positive funding with spot/perp hedged paper positions after costs.",
                "entry_rules": {
                    "min_estimated_net_edge_bps": 10.0,
                    "round_trip_cost_bps": round_trip_cost_bps,
                    "requires_positive_funding": True,
                    "requires_non_negative_basis": True,
                },
                "stoploss_rules": {"basis_widening_bps": 50, "max_adverse_mark_move_bps": 80},
                "position_rules": {"paper_only": True, "max_leverage": 2},
            },
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


def _usdm_raw_to_platform_symbol(raw_symbol: str) -> str:
    if raw_symbol.endswith("USDT"):
        return f"{raw_symbol.removesuffix('USDT')}/USDT"
    return raw_symbol


def _float_or_none(value: object) -> float | None:
    if value is None:
        return None
    if not isinstance(value, (str, bytes, SupportsFloat)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _decimal_or_none(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None
