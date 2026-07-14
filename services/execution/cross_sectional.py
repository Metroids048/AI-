"""Cross-sectional funding-rate ranking across the fixed Top20 basket.

Every other automatic strategy lane (`carry`, `directional`) evaluates one
symbol at a time against its own history. This module instead ranks all
scanned symbols against each other by current funding rate on every cycle,
so the strategy can go short the symbols paying the most funding and long
the symbols paying the least (or receiving it), collecting the funding-rate
spread with a delta-lighter, less directionally-correlated book than either
existing lane.
"""

from __future__ import annotations

from dataclasses import dataclass

from services.data import DataRepository

BASKET_SIDE_SHORT = "short_candidate"
BASKET_SIDE_LONG = "long_candidate"


@dataclass(frozen=True)
class CrossSectionalRankEntry:
    symbol: str
    funding_rate_bps: float
    rank: int  # 1 = highest funding rate (most expensive to hold long)
    total_ranked: int
    basket_side: str | None  # BASKET_SIDE_SHORT / BASKET_SIDE_LONG / None (outside basket)


def compute_funding_rank_snapshot(
    *,
    data_repo: DataRepository,
    symbols: list[str],
    basket_size: int,
) -> dict[str, CrossSectionalRankEntry]:
    """Rank `symbols` by latest known funding rate; symbols missing funding data
    are excluded entirely (fail-closed), not defaulted to a neutral rank."""
    extras: list[tuple[str, float]] = []
    for symbol in symbols:
        latest = data_repo.get_latest_market_extras(symbol=symbol)
        if latest is not None and latest.funding_rate is not None:
            extras.append((symbol, float(latest.funding_rate) * 10_000.0))
    extras.sort(key=lambda item: item[1], reverse=True)
    total = len(extras)
    snapshot: dict[str, CrossSectionalRankEntry] = {}
    for idx, (symbol, funding_bps) in enumerate(extras, start=1):
        side: str | None = None
        if idx <= basket_size:
            side = BASKET_SIDE_SHORT
        elif idx > total - basket_size:
            side = BASKET_SIDE_LONG
        snapshot[symbol] = CrossSectionalRankEntry(
            symbol=symbol,
            funding_rate_bps=funding_bps,
            rank=idx,
            total_ranked=total,
            basket_side=side,
        )
    return snapshot
