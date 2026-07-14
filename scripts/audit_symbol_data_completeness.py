"""Top20 data-completeness + rejection-reason audit (模块4).

Answers: "how many Top20 coins have never been eligible to trade because
their OHLCV history is incomplete?" -- turning that assumption into a
measured number instead of a guess.

For each symbol in `DEFAULT_BINANCE_TOP20`:
  1. Runs the exact same query paper_signal.py::_build_risk_state uses
     (`data_repo.list_ohlcv_bars(symbol=symbol, timeframe="1h", limit=61)`)
     and records actual bar count, earliest/latest timestamp, whether the
     count reaches 61, how stale the latest bar is, and any abnormal gaps
     between consecutive bars (> 1.5x the timeframe interval).
  2. Computes the 7-day occurrence rate of `portfolio_correlation_unavailable`,
     `technical_signals_insufficient`, and `confirmation_unavailable_fail_closed`
     per symbol -- the first from `OrderExecution.rejection_codes` (persisted on
     every gatekeeper.submit_order() call), the other two from the new
     `decision_snapshots` history table (services/strategy_library, migration
     0008), since decision_pipeline "no trade" skips never reach submit_order()
     and were never durably persisted before that table existed.

Writes a Markdown table to stdout and, with --output, to a file.

Usage:
    python scripts/audit_symbol_data_completeness.py --database-url sqlite:///.local_paper_console.db
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta


def _as_aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


TARGET_TIMEFRAME = "1h"
REQUIRED_BAR_COUNT = 61
STALE_AFTER_HOURS = 3
GAP_TOLERANCE_MULTIPLIER = 1.5
LOOKBACK_DAYS = 7
REJECTION_REASONS = (
    "portfolio_correlation_unavailable",
    "technical_signals_insufficient",
    "confirmation_unavailable_fail_closed",
)


@dataclass
class SymbolCompleteness:
    symbol: str
    bar_count: int
    earliest: datetime | None
    latest: datetime | None
    has_gap: bool
    max_gap_hours: float
    stale_hours: float | None

    @property
    def meets_minimum(self) -> bool:
        return self.bar_count >= REQUIRED_BAR_COUNT

    @property
    def is_stale(self) -> bool:
        return self.stale_hours is not None and self.stale_hours > STALE_AFTER_HOURS


@dataclass
class SymbolRejectionStats:
    symbol: str
    counts: dict[str, int] = field(default_factory=dict)
    total_cycles: int = 0

    def rate(self, reason: str) -> float:
        if self.total_cycles == 0:
            return 0.0
        return self.counts.get(reason, 0) / self.total_cycles


def _timeframe_hours(timeframe: str) -> float:
    unit = timeframe[-1]
    value = float(timeframe[:-1])
    if unit == "m":
        return value / 60.0
    if unit == "h":
        return value
    if unit == "d":
        return value * 24.0
    raise ValueError(f"unsupported timeframe: {timeframe}")


def _assess_completeness(symbol: str, bars: list, *, now: datetime) -> SymbolCompleteness:
    if not bars:
        return SymbolCompleteness(
            symbol=symbol,
            bar_count=0,
            earliest=None,
            latest=None,
            has_gap=False,
            max_gap_hours=0.0,
            stale_hours=None,
        )
    expected_interval = _timeframe_hours(TARGET_TIMEFRAME)
    max_gap_hours = 0.0
    for previous, current in zip(bars, bars[1:], strict=False):
        gap_hours = (current.timestamp - previous.timestamp).total_seconds() / 3600.0
        max_gap_hours = max(max_gap_hours, gap_hours)
    latest = bars[-1].timestamp
    return SymbolCompleteness(
        symbol=symbol,
        bar_count=len(bars),
        earliest=bars[0].timestamp,
        latest=latest,
        has_gap=max_gap_hours > expected_interval * GAP_TOLERANCE_MULTIPLIER,
        max_gap_hours=max_gap_hours,
        stale_hours=(now - latest).total_seconds() / 3600.0,
    )


def _collect_rejection_stats(
    *,
    symbol: str,
    execution_repo,
    decision_snapshot_repo,
    since: datetime,
) -> SymbolRejectionStats:
    stats = SymbolRejectionStats(symbol=symbol)

    orders = [
        order
        for order in execution_repo.list_orders()
        if order.symbol == symbol and order.created_at is not None and _as_aware(order.created_at) >= since
    ]
    stats.total_cycles += len(orders)
    for order in orders:
        for code in order.rejection_codes:
            if code in REJECTION_REASONS:
                stats.counts[code] = stats.counts.get(code, 0) + 1

    snapshots = decision_snapshot_repo.list_snapshots(symbol=symbol, since=since)
    stats.total_cycles += len(snapshots)
    for snapshot in snapshots:
        if snapshot.pipeline_status in REJECTION_REASONS:
            stats.counts[snapshot.pipeline_status] = stats.counts.get(snapshot.pipeline_status, 0) + 1

    return stats


def _render_markdown(
    completeness: list[SymbolCompleteness],
    rejection_stats: dict[str, SymbolRejectionStats],
    *,
    now: datetime,
) -> str:
    lines = [
        f"# Top20 数据完整性 + 拒绝原因巡检 ({now.isoformat()})",
        "",
        "## 1. OHLCV 数据完整性",
        "",
        "| 币种 | 实际根数 | 是否≥61 | 最早时间 | 最新时间 | 数据滞后(小时) | 存在异常缺口 |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in completeness:
        lines.append(
            f"| {row.symbol} | {row.bar_count} | {'是' if row.meets_minimum else '否'} | "
            f"{row.earliest.isoformat() if row.earliest else 'N/A'} | "
            f"{row.latest.isoformat() if row.latest else 'N/A'} | "
            f"{f'{row.stale_hours:.1f}' if row.stale_hours is not None else 'N/A'} | "
            f"{'是' if row.has_gap else '否'} |"
        )

    lines += [
        "",
        f"## 2. 过去{LOOKBACK_DAYS}天拒绝原因占比",
        "",
        "| 币种 | 总样本数 | portfolio_correlation_unavailable | technical_signals_insufficient | "
        "confirmation_unavailable_fail_closed |",
        "|---|---|---|---|---|",
    ]
    for symbol, stats in rejection_stats.items():
        lines.append(
            f"| {symbol} | {stats.total_cycles} | "
            f"{stats.rate('portfolio_correlation_unavailable'):.1%} | "
            f"{stats.rate('technical_signals_insufficient'):.1%} | "
            f"{stats.rate('confirmation_unavailable_fail_closed'):.1%} |"
        )

    never_eligible = [
        row.symbol
        for row in completeness
        if not row.meets_minimum
        and rejection_stats.get(row.symbol, SymbolRejectionStats(symbol=row.symbol)).rate(
            "portfolio_correlation_unavailable"
        )
        > 0
    ]
    lines += [
        "",
        "## 3. 结论",
        "",
        (
            f"因数据不完整（<{REQUIRED_BAR_COUNT}根）且触发过 portfolio_correlation_unavailable 的币种: "
            f"{', '.join(never_eligible) if never_eligible else '无'}"
        ),
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--output", default=None, help="optional path to also write the Markdown report to")
    parser.add_argument("--lookback-days", type=int, default=LOOKBACK_DAYS)
    args = parser.parse_args()

    if args.database_url:
        os.environ["POSTGRES_URL"] = args.database_url

    from services.data import DataRepository
    from services.data.service import DEFAULT_BINANCE_TOP20
    from services.database import get_session_factory
    from services.strategy_library import DecisionSnapshotRepository, ExecutionRepository

    session = get_session_factory()()
    try:
        data_repo = DataRepository(session)
        execution_repo = ExecutionRepository(session)
        decision_snapshot_repo = DecisionSnapshotRepository(session)

        now = datetime.now(UTC)
        since = now - timedelta(days=args.lookback_days)

        completeness: list[SymbolCompleteness] = []
        rejection_stats: dict[str, SymbolRejectionStats] = {}
        for symbol in DEFAULT_BINANCE_TOP20:
            bars = data_repo.list_ohlcv_bars(symbol=symbol, timeframe=TARGET_TIMEFRAME, limit=REQUIRED_BAR_COUNT)
            completeness.append(_assess_completeness(symbol, bars, now=now))
            rejection_stats[symbol] = _collect_rejection_stats(
                symbol=symbol,
                execution_repo=execution_repo,
                decision_snapshot_repo=decision_snapshot_repo,
                since=since,
            )
    finally:
        session.close()

    report = _render_markdown(completeness, rejection_stats, now=now)
    print(report)
    if args.output:
        from pathlib import Path

        Path(args.output).write_text(report, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
