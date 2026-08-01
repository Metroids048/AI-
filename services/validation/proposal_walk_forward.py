"""Leakage-resistant walk-forward boundaries for proposal research."""

from __future__ import annotations

import calendar
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from services.validation.strategy_promotion import TrialLedger


def _add_months(value: datetime, months: int) -> datetime:
    month_index = value.year * 12 + value.month - 1 + months
    year, month_zero = divmod(month_index, 12)
    month = month_zero + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


@dataclass(frozen=True)
class ProposalWalkForwardWindow:
    window_id: str
    train_start: datetime
    train_end: datetime
    purge_start: datetime
    purge_end: datetime
    oos_start: datetime
    oos_end: datetime
    embargo_start: datetime
    embargo_end: datetime

    def as_record(self) -> dict[str, Any]:
        return {key: value.isoformat() if isinstance(value, datetime) else value for key, value in asdict(self).items()}


def build_proposal_walk_forward_windows(
    *,
    development_start: datetime,
    development_end: datetime,
    window_count: int = 8,
    train_months: int = 12,
    oos_months: int = 3,
    max_lookback_bars: int = 72,
    max_holding_bars: int = 8,
    bar_interval: timedelta = timedelta(minutes=15),
    embargo: timedelta = timedelta(hours=24),
) -> tuple[ProposalWalkForwardWindow, ...]:
    """Build fixed three-month OOS slices without reading a sealed holdout."""

    if development_start.tzinfo is None or development_end.tzinfo is None:
        raise ValueError("walk-forward boundaries must be timezone-aware")
    if window_count != 8 or train_months != 12 or oos_months != 3:
        raise ValueError("Phase 1 requires eight 3-month OOS windows and 12-month training windows")
    if embargo < timedelta(hours=24):
        raise ValueError("embargo must be at least 24 hours")
    development_start = development_start.astimezone(UTC)
    development_end = development_end.astimezone(UTC)
    purge = bar_interval * (max_lookback_bars + max_holding_bars)
    first_oos = _add_months(development_start, train_months)
    windows: list[ProposalWalkForwardWindow] = []
    for index in range(window_count):
        oos_start = _add_months(first_oos, index * oos_months)
        oos_end = min(_add_months(oos_start, oos_months), development_end)
        if oos_end <= oos_start:
            raise ValueError("development range cannot provide eight complete OOS windows")
        train_start = _add_months(oos_start, -train_months)
        purge_start = oos_start - purge
        if train_start < development_start or purge_start <= train_start:
            raise ValueError("training range is shorter than the required purge boundary")
        if oos_end > development_end:
            raise ValueError("walk-forward window would read beyond development_end")
        windows.append(
            ProposalWalkForwardWindow(
                window_id=f"oos_{index + 1}",
                train_start=train_start,
                train_end=purge_start,
                purge_start=purge_start,
                purge_end=oos_start,
                oos_start=oos_start,
                oos_end=oos_end,
                embargo_start=oos_end,
                embargo_end=oos_end + embargo,
            )
        )
    return tuple(windows)


def record_walk_forward_trials(
    ledger: TrialLedger,
    *,
    windows: tuple[ProposalWalkForwardWindow, ...],
    candidate_ids: tuple[str, ...],
    metrics_by_trial: Mapping[tuple[str, str], Mapping[str, Any]] | None = None,
    status: str = "recorded_no_parameter_optimization",
) -> None:
    """Append one immutable ledger record for every window/candidate pair."""

    for window in windows:
        for candidate_id in candidate_ids:
            ledger.record(
                trial_id=f"{candidate_id}:{window.window_id}",
                strategy_id=candidate_id,
                parameters={
                    "window": window.as_record(),
                    "metrics": dict((metrics_by_trial or {}).get((window.window_id, candidate_id), {})),
                    "parameter_optimization": False,
                },
                status=status,
            )
