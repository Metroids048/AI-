"""Fail-closed portfolio correlation and directional-exposure calculations."""

from __future__ import annotations

from collections.abc import Iterable
from math import sqrt

from shared.models import PositionSnapshot, TradeSide


def correlation(left: list[float], right: list[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    numerator = sum((x - left_mean) * (y - right_mean) for x, y in zip(left, right, strict=True))
    left_scale = sqrt(sum((x - left_mean) ** 2 for x in left))
    right_scale = sqrt(sum((y - right_mean) ** 2 for y in right))
    if left_scale == 0 or right_scale == 0:
        return None
    return numerator / (left_scale * right_scale)


def close_returns(prices: Iterable[float]) -> list[float] | None:
    values = list(prices)
    if len(values) < 61 or any(price <= 0 for price in values):
        return None
    return [(current / previous) - 1 for previous, current in zip(values, values[1:], strict=False)]


def signed_exposure(position: PositionSnapshot, *, account_equity: float) -> float:
    if account_equity <= 0:
        return 0.0
    notional = abs(position.quantity * position.mark_price) / account_equity
    return notional if position.side == TradeSide.LONG else -notional
