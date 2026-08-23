from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from services.automated_trading.domain.candidates import EXECUTION_UNIVERSE

from ..domain.events import EntrySemantics, KolEventType, KolTradeEvent


@dataclass(frozen=True)
class MarketSnapshot:
    symbol: str
    price: Decimal
    spread_bps: Decimal
    observed_at: datetime
    liquidity_ok: bool = True
    symbol_status: str = "TRADING"


@dataclass(frozen=True)
class MarketSanityResult:
    accepted: bool
    reason_code: str
    detail: str = ""


def check_market_sanity(
    event: KolTradeEvent,
    *,
    snapshot: MarketSnapshot,
    now: datetime,
    max_age_seconds: int = 900,
    max_spread_bps: Decimal = Decimal("50"),
) -> MarketSanityResult:
    if event.event_type is not KolEventType.OPEN:
        return MarketSanityResult(False, "NON_ENTRY_EVENT")
    if event.symbol != snapshot.symbol:
        return MarketSanityResult(False, "SYMBOL_MISMATCH")
    if event.symbol not in EXECUTION_UNIVERSE:
        return MarketSanityResult(False, "SYMBOL_NOT_EXECUTION_ELIGIBLE")
    if snapshot.symbol_status != "TRADING":
        return MarketSanityResult(False, "SYMBOL_NOT_TRADING")
    if not snapshot.liquidity_ok:
        return MarketSanityResult(False, "LIQUIDITY_UNAVAILABLE")
    if snapshot.spread_bps > max_spread_bps:
        return MarketSanityResult(False, "SPREAD_TOO_WIDE")
    if event.detected_at is not None and (now - event.detected_at).total_seconds() > max_age_seconds:
        return MarketSanityResult(False, "STALE_SIGNAL")
    if event.entry_semantics is EntrySemantics.RANGE:
        if event.entry_low is None or event.entry_high is None:
            return MarketSanityResult(False, "INVALID_ENTRY_RANGE")
        if not event.entry_low <= snapshot.price <= event.entry_high:
            return MarketSanityResult(False, "ENTRY_ZONE_MISSED")
    elif event.entry_price is not None and event.entry_semantics is not EntrySemantics.CONDITIONAL:
        drift_bps = abs(snapshot.price - event.entry_price) / event.entry_price * Decimal("10000")
        if drift_bps > Decimal("20"):
            return MarketSanityResult(False, "ENTRY_DRIFT_TOO_WIDE")
    return MarketSanityResult(True, "MARKET_SANITY_PASS")
