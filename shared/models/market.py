"""A-level market data contracts (produced by CCXT collectors).

`OHLCVBar` is the canonical bar consumed by Freqtrade / VectorBT / Jesse.
`MarketExtras` carries crypto-specific funding/OI/liquidation data.
Both map to TimescaleDB hypertables (see infra/timescale/init.sql).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

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
    def _high_ge_low(self) -> "OHLCVBar":
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
