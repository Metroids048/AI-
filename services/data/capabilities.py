"""Exchange capability registry for connector-aware UI and gates."""

from __future__ import annotations

from shared.models import ExchangeCapability


def list_exchange_capabilities() -> list[ExchangeCapability]:
    """Return the first explicit connector capability registry."""

    return [
        ExchangeCapability(
            exchange="binance",
            market_type="spot",
            supports_public_rest=True,
            supports_public_ws=True,
            supports_private_account=False,
            supports_order_placement=False,
            symbols=["BTC/USDT", "ETH/USDT"],
            notes=[
                "Public OHLCV REST backfill is implemented through CCXT.",
                "Public closed-Kline WS persistence seam is implemented.",
                "Private account sync and live order placement remain intentionally disabled.",
            ],
        ),
        ExchangeCapability(
            exchange="binance",
            market_type="usdm_perpetual",
            supports_public_rest=True,
            supports_public_ws=True,
            supports_private_account=False,
            supports_order_placement=False,
            symbols=["BTC/USDT:USDT", "ETH/USDT:USDT"],
            notes=[
                "Funding-rate backfill is implemented for validation and Paper visibility.",
                "Private futures account permissions are not wired in this tranche.",
            ],
        ),
    ]
