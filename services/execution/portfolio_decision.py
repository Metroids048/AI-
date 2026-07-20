"""Cycle-level correlation conflict resolution over aligned log returns."""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal

from shared.models import BlockCode, PortfolioDecision, PositionSide


@dataclass(frozen=True, slots=True)
class PortfolioCandidate:
    symbol: str
    side: PositionSide
    score: Decimal


def _log_returns(closes: list[float]) -> list[float]:
    if len(closes) < 2 or any(value <= 0 for value in closes):
        return []
    return [math.log(current / previous) for previous, current in zip(closes, closes[1:], strict=False)]


def _correlation(left: list[float], right: list[float]) -> Decimal | None:
    size = min(len(left), len(right))
    if size < 2:
        return None
    left = left[-size:]
    right = right[-size:]
    left_mean = sum(left) / size
    right_mean = sum(right) / size
    numerator = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right, strict=True))
    left_variance = sum((value - left_mean) ** 2 for value in left)
    right_variance = sum((value - right_mean) ** 2 for value in right)
    denominator = math.sqrt(left_variance * right_variance)
    if denominator == 0:
        return None
    return Decimal(str(numerator / denominator))


def resolve_correlation_conflicts(
    *,
    candidates: list[PortfolioCandidate],
    aligned_closes: dict[str, list[float]],
    correlation_threshold: Decimal,
) -> dict[str, PortfolioDecision]:
    decisions = {
        item.symbol: PortfolioDecision(
            decision_id=f"portfolio:{item.symbol}",
            symbol=item.symbol,
            raw_side=item.side,
            final_side=item.side,
            accepted=True,
        )
        for item in candidates
    }
    ordered = sorted(candidates, key=lambda item: (-item.score, item.symbol))
    accepted: list[PortfolioCandidate] = []
    for candidate in ordered:
        conflict_with: PortfolioCandidate | None = None
        for stronger in accepted:
            correlation = _correlation(
                _log_returns(aligned_closes.get(candidate.symbol, [])),
                _log_returns(aligned_closes.get(stronger.symbol, [])),
            )
            if (
                correlation is not None
                and abs(correlation) >= correlation_threshold
                and candidate.side is not stronger.side
            ):
                conflict_with = stronger
                break
        if conflict_with is None:
            accepted.append(candidate)
            continue
        decisions[candidate.symbol] = PortfolioDecision(
            decision_id=f"portfolio:{candidate.symbol}",
            symbol=candidate.symbol,
            raw_side=candidate.side,
            final_side=PositionSide.FLAT,
            accepted=False,
            block_codes=(BlockCode.CORRELATION_DIRECTION_CONFLICT,),
            reason=f"weaker than correlated opposite candidate {conflict_with.symbol}",
        )
    return decisions
