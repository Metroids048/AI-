"""A-level market data contracts (produced by CCXT collectors).

`OHLCVBar` is the canonical bar consumed by Freqtrade / VectorBT / Jesse.
`MarketExtras` carries crypto-specific funding/OI/liquidation data.
Both map to TimescaleDB hypertables (see infra/timescale/init.sql).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import Field, field_validator, model_validator

from .base import PlatformModel
from .enums import Exchange, Timeframe


class OHLCVBar(PlatformModel):
    """One OHLCV candle. `timestamp` is the TimescaleDB `time` column."""

    symbol: str = Field(examples=["BTC/USDT"])
    exchange: Exchange = Exchange.BINANCE
    timeframe: Timeframe = Timeframe.H1
    timestamp: datetime = Field(alias="time")
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    @field_validator("open", "high", "low", "close", "volume")
    @classmethod
    def _non_negative(cls, v: Decimal) -> Decimal:
        if v < 0:
            raise ValueError("OHLCV values must be non-negative")
        return v

    @model_validator(mode="after")
    def _high_ge_low(self) -> OHLCVBar:
        # Cross-field check must run after all fields are set (field-level
        # validators see only previously-defined fields).
        if self.high < self.low:
            raise ValueError("high must be >= low")
        return self


class MarketExtras(PlatformModel):
    """Crypto-specific extras: funding rate / OI / long-short / liquidation."""

    symbol: str
    timestamp: datetime = Field(alias="time")
    funding_rate: Decimal | None = None
    open_interest: Decimal | None = None
    long_ratio: Decimal | None = None
    short_ratio: Decimal | None = None
    liquidation_usd: Decimal | None = None


class MarketSnapshot(PlatformModel):
    """Read model for the Paper console market header."""

    symbol: str
    perp_symbol: str
    exchange: str = "binance"
    data_status: str = "empty"
    spot_last_price: Decimal | None = None
    perp_last_price: Decimal | None = None
    basis_bps: float | None = None
    funding_rate: Decimal | None = None
    next_funding_at: datetime | None = None
    latest_bar_at: datetime | None = None
    data_freshness: dict[str, Any] = Field(default_factory=dict)


class OhlcvSeriesResponse(PlatformModel):
    """Candles for chart rendering plus an explicit data status."""

    symbol: str
    timeframe: str
    exchange: str = "binance"
    data_status: str = "empty"
    candles: list[OHLCVBar] = Field(default_factory=list)


class ConsoleOverview(PlatformModel):
    """Aggregated read model for the first Paper trading console screen."""

    environment: str = "dev"
    mode: str = "paper"
    exchange: str = "binance"
    market: MarketSnapshot
    latest_backtests: list[dict[str, Any]] = Field(default_factory=list)
    paper_runs: list[dict[str, Any]] = Field(default_factory=list)
    orders: list[dict[str, Any]] = Field(default_factory=list)
    positions: list[dict[str, Any]] = Field(default_factory=list)
    risk_events: list[dict[str, Any]] = Field(default_factory=list)
    global_risk_status: str = "normal"


class ExchangeCapability(PlatformModel):
    """Exchange connector capability registry entry."""

    exchange: str
    market_type: str
    supports_public_rest: bool = False
    supports_public_ws: bool = False
    supports_private_account: bool = False
    supports_order_placement: bool = False
    symbols: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
