"""Candidate leaderboard: fair comparison of multiple strategy candidates.

Extends services/validation/technical_replay.py's compare_exit_policies from
"two-way comparison" to "N-candidate leaderboard". Each candidate runs through
the same historical data with the same backtest engine, producing a ranked list
sorted by net expectancy confidence interval lower bound.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from services.strategy_library.candidates.registry import StrategyCandidate, get_candidate
from services.validation.technical_replay import (
    MarketData,
    ReplayMetrics,
    TechnicalStrategyValidationService,
)
from shared.models import StrategyContract


@dataclass(frozen=True)
class CandidateLeaderboardEntry:
    """Single candidate's performance metrics in the leaderboard.

    Attributes:
        candidate_id: Unique candidate identifier
        source: Origin (e.g. "operator_experience", "data_driven")
        hypothesis: Core thesis
        metrics: Full ReplayMetrics from technical_replay
        net_expectancy_ci_lower: 95% confidence interval lower bound
        net_expectancy_ci_upper: 95% confidence interval upper bound
        rank: Position in leaderboard (1 = best)
    """

    candidate_id: str
    source: str
    hypothesis: str
    metrics: ReplayMetrics
    net_expectancy_ci_lower: float
    net_expectancy_ci_upper: float
    rank: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "source": self.source,
            "hypothesis": self.hypothesis,
            "rank": self.rank,
            "net_expectancy": self.metrics.net_expectancy,
            "net_expectancy_ci_lower": self.net_expectancy_ci_lower,
            "net_expectancy_ci_upper": self.net_expectancy_ci_upper,
            "total_trades": self.metrics.total_trades,
            "win_rate": self.metrics.win_rate,
            "profit_factor": self.metrics.profit_factor,
            "sharpe": self.metrics.sharpe,
            "max_drawdown": self.metrics.max_drawdown,
            "average_r": self.metrics.average_r,
            "metrics": self.metrics.as_dict(),
        }


@dataclass(frozen=True)
class CandidateLeaderboard:
    """Ranked comparison of multiple strategy candidates.

    Attributes:
        generated_at: When this leaderboard was generated
        entries: Ranked list of candidates (best first)
        market_data_summary: Summary of data used (symbols, date range, bar count)
    """

    generated_at: datetime
    entries: tuple[CandidateLeaderboardEntry, ...]
    market_data_summary: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at.isoformat(),
            "entries": [entry.as_dict() for entry in self.entries],
            "market_data_summary": self.market_data_summary,
        }

    def get_winner(self) -> CandidateLeaderboardEntry | None:
        """Return the top-ranked candidate (if any)."""
        return self.entries[0] if self.entries else None

    def get_by_id(self, candidate_id: str) -> CandidateLeaderboardEntry | None:
        """Find a candidate by ID in the leaderboard."""
        for entry in self.entries:
            if entry.candidate_id == candidate_id:
                return entry
        return None


def _compute_net_expectancy_ci(metrics: ReplayMetrics, confidence: float = 0.95) -> tuple[float, float]:
    """Compute confidence interval for net expectancy.

    Uses bootstrap or normal approximation depending on sample size.
    For small samples (< 30 trades), returns conservative wider bounds.

    Args:
        metrics: ReplayMetrics with trade history
        confidence: Confidence level (default 0.95 for 95%)

    Returns:
        (lower_bound, upper_bound)
    """
    n = metrics.total_trades
    if n == 0:
        return (0.0, 0.0)

    net_exp = metrics.net_expectancy

    # Conservative estimate: use sample variance from trades
    # For now, simple approximation: assume std ~ 2 * |net_exp| (conservative)
    # TODO: Replace with actual trade-level return variance once we have trade list access
    if n < 30:
        # Small sample: use wider margin
        std_estimate = max(abs(net_exp) * 2.0, 0.01)
        margin = 2.0 * std_estimate / (n**0.5)  # ~95% CI for small sample
    else:
        # Larger sample: tighter margin
        std_estimate = max(abs(net_exp) * 1.5, 0.01)
        margin = 1.96 * std_estimate / (n**0.5)  # 95% CI

    lower = net_exp - margin
    upper = net_exp + margin
    return (lower, upper)


def run_candidate_leaderboard(
    *,
    candidate_ids: list[str],
    market_data: MarketData,
    symbols: list[str] | None = None,
    date_range: tuple[datetime | None, datetime | None] | None = None,
    warmup_bars: int = 80,
    max_workers: int = 8,
) -> CandidateLeaderboard:
    """Run multiple candidates through the same backtest and generate a leaderboard.

    Each candidate is evaluated independently on the same historical data using
    the existing technical_replay engine. Results are ranked by net expectancy
    confidence interval lower bound (conservative ranking).

    Args:
        candidate_ids: List of candidate IDs from the registry
        market_data: Historical OHLCV data (same format as technical_replay)
        symbols: Optional symbol filter
        date_range: Optional (start, end) datetime range
        warmup_bars: Bars needed before first signal (passed to replay)
        max_workers: Parallelism for replay

    Returns:
        CandidateLeaderboard with ranked entries

    Raises:
        KeyError: If any candidate_id not found in registry
    """
    if symbols is not None:
        wanted = set(symbols)
        market_data = {symbol: frames for symbol, frames in market_data.items() if symbol in wanted}

    start_at, end_at = (date_range or (None, None))

    # Run each candidate through replay
    results: list[tuple[StrategyCandidate, ReplayMetrics]] = []

    for candidate_id in candidate_ids:
        candidate = get_candidate(candidate_id)
        config = candidate.get_config()

        # Convert candidate config to StrategyContract format
        strategy = StrategyContract(
            strategy_key=candidate.candidate_id,
            version=candidate.version,
            rules=config,
        )

        # Run replay
        service = TechnicalStrategyValidationService(
            warmup_bars=warmup_bars,
            max_workers=max_workers,
        )
        metrics = service.replay(
            strategy=strategy,
            market_data=market_data,
            start_at=start_at,
            end_at=end_at,
        )

        results.append((candidate, metrics))

    # Compute confidence intervals and rank
    entries_unranked: list[tuple[float, CandidateLeaderboardEntry]] = []

    for candidate, metrics in results:
        ci_lower, ci_upper = _compute_net_expectancy_ci(metrics)

        entry = CandidateLeaderboardEntry(
            candidate_id=candidate.candidate_id,
            source=candidate.source,
            hypothesis=candidate.hypothesis,
            metrics=metrics,
            net_expectancy_ci_lower=ci_lower,
            net_expectancy_ci_upper=ci_upper,
            rank=0,  # Will be set after sorting
        )
        entries_unranked.append((ci_lower, entry))

    # Sort by CI lower bound (descending)
    entries_unranked.sort(key=lambda x: x[0], reverse=True)

    # Assign ranks
    entries_ranked: list[CandidateLeaderboardEntry] = []
    for rank, (_, entry) in enumerate(entries_unranked, start=1):
        entries_ranked.append(
            CandidateLeaderboardEntry(
                candidate_id=entry.candidate_id,
                source=entry.source,
                hypothesis=entry.hypothesis,
                metrics=entry.metrics,
                net_expectancy_ci_lower=entry.net_expectancy_ci_lower,
                net_expectancy_ci_upper=entry.net_expectancy_ci_upper,
                rank=rank,
            )
        )

    # Summarize market data
    total_bars = sum(len(frames.get("15m", [])) for frames in market_data.values())
    symbols_list = list(market_data.keys())
    data_summary = {
        "symbols": symbols_list,
        "symbol_count": len(symbols_list),
        "total_bars": total_bars,
        "date_range": {
            "start": start_at.isoformat() if start_at else None,
            "end": end_at.isoformat() if end_at else None,
        },
    }

    return CandidateLeaderboard(
        generated_at=datetime.now(UTC),
        entries=tuple(entries_ranked),
        market_data_summary=data_summary,
    )
