"""Shared validation metrics for strategy backtests."""

from __future__ import annotations

import math
from decimal import Decimal
from statistics import mean, pstdev


def profit_factor(pnls: list[Decimal]) -> float:
    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [pnl for pnl in pnls if pnl < 0]
    if not losses:
        return 9.99 if wins else 0.0
    return float(sum(wins, Decimal("0")) / abs(sum(losses, Decimal("0"))))


def expectancy(pnls: list[Decimal]) -> float:
    return float(sum(pnls, Decimal("0")) / len(pnls)) if pnls else 0.0


def win_rate(pnls: list[Decimal]) -> float:
    return len([pnl for pnl in pnls if pnl > 0]) / len(pnls) if pnls else 0.0


def annualized_sharpe(returns: list[float], *, periods_per_year: float) -> float:
    if len(returns) < 2:
        return 0.0
    avg = mean(returns)
    volatility = pstdev(returns)
    if volatility == 0:
        return 0.0
    return (avg / volatility) * math.sqrt(periods_per_year)


def max_drawdown_from_pnls(pnls: list[Decimal], *, initial_equity: Decimal) -> float:
    if initial_equity <= 0:
        raise ValueError("initial_equity must be positive")
    equity = initial_equity
    peak = initial_equity
    max_drawdown = Decimal("0")
    for pnl in pnls:
        equity += pnl
        if equity > peak:
            peak = equity
        drawdown = (peak - equity) / peak if peak > 0 else Decimal("0")
        if drawdown > max_drawdown:
            max_drawdown = drawdown
    return float(max_drawdown)


def deflated_sharpe(
    sharpe: float,
    returns: list[float],
    *,
    trials_count: int | None,
) -> float:
    """Conservatively deflate Sharpe for repeated trials.

    This implements a lightweight Bailey/Lopez de Prado style correction:
    subtract the expected maximum Sharpe inflation from multiple trials. The
    correction is intentionally dependency-free and monotonic in trials_count.
    """

    if len(returns) < 2:
        return 0.0
    trials = max(int(trials_count or 1), 1)
    if trials == 1:
        return sharpe
    sample_size = len(returns)
    penalty = math.sqrt(2.0 * math.log(trials)) / math.sqrt(sample_size)
    return sharpe - penalty


def sharpe_confidence(sharpe: float, *, sample_size: int) -> float:
    if sample_size <= 1:
        return 0.0
    z_score = sharpe * math.sqrt(sample_size - 1)
    return 0.5 * (1.0 + math.erf(z_score / math.sqrt(2.0)))
