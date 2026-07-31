"""Run proposal candidates on the sealed pre-holdout BTC/ETH history only.

This command is deliberately research-only.  It reads the immutable history
database, writes an append-only trial ledger, and never imports an execution
service or active strategy manifest.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from bisect import bisect_right
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import tee
from pathlib import Path
from typing import Any

from services.strategy_library.candidates.failed_breakout_reversal_v1 import evaluate_failed_breakout_reversal
from services.strategy_library.candidates.range_sweep_reversion_v1 import evaluate_range_sweep_reversion
from services.strategy_library.candidates.trend_pullback_v2 import evaluate_trend_pullback_v2
from services.strategy_library.context import MarketContext, MarketContextBuilder
from services.strategy_library.regime.scorer_v2 import RegimeScorerV2
from services.validation.proposal_replay import (
    ProposalGenerator,
    ProposalReplayMetrics,
    ProposalReplayRunner,
    ProposalReplayTrade,
    ReplayCostModel,
)
from services.validation.strategy_promotion import (
    FinalHoldoutGuard,
    PromotionMetrics,
    TrialLedger,
    evaluate_promotion,
    stationary_cluster_bootstrap_lcb,
)
from shared.models import Exchange, OHLCVBar, Timeframe

SYMBOLS = ("BTC/USDT", "ETH/USDT")
FINAL_HOLDOUT_START = datetime(2026, 1, 29, tzinfo=UTC)
DEVELOPMENT_START = datetime(2023, 1, 29, tzinfo=UTC)
WARMUP_START = DEVELOPMENT_START - timedelta(days=15)
TIMEFRAME_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400}
WINDOW_LENGTHS = {"1m": 2, "5m": 2, "15m": 80, "1h": 80, "4h": 80}
GENERATORS: dict[str, ProposalGenerator] = {
    "failed_breakout_reversal_v1": lambda context: evaluate_failed_breakout_reversal(
        context, RegimeScorerV2().score(context)
    ),
    "trend_pullback_v2": lambda context: evaluate_trend_pullback_v2(context, RegimeScorerV2().score(context)),
    "range_sweep_reversion_v1": lambda context: evaluate_range_sweep_reversion(
        context, RegimeScorerV2().score(context)
    ),
}


@dataclass(frozen=True)
class RawSeries:
    timeframe: str
    times: tuple[int, ...]
    rows: tuple[tuple[float, float, float, float, float], ...]

    def window(self, *, decision_at: datetime, count: int) -> list[OHLCVBar]:
        close_cutoff = int(decision_at.timestamp()) - TIMEFRAME_SECONDS[self.timeframe]
        end = bisect_right(self.times, close_cutoff)
        start = max(0, end - count)
        return [
            OHLCVBar(
                symbol="",
                exchange=Exchange.BINANCE,
                timeframe=Timeframe(self.timeframe),
                time=datetime.fromtimestamp(opened_at, UTC),
                open=Decimal(str(values[0])),
                high=Decimal(str(values[1])),
                low=Decimal(str(values[2])),
                close=Decimal(str(values[3])),
                volume=Decimal(str(values[4])),
            )
            for opened_at, values in zip(self.times[start:end], self.rows[start:end], strict=True)
        ]

    def bar_at(self, index: int, *, symbol: str) -> OHLCVBar:
        opened_at = self.times[index]
        values = self.rows[index]
        return OHLCVBar(
            symbol=symbol,
            exchange=Exchange.BINANCE,
            timeframe=Timeframe(self.timeframe),
            time=datetime.fromtimestamp(opened_at, UTC),
            open=Decimal(str(values[0])),
            high=Decimal(str(values[1])),
            low=Decimal(str(values[2])),
            close=Decimal(str(values[3])),
            volume=Decimal(str(values[4])),
        )


def _parse_time(value: str) -> int:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return int((parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)).timestamp())


def _load_series(connection: sqlite3.Connection, *, symbol: str, timeframe: str) -> RawSeries:
    rows = connection.execute(
        """
        SELECT time, open, high, low, close, volume
        FROM ohlcv_bars
        WHERE symbol = ? AND timeframe = ? AND time >= ? AND time < ?
        ORDER BY time
        """,
        (
            symbol,
            timeframe,
            WARMUP_START.replace(tzinfo=None).isoformat(sep=" "),
            FINAL_HOLDOUT_START.replace(tzinfo=None).isoformat(sep=" "),
        ),
    ).fetchall()
    return RawSeries(
        timeframe=timeframe,
        times=tuple(_parse_time(str(row[0])) for row in rows),
        rows=tuple(tuple(float(value) for value in row[1:]) for row in rows),
    )


def _contexts_and_next_bars(*, symbol: str, series: dict[str, RawSeries]) -> Iterator[tuple[MarketContext, OHLCVBar]]:
    entry = series["15m"]
    builder = MarketContextBuilder()
    for index in range(WINDOW_LENGTHS["15m"], len(entry.times) - 1):
        signal_bar = entry.bar_at(index, symbol=symbol)
        decision_at = signal_bar.timestamp + timedelta(minutes=15)
        next_bar = entry.bar_at(index + 1, symbol=symbol)
        if decision_at < DEVELOPMENT_START or next_bar.timestamp >= FINAL_HOLDOUT_START:
            continue
        windows = {
            timeframe: [
                bar.model_copy(update={"symbol": symbol})
                for bar in raw.window(decision_at=decision_at, count=WINDOW_LENGTHS[timeframe])
            ]
            for timeframe, raw in series.items()
        }
        if any(len(windows[timeframe]) < WINDOW_LENGTHS[timeframe] for timeframe in WINDOW_LENGTHS):
            continue
        yield (
            builder.build(
                symbol=symbol,
                decision_time=decision_at,
                bars_by_timeframe=windows,
                source_ids=("binance_vision_ohlcv",),
            ),
            next_bar,
        )


def _split_pairs(pairs: Iterator[tuple[MarketContext, OHLCVBar]]) -> tuple[Iterator[MarketContext], Iterator[OHLCVBar]]:
    context_pairs, bar_pairs = tee(pairs)
    return (context for context, _ in context_pairs), (bar for _, bar in bar_pairs)


def _trades_payload(trades: tuple[ProposalReplayTrade, ...]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for trade in trades:
        item = asdict(trade)
        payload.append(
            {
                key: value.isoformat()
                if isinstance(value, datetime)
                else str(value)
                if isinstance(value, Decimal)
                else value
                for key, value in item.items()
            }
        )
    return payload


def _metric_payload(metrics: ProposalReplayMetrics) -> dict[str, Any]:
    payload = asdict(metrics)
    payload.pop("trades")
    return payload


def _max_drawdown(trades: tuple[ProposalReplayTrade, ...]) -> float:
    equity = Decimal("1")
    peak = equity
    drawdown = Decimal("0")
    for trade in trades:
        equity += trade.net_return
        peak = max(peak, equity)
        if peak > 0:
            drawdown = max(drawdown, (peak - equity) / peak)
    return float(drawdown)


def _trade_summary(trades: tuple[ProposalReplayTrade, ...]) -> dict[str, float | int | None]:
    returns = [trade.net_return for trade in trades]
    wins = [value for value in returns if value > 0]
    losses = [value for value in returns if value < 0]
    loss_total = abs(sum(losses, Decimal("0")))
    return {
        "total_trades": len(trades),
        "win_rate": float(len(wins) / len(trades)) if trades else 0.0,
        "average_profit_loss_ratio": float(sum(wins) / len(wins) / abs(sum(losses) / len(losses)))
        if wins and losses
        else None,
        "profit_factor": float(sum(wins, Decimal("0")) / loss_total) if loss_total else 0.0,
        "net_expectancy": float(sum(returns, Decimal("0")) / len(returns)) if returns else 0.0,
        "net_return": float(sum(returns, Decimal("0"))),
        "max_drawdown": _max_drawdown(trades),
    }


def _oos_boundaries() -> list[tuple[str, datetime, datetime]]:
    starts = [
        datetime(2024, 1, 29, tzinfo=UTC),
        datetime(2024, 4, 29, tzinfo=UTC),
        datetime(2024, 7, 29, tzinfo=UTC),
        datetime(2024, 10, 29, tzinfo=UTC),
        datetime(2025, 1, 29, tzinfo=UTC),
        datetime(2025, 4, 29, tzinfo=UTC),
        datetime(2025, 7, 29, tzinfo=UTC),
        datetime(2025, 10, 29, tzinfo=UTC),
    ]
    ends = starts[1:] + [FINAL_HOLDOUT_START]
    return [(f"oos_{index + 1}", start, end) for index, (start, end) in enumerate(zip(starts, ends, strict=True))]


def _bootstrap(trades: tuple[ProposalReplayTrade, ...]) -> dict[str, Any] | None:
    clusters: dict[tuple[int, int], list[Decimal]] = defaultdict(list)
    for trade in trades:
        iso = trade.closed_at.isocalendar()
        clusters[(iso.year, iso.week)].append(trade.net_return)
    if not clusters:
        return None
    return stationary_cluster_bootstrap_lcb(tuple(tuple(values) for values in clusters.values())).model_dump(
        mode="json"
    )


def _result_for_candidate(
    *,
    candidate_id: str,
    database_path: Path,
    ledger: TrialLedger,
) -> dict[str, Any]:
    guard = FinalHoldoutGuard(FINAL_HOLDOUT_START)
    guard.assert_development_end(FINAL_HOLDOUT_START)
    cost_model = ReplayCostModel()
    by_symbol: dict[str, ProposalReplayMetrics] = {}
    with sqlite3.connect(f"file:{database_path.resolve().as_posix()}?mode=ro", uri=True) as connection:
        funding_rows = connection.execute(
            "SELECT COUNT(*) FROM market_extras WHERE time >= ? AND time < ?",
            (
                WARMUP_START.replace(tzinfo=None).isoformat(sep=" "),
                FINAL_HOLDOUT_START.replace(tzinfo=None).isoformat(sep=" "),
            ),
        ).fetchone()[0]
        for symbol in SYMBOLS:
            series = {
                timeframe: _load_series(connection, symbol=symbol, timeframe=timeframe)
                for timeframe in TIMEFRAME_SECONDS
            }
            contexts, bars = _split_pairs(_contexts_and_next_bars(symbol=symbol, series=series))
            by_symbol[symbol] = ProposalReplayRunner(cost_model=cost_model).replay(
                strategy_id=candidate_id,
                contexts=contexts,
                next_entry_bars=bars,
                generator=GENERATORS[candidate_id],
            )
    all_trades = tuple(
        sorted((trade for result in by_symbol.values() for trade in result.trades), key=lambda trade: trade.closed_at)
    )
    portfolio_summary = _trade_summary(all_trades)
    portfolio = ProposalReplayMetrics(
        strategy_id=candidate_id,
        total_proposals=sum(value.total_proposals for value in by_symbol.values()),
        expired_proposals=sum(value.expired_proposals for value in by_symbol.values()),
        rejected_price_drift=sum(value.rejected_price_drift for value in by_symbol.values()),
        total_trades=int(portfolio_summary["total_trades"]),
        win_rate=float(portfolio_summary["win_rate"]),
        average_profit_loss_ratio=portfolio_summary["average_profit_loss_ratio"],
        profit_factor=float(portfolio_summary["profit_factor"]),
        net_expectancy=float(portfolio_summary["net_expectancy"]),
        net_return=float(portfolio_summary["net_return"]),
        max_drawdown=float(portfolio_summary["max_drawdown"]),
        funding_rate_available=False,
        trades=all_trades,
    )
    bootstrap = _bootstrap(all_trades)
    promotion = evaluate_promotion(
        PromotionMetrics(
            win_rate=portfolio.win_rate,
            average_profit_loss_ratio=portfolio.average_profit_loss_ratio or 0.0,
            profit_factor=portfolio.profit_factor,
            net_expectancy=portfolio.net_expectancy,
            max_drawdown=portfolio.max_drawdown,
            expectancy_lcb=float(bootstrap["expectancy_lcb"]) if bootstrap is not None else 0.0,
        )
    )
    ledger.record(
        trial_id=f"{candidate_id}:fixed-v1",
        strategy_id=candidate_id,
        parameters={
            "cost_model": {
                key: str(value) if isinstance(value, Decimal) else value for key, value in asdict(cost_model).items()
            },
            "development_end": FINAL_HOLDOUT_START.isoformat(),
        },
        status="completed",
    )
    return {
        "candidate_id": candidate_id,
        "symbols": {symbol: _metric_payload(value) for symbol, value in by_symbol.items()},
        "portfolio": _metric_payload(portfolio),
        "trades": _trades_payload(all_trades),
        "walk_forward_oos": [
            {
                "window_id": window_id,
                "start_at": start.isoformat(),
                "end_at": end.isoformat(),
                "metrics": _trade_summary(tuple(trade for trade in all_trades if start <= trade.closed_at < end)),
            }
            for window_id, start, end in _oos_boundaries()
        ],
        "stationary_cluster_bootstrap": bootstrap,
        "promotion": {
            "eligible": False,
            "failed_requirements": [
                *promotion.failed_requirements,
                "funding_history_not_applied",
                "runtime_replay_parity_unproven",
            ],
        },
        "funding_rows_detected": funding_rows,
        "funding_treatment": "not_applied; no point-in-time funding replay implementation",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=Path(".strategy_refactor_history.db"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite research output: {args.output}")
    if not args.database.is_file():
        raise FileNotFoundError(args.database)
    args.output.mkdir(parents=True)
    ledger = TrialLedger(args.output / "trial-ledger.jsonl")
    results = {
        candidate_id: _result_for_candidate(candidate_id=candidate_id, database_path=args.database, ledger=ledger)
        for candidate_id in GENERATORS
    }
    report = {
        "scope": {
            "symbols": SYMBOLS,
            "development_start": DEVELOPMENT_START.isoformat(),
            "final_holdout_start": FINAL_HOLDOUT_START.isoformat(),
        },
        "holdout_results_accessed": False,
        "cost_model": asdict(ReplayCostModel()),
        "results": results,
        "verdict": "NO_ACTIVE_STRATEGY",
    }
    (args.output / "proposal-research-report.json").write_text(
        json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8"
    )
    print(json.dumps({candidate: result["portfolio"] for candidate, result in results.items()}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
