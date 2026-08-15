"""Point-in-time market-alpha feature primitives for research-only replay.

The feature source is intentionally outside the legacy OHLCV strategy family:
perpetual taker flow, spot/perpetual dislocation, funding pressure, and BTC/ETH
relative returns.  Nothing in this module creates an execution intent.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import isfinite
from statistics import mean, stdev


@dataclass(frozen=True)
class MarketBar:
    timestamp: int
    close: float
    quote_volume: float
    taker_buy_quote: float


@dataclass(frozen=True)
class AlphaSnapshot:
    symbol: str
    timestamp: int
    perp_return_1h: float
    spot_return_1h: float
    basis: float
    basis_change: float
    taker_imbalance: float
    taker_imbalance_3h: float
    funding_rate: float
    funding_zscore: float
    btc_return_1h: float
    relative_return: float


def taker_imbalance(*, taker_buy_quote: float, quote_volume: float) -> float:
    """Return signed taker-buy pressure in [-1, 1]."""
    if quote_volume <= 0:
        return 0.0
    value = (2.0 * taker_buy_quote / quote_volume) - 1.0
    return max(-1.0, min(1.0, value))


def zscore(value: float, history: Sequence[float]) -> float:
    """Point-in-time z-score; insufficient/constant history returns zero."""
    if len(history) < 2:
        return 0.0
    deviation = stdev(history)
    if deviation <= 0:
        return 0.0
    return (value - mean(history)) / deviation


def build_snapshot(
    *,
    symbol: str,
    timestamp: int,
    perp: MarketBar,
    perp_previous: MarketBar,
    spot: MarketBar,
    spot_previous: MarketBar,
    perp_history: Sequence[MarketBar],
    basis_history: Sequence[float],
    funding_rate: float,
    funding_history: Sequence[float],
    btc_return_1h: float,
    relative_return: float,
) -> AlphaSnapshot:
    perp_return = perp.close / perp_previous.close - 1.0 if perp_previous.close else 0.0
    spot_return = spot.close / spot_previous.close - 1.0 if spot_previous.close else 0.0
    basis = perp.close / spot.close - 1.0 if spot.close else 0.0
    previous_basis = perp_previous.close / spot_previous.close - 1.0 if spot_previous.close else 0.0
    imbalance = taker_imbalance(
        taker_buy_quote=perp.taker_buy_quote,
        quote_volume=perp.quote_volume,
    )
    recent_imbalances = [
        taker_imbalance(taker_buy_quote=item.taker_buy_quote, quote_volume=item.quote_volume)
        for item in perp_history[-3:]
    ]
    return AlphaSnapshot(
        symbol=symbol,
        timestamp=timestamp,
        perp_return_1h=perp_return,
        spot_return_1h=spot_return,
        basis=basis,
        basis_change=basis - previous_basis,
        taker_imbalance=imbalance,
        taker_imbalance_3h=sum(recent_imbalances) / len(recent_imbalances) if recent_imbalances else imbalance,
        funding_rate=funding_rate,
        funding_zscore=zscore(funding_rate, funding_history[-90:]),
        btc_return_1h=btc_return_1h,
        relative_return=relative_return,
    )


def feature_vector(snapshot: AlphaSnapshot, feature_set: str) -> tuple[float, ...]:
    """Return deterministic features for one ablation arm."""
    price_only = (snapshot.perp_return_1h,)
    pressure = (snapshot.taker_imbalance, snapshot.taker_imbalance_3h, snapshot.funding_zscore)
    dislocation = (snapshot.basis, snapshot.basis_change, snapshot.perp_return_1h - snapshot.spot_return_1h)
    cross_asset = (snapshot.btc_return_1h, snapshot.relative_return)
    result: tuple[float, ...]
    if feature_set == "PRICE_ONLY":
        result = price_only
    elif feature_set == "DERIVATIVES_PRESSURE":
        result = price_only + pressure
    elif feature_set == "SPOT_FUTURES_DISLOCATION":
        result = price_only + dislocation
    elif feature_set == "BTC_ETH_LEAD_LAG":
        result = price_only + cross_asset
    elif feature_set == "ALL_ALPHA":
        result = price_only + pressure + dislocation + cross_asset
    else:
        raise ValueError(f"unknown feature_set: {feature_set}")
    if not all(isfinite(value) for value in result):
        raise ValueError("non-finite market-alpha feature")
    return result


def row_to_mapping(snapshot: AlphaSnapshot) -> Mapping[str, float | int | str]:
    return {
        "symbol": snapshot.symbol,
        "timestamp": snapshot.timestamp,
        "perp_return_1h": snapshot.perp_return_1h,
        "spot_return_1h": snapshot.spot_return_1h,
        "basis": snapshot.basis,
        "basis_change": snapshot.basis_change,
        "taker_imbalance": snapshot.taker_imbalance,
        "taker_imbalance_3h": snapshot.taker_imbalance_3h,
        "funding_rate": snapshot.funding_rate,
        "funding_zscore": snapshot.funding_zscore,
        "btc_return_1h": snapshot.btc_return_1h,
        "relative_return": snapshot.relative_return,
    }


__all__ = [
    "AlphaSnapshot",
    "MarketBar",
    "build_snapshot",
    "feature_vector",
    "row_to_mapping",
    "taker_imbalance",
    "zscore",
]
