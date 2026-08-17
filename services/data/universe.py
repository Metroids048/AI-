"""Operator-approved fixed Binance simulation universe."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from decimal import Decimal
from typing import Any

from shared.models import MarketUniverseItem, UniverseAsset

AUTO_PAPER_RESEARCH_SYMBOLS: tuple[str, ...] = ("BTC/USDT", "ETH/USDT", "SOL/USDT")
AUTO_SIMULATION_EXECUTION_SYMBOLS: tuple[str, ...] = (
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "XRP/USDT",
    "BNB/USDT",
)


def execution_baseline_keys() -> frozenset[str]:
    """Return the canonical long/short keys for the execution universe."""

    return frozenset(
        f"{symbol}:{direction}" for symbol in AUTO_SIMULATION_EXECUTION_SYMBOLS for direction in ("long", "short")
    )


def execution_scope_hash(symbols: Iterable[str] = AUTO_SIMULATION_EXECUTION_SYMBOLS) -> str:
    """Stable identity for the exact ordered Binance simulation execution scope."""

    normalized = [exchange_to_platform_symbol(str(symbol)) for symbol in symbols]
    payload = json.dumps(normalized, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


# ULTRA-AGGRESSIVE Paper testing: reduced from Top20 to Top10 (2026-07-15 v2)
# Concentrating on highest-liquidity majors to maximize single-symbol exposure
# and reduce correlation complexity. More concentrated = more decisive signals.
FIXED_TOP20_ASSETS: tuple[dict[str, str], ...] = (
    {"display_symbol": "BTC", "platform_symbol": "BTC/USDT", "exchange_symbol": "BTCUSDT"},
    {"display_symbol": "ETH", "platform_symbol": "ETH/USDT", "exchange_symbol": "ETHUSDT"},
    {"display_symbol": "SOL", "platform_symbol": "SOL/USDT", "exchange_symbol": "SOLUSDT"},
    {"display_symbol": "XRP", "platform_symbol": "XRP/USDT", "exchange_symbol": "XRPUSDT"},
    {"display_symbol": "BNB", "platform_symbol": "BNB/USDT", "exchange_symbol": "BNBUSDT"},
    {"display_symbol": "DOGE", "platform_symbol": "DOGE/USDT", "exchange_symbol": "DOGEUSDT"},
    {"display_symbol": "ADA", "platform_symbol": "ADA/USDT", "exchange_symbol": "ADAUSDT"},
    {"display_symbol": "LINK", "platform_symbol": "LINK/USDT", "exchange_symbol": "LINKUSDT"},
    {"display_symbol": "AVAX", "platform_symbol": "AVAX/USDT", "exchange_symbol": "AVAXUSDT"},
    {"display_symbol": "TRX", "platform_symbol": "TRX/USDT", "exchange_symbol": "TRXUSDT"},
    # REMOVED 10 symbols: HYPE, SUI, TON, HBAR, ONDO, ENA, TAO, FET, RENDER, PEPE
    # Rationale: focus on established majors with deepest liquidity and clearest trends
)

FIXED_TOP20_SYMBOLS: tuple[str, ...] = tuple(item["platform_symbol"] for item in FIXED_TOP20_ASSETS)
# Offline competition may inspect the full liquid Top10.  This constant never
# grants execution permission; the active manifest remains the execution scope.
TECHNICAL_RESEARCH_SYMBOLS: tuple[str, ...] = FIXED_TOP20_SYMBOLS
CONTRACT_SYMBOL_ALIASES: dict[str, str] = {
    "PEPE/USDT": "1000PEPEUSDT",
}
PLATFORM_TO_EXCHANGE_SYMBOL: dict[str, str] = {
    **{item["platform_symbol"]: item["exchange_symbol"] for item in FIXED_TOP20_ASSETS},
    **CONTRACT_SYMBOL_ALIASES,
}
EXCHANGE_TO_PLATFORM_SYMBOL: dict[str, str] = {
    **{item["exchange_symbol"]: item["platform_symbol"] for item in FIXED_TOP20_ASSETS},
    **{exchange_symbol: platform_symbol for platform_symbol, exchange_symbol in CONTRACT_SYMBOL_ALIASES.items()},
}


def fixed_top20_assets(exchange_info_symbols: Iterable[Mapping[str, Any]] | None = None) -> list[UniverseAsset]:
    """Return the fixed operator Top20 with optional Binance exchangeInfo status."""

    exchange_info = {str(item.get("symbol", "")).upper(): item for item in exchange_info_symbols or []}
    assets: list[UniverseAsset] = []
    for item in FIXED_TOP20_ASSETS:
        raw = exchange_info.get(item["exchange_symbol"], {})
        status = str(raw.get("status") or "unknown").lower()
        reason = None
        if status == "unknown":
            reason = "exchangeInfo unavailable; runtime will skip exchange-first orders until status is known"
        elif status != "trading":
            reason = f"Binance contract status is {raw.get('status')}"
        assets.append(
            UniverseAsset(
                display_symbol=item["display_symbol"],
                platform_symbol=item["platform_symbol"],
                perp_symbol=f"{item['platform_symbol']}:USDT",
                exchange_symbol=item["exchange_symbol"],
                tradable_status=status,
                reason=reason,
                precision=_precision(raw),
                min_notional=_min_notional(raw),
            )
        )
    return assets


def fixed_top20_market_items(
    exchange_info_symbols: Iterable[Mapping[str, Any]] | None = None,
) -> list[MarketUniverseItem]:
    return [
        MarketUniverseItem(
            symbol=asset.platform_symbol,
            perp_symbol=asset.perp_symbol,
            display_symbol=asset.display_symbol,
            exchange_symbol=asset.exchange_symbol,
            tradable_status=asset.tradable_status,
            reason=asset.reason,
            precision=asset.precision,
            min_notional=asset.min_notional,
            source=asset.source,
        )
        for asset in fixed_top20_assets(exchange_info_symbols)
    ]


def tradable_fixed_top20_symbols(exchange_info_symbols: Iterable[Mapping[str, Any]] | None = None) -> list[str]:
    assets = fixed_top20_assets(exchange_info_symbols)
    return [asset.platform_symbol for asset in assets if asset.tradable_status in {"trading", "unknown"}]


def canonical_market_symbol(symbol: str) -> str:
    """Return the single internal identity for a market symbol.

    The exchange boundary speaks ``BTC/USDT:USDT`` for USD-M perpetuals, but the
    database and every internal reader use the canonical ``BTC/USDT``. Persisting
    the exchange form made ``market_extras`` unreachable by exact-equality
    queries, so funding/open-interest read as permanently missing (ADR: T0-01).

    This is the boundary converter: it accepts either shape and is idempotent.
    It is not a dual-truth fallback — callers use the result for a single query
    against a single canonical identity.
    """
    return symbol.replace(":USDT", "")


def platform_to_exchange_symbol(symbol: str) -> str:
    normalized = canonical_market_symbol(symbol)
    if normalized in PLATFORM_TO_EXCHANGE_SYMBOL:
        return PLATFORM_TO_EXCHANGE_SYMBOL[normalized]
    return normalized.replace("/", "").upper()


def exchange_to_platform_symbol(symbol: str) -> str:
    raw = symbol.upper()
    if "/" in raw:
        return canonical_market_symbol(raw)
    if raw in EXCHANGE_TO_PLATFORM_SYMBOL:
        return EXCHANGE_TO_PLATFORM_SYMBOL[raw]
    if raw.endswith("USDT"):
        return f"{raw.removesuffix('USDT')}/USDT"
    return raw


def _precision(raw: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in ("pricePrecision", "quantityPrecision", "baseAssetPrecision", "quotePrecision"):
        if key in raw:
            result[key] = raw[key]
    return result


def _min_notional(raw: Mapping[str, Any]) -> Decimal | None:
    for item in raw.get("filters", []) or []:
        if not isinstance(item, Mapping):
            continue
        if item.get("filterType") in {"MIN_NOTIONAL", "NOTIONAL"}:
            value = item.get("notional") or item.get("minNotional")
            if value is not None:
                return Decimal(str(value))
    return None
