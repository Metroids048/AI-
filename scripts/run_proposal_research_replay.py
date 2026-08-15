"""Run proposal candidates on the sealed pre-holdout BTC/ETH history only.

This command is deliberately research-only.  It reads the immutable history
database, writes an append-only trial ledger, and never imports an execution
service or active strategy manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from bisect import bisect_right
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from functools import partial
from itertools import tee
from pathlib import Path
from typing import Any

from scripts.generate_strategy_golden_baseline import source_tree_manifest
from services.strategy_library.canonical import canonical_hash
from services.strategy_library.context import MarketContext, MarketContextBuilder
from services.strategy_library.proposal_pipeline import CandidateEvaluator, run_proposal_pipeline
from services.validation.proposal_replay import (
    ProposalReplayMetrics,
    ProposalReplayRunner,
    ProposalReplayTrade,
    ReplayCostModel,
)
from services.validation.strategy_promotion import FinalHoldoutGuard, TrialLedger
from shared.models import Exchange, MarketExtras, OHLCVBar, Timeframe

SYMBOLS = ("BTC/USDT", "ETH/USDT")
FINAL_HOLDOUT_START = datetime(2026, 1, 29, tzinfo=UTC)
DEVELOPMENT_START = datetime(2023, 1, 29, tzinfo=UTC)
WARMUP_START = DEVELOPMENT_START - timedelta(days=15)
TIMEFRAME_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400}
WINDOW_LENGTHS = {"1m": 2, "5m": 2, "15m": 80, "1h": 80, "4h": 80}
REFERENCE_CANDIDATE_IDS = (
    "loss_aware_trend_pullback_v1",
    "breakout_continuation_v1",
    "failed_breakout_reversal_v1",
    "trend_pullback_v2",
    "range_sweep_reversion_v1",
)
GENERATION_NEXT_CANDIDATE_IDS = (
    "htf_trend_continuation_v1",
    "breakout_retest_v1",
    "donchian_breakout_retest_v1",
    "momentum_continuation_v1",
    "volatility_expansion_v1",
)
ALL_CANDIDATE_IDS = REFERENCE_CANDIDATE_IDS + GENERATION_NEXT_CANDIDATE_IDS
from services.validation.proposal_walk_forward import (
    ProposalWalkForwardWindow,
    build_proposal_walk_forward_windows,
)


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


@dataclass(frozen=True)
class RawFundingSeries:
    times: tuple[int, ...]
    rates: tuple[Decimal, ...]

    @property
    def observations_complete(self) -> bool:
        """Require the public funding series to retain its 8-hour cadence."""

        if len(self.times) < 2 or len(self.times) != len(self.rates):
            return False
        expected_interval = 8 * 60 * 60
        max_allowed_gap = expected_interval + 5 * 60
        return all(
            0 < later - earlier <= max_allowed_gap for earlier, later in zip(self.times, self.times[1:], strict=False)
        )

    def latest(self, *, decision_at: datetime, symbol: str) -> MarketExtras | None:
        index = bisect_right(self.times, int(decision_at.timestamp())) - 1
        if index < 0:
            return None
        return MarketExtras(
            symbol=symbol,
            time=datetime.fromtimestamp(self.times[index], UTC),
            funding_rate=self.rates[index],
        )

    def points(self) -> tuple[tuple[datetime, Decimal], ...]:
        return tuple(
            (datetime.fromtimestamp(timestamp, UTC), rate)
            for timestamp, rate in zip(self.times, self.rates, strict=True)
        )


def _parse_time(value: str) -> int:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return int((parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)).timestamp())


def _load_series(
    connection: sqlite3.Connection,
    *,
    symbol: str,
    timeframe: str,
    end_at: datetime = FINAL_HOLDOUT_START,
) -> RawSeries:
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
            end_at.replace(tzinfo=None).isoformat(sep=" "),
        ),
    ).fetchall()
    parsed_rows: tuple[tuple[float, float, float, float, float], ...] = tuple(
        (
            float(row[1]),
            float(row[2]),
            float(row[3]),
            float(row[4]),
            float(row[5]),
        )
        for row in rows
    )
    return RawSeries(
        timeframe=timeframe,
        times=tuple(_parse_time(str(row[0])) for row in rows),
        rows=parsed_rows,
    )


def _load_funding_series(
    connection: sqlite3.Connection,
    *,
    symbol: str,
    end_at: datetime = FINAL_HOLDOUT_START,
) -> RawFundingSeries:
    rows = connection.execute(
        """
        SELECT time, funding_rate
        FROM market_extras
        WHERE symbol = ? AND time >= ? AND time < ? AND funding_rate IS NOT NULL
        ORDER BY time
        """,
        (
            symbol,
            WARMUP_START.replace(tzinfo=None).isoformat(sep=" "),
            end_at.replace(tzinfo=None).isoformat(sep=" "),
        ),
    ).fetchall()
    return RawFundingSeries(
        times=tuple(_parse_time(str(row[0])) for row in rows),
        rates=tuple(Decimal(str(row[1])) for row in rows),
    )


def _contexts_and_next_bars(
    *,
    symbol: str,
    series: dict[str, RawSeries],
    funding: RawFundingSeries,
    evaluation_start: datetime = DEVELOPMENT_START,
    evaluation_end: datetime = FINAL_HOLDOUT_START,
) -> Iterator[tuple[MarketContext, OHLCVBar]]:
    entry = series["15m"]
    builder = MarketContextBuilder()
    for index in range(WINDOW_LENGTHS["15m"], len(entry.times) - 1):
        signal_bar = entry.bar_at(index, symbol=symbol)
        decision_at = signal_bar.timestamp + timedelta(minutes=15)
        next_bar = entry.bar_at(index + 1, symbol=symbol)
        if decision_at < evaluation_start or next_bar.timestamp >= evaluation_end:
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
                market_extras=tuple(filter(None, (funding.latest(decision_at=decision_at, symbol=symbol),))),
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
        "average_profit_loss_ratio": float(
            sum(wins, Decimal("0")) / Decimal(len(wins)) / abs(sum(losses, Decimal("0")) / Decimal(len(losses)))
        )
        if wins and losses
        else None,
        "profit_factor": float(sum(wins, Decimal("0")) / loss_total) if loss_total else 0.0,
        "net_expectancy": float(sum(returns, Decimal("0")) / len(returns)) if returns else 0.0,
        "net_return": float(sum(returns, Decimal("0"))),
        "max_drawdown": _max_drawdown(trades),
    }


def _walk_forward_windows() -> tuple[ProposalWalkForwardWindow, ...]:
    return build_proposal_walk_forward_windows(
        development_start=DEVELOPMENT_START,
        development_end=FINAL_HOLDOUT_START,
    )


def _build_window_runs(
    *,
    database_path: Path,
    windows: tuple[ProposalWalkForwardWindow, ...],
    candidate_ids: tuple[str, ...],
    evaluator_overrides: dict[str, CandidateEvaluator] | None = None,
    data_end: datetime = FINAL_HOLDOUT_START,
) -> tuple[dict[str, Any], ...]:
    runs: list[dict[str, Any]] = []
    with sqlite3.connect(f"file:{database_path.resolve().as_posix()}?mode=ro", uri=True) as connection:
        for symbol in SYMBOLS:
            funding = _load_funding_series(connection, symbol=symbol, end_at=data_end)
            cost_model = ReplayCostModel(
                taker_fee_bps_per_side=Decimal(str(_configured_taker_fee_bps())),
                funding_points=funding.points(),
                funding_rate_available=bool(funding.times) and funding.observations_complete,
                funding_observations_complete=funding.observations_complete,
                funding_provenance="OBSERVED" if funding.observations_complete else "UNAVAILABLE",
            )
            series = {
                timeframe: _load_series(connection, symbol=symbol, timeframe=timeframe, end_at=data_end)
                for timeframe in TIMEFRAME_SECONDS
            }
            # Memory note: materialising every (context, next_bar) pair for the
            # whole OOS span holds ~70k MarketContext objects per symbol, each
            # carrying 244 bar models, which exhausts a 32 GB host.  The walk
            # forward windows are disjoint and sorted, so each decision belongs
            # to at most one window and the stream can be bucketed in a single
            # pass.  Assignment uses the same predicate as before, so the set of
            # pairs replayed per window is unchanged; only object lifetime is.
            pair_stream = _contexts_and_next_bars(
                symbol=symbol,
                series=series,
                funding=funding,
                evaluation_start=windows[0].oos_start,
                evaluation_end=windows[-1].oos_end,
            )
            metrics_by_window: dict[int, ProposalReplayMetrics] = {}
            pending: list[tuple[MarketContext, OHLCVBar]] = []
            cursor = 0

            def _flush(
                index: int,
                pairs: list[tuple[MarketContext, OHLCVBar]],
                *,
                metrics_by_window=metrics_by_window,
                cost_model=cost_model,
            ) -> None:
                contexts, bars = zip(*pairs, strict=True) if pairs else ((), ())
                pipeline = partial(
                    run_proposal_pipeline,
                    candidate_ids=frozenset(candidate_ids),
                    evaluator_overrides=evaluator_overrides,
                )
                metrics_by_window[index] = ProposalReplayRunner(cost_model=cost_model).replay(
                    strategy_id="proposal_pipeline",
                    contexts=contexts,
                    next_entry_bars=bars,
                    pipeline=pipeline,
                    mode="v2_single_target",
                )

            for context, bar in pair_stream:
                decision_time = context.decision_time
                while cursor < len(windows) and decision_time >= windows[cursor].oos_end:
                    _flush(cursor, pending)
                    pending = []
                    cursor += 1
                if cursor >= len(windows):
                    break
                if decision_time >= windows[cursor].oos_start:
                    pending.append((context, bar))
            while cursor < len(windows):
                _flush(cursor, pending)
                pending = []
                cursor += 1

            for index, window in enumerate(windows):
                runs.append(
                    {
                        "window": window,
                        "symbol": symbol,
                        "metrics": metrics_by_window[index],
                        "cost_model": cost_model,
                    }
                )
    return tuple(runs)


def _configured_taker_fee_bps() -> float:
    from services.execution.bootstrap import AUTO_PAPER_TECHNICAL_RULES

    return float(AUTO_PAPER_TECHNICAL_RULES["entry_rules"]["core_fee_bps"])


def _candidate_metric_payload(
    *, trades: tuple[ProposalReplayTrade, ...], metrics: tuple[ProposalReplayMetrics, ...], candidate_id: str
) -> dict[str, Any]:
    summary = _trade_summary(trades)
    return {
        **summary,
        "selected_proposals": sum(item.proposal_counts.get(candidate_id, 0) for item in metrics),
        "expired_proposals": sum(item.expired_proposal_counts.get(candidate_id, 0) for item in metrics),
        "rejected_price_drift": sum(item.rejected_price_drift_counts.get(candidate_id, 0) for item in metrics),
        "funding_rate_available": all(item.funding_rate_available for item in metrics),
        "promotion_observations_complete": all(item.promotion_observations_complete for item in metrics),
        "cost_provenance": metrics[0].cost_provenance if metrics else {},
    }


def _result_for_candidate(
    *,
    candidate_id: str,
    window_runs: tuple[dict[str, Any], ...],
    ledger: TrialLedger,
    windows: tuple[ProposalWalkForwardWindow, ...] | None = None,
    ledger_strategy_id: str | None = None,
    ledger_parameters: dict[str, Any] | None = None,
    ledger_status: str = "phase1_observed_no_parameter_optimization",
) -> dict[str, Any]:
    active_windows = windows or _walk_forward_windows()
    all_trades = tuple(
        sorted(
            (trade for run in window_runs for trade in run["metrics"].trades if trade.strategy_id == candidate_id),
            key=lambda trade: trade.closed_at,
        )
    )
    portfolio_metrics = tuple(run["metrics"] for run in window_runs)
    portfolio_payload = _candidate_metric_payload(
        trades=all_trades, metrics=portfolio_metrics, candidate_id=candidate_id
    )
    symbol_payload: dict[str, Any] = {}
    window_payload: dict[str, Any] = {}
    for window in active_windows:
        window_payload[window.window_id] = {"window": window.as_record(), "symbols": {}}
        for symbol in SYMBOLS:
            matching = tuple(
                run for run in window_runs if run["window"].window_id == window.window_id and run["symbol"] == symbol
            )
            metrics = tuple(run["metrics"] for run in matching)
            trades = tuple(
                trade for run in matching for trade in run["metrics"].trades if trade.strategy_id == candidate_id
            )
            payload = _candidate_metric_payload(trades=trades, metrics=metrics, candidate_id=candidate_id)
            window_payload[window.window_id]["symbols"][symbol] = payload
            symbol_runs = tuple(run for run in window_runs if run["symbol"] == symbol)
            symbol_metrics = tuple(run["metrics"] for run in symbol_runs)
            symbol_trades = tuple(
                trade for run in symbol_runs for trade in run["metrics"].trades if trade.strategy_id == candidate_id
            )
            symbol_payload[symbol] = _candidate_metric_payload(
                trades=symbol_trades, metrics=symbol_metrics, candidate_id=candidate_id
            )
            trial_strategy_id = ledger_strategy_id or candidate_id
            ledger.record(
                trial_id=f"{trial_strategy_id}:{symbol}:{window.window_id}",
                strategy_id=trial_strategy_id,
                parameters={
                    "window": window.as_record(),
                    "symbol": symbol,
                    "metrics": payload,
                    "parameter_optimization": False,
                    **(ledger_parameters or {}),
                },
                status=ledger_status,
            )
    return {
        "candidate_id": candidate_id,
        "symbols": symbol_payload,
        "portfolio": portfolio_payload,
        "trades": _trades_payload(all_trades),
        "walk_forward_oos": window_payload,
        "promotion": {
            "eligible": False,
            "failed_requirements": [
                "phase1_does_not_run_final_promotion",
                "required_cost_observation_not_observed",
            ],
        },
        "funding_treatment": "settlement_events_from_point_in_time_market_extras_with_signed_side_cost",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=Path(".strategy_refactor_history.db"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--candidate-ids", nargs="*", default=list(GENERATION_NEXT_CANDIDATE_IDS))
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite research output: {args.output}")
    if not args.database.is_file():
        raise FileNotFoundError(args.database)
    args.output.mkdir(parents=True)
    ledger = TrialLedger(args.output / "trial-ledger.jsonl")
    guard = FinalHoldoutGuard(FINAL_HOLDOUT_START)
    guard.assert_development_end(FINAL_HOLDOUT_START)
    windows = _walk_forward_windows()
    candidate_ids = tuple(args.candidate_ids)
    unknown = set(candidate_ids) - set(ALL_CANDIDATE_IDS)
    if unknown:
        raise ValueError(f"unknown candidate ids: {sorted(unknown)}")
    window_runs = _build_window_runs(database_path=args.database, windows=windows, candidate_ids=candidate_ids)
    results = {
        candidate_id: _result_for_candidate(candidate_id=candidate_id, window_runs=window_runs, ledger=ledger)
        for candidate_id in candidate_ids
    }
    with sqlite3.connect(f"file:{args.database.resolve().as_posix()}?mode=ro", uri=True) as connection:
        funding_rows = connection.execute(
            "SELECT COUNT(*) FROM market_extras WHERE time >= ? AND time < ?",
            (
                WARMUP_START.replace(tzinfo=None).isoformat(sep=" "),
                FINAL_HOLDOUT_START.replace(tzinfo=None).isoformat(sep=" "),
            ),
        ).fetchone()[0]
    report = {
        "scope": {
            "symbols": SYMBOLS,
            "development_start": DEVELOPMENT_START.isoformat(),
            "final_holdout_start": FINAL_HOLDOUT_START.isoformat(),
        },
        "holdout_results_accessed": False,
        "cost_model": asdict(ReplayCostModel(taker_fee_bps_per_side=Decimal(str(_configured_taker_fee_bps())))),
        "results": results,
        "funding_rows_detected": funding_rows,
        "verdict": "NO_ACTIVE_STRATEGY",
    }
    database_hash = hashlib.sha256(args.database.read_bytes()).hexdigest()
    tree = source_tree_manifest(Path.cwd())
    config_payload = {
        "pipeline_version": "proposal-pipeline-v1",
        "candidate_ids": list(candidate_ids),
        "cost_model": {
            "taker_fee_bps_per_side": str(_configured_taker_fee_bps()),
            "spread_bps_per_side": "1",
            "latency_slippage_bps_per_side": "1",
            "partial_fill_fraction": "0.85",
            "funding_source": "market_extras",
            "funding_missing_policy": "UNAVAILABLE_AND_NOT_PROMOTABLE",
        },
        "walk_forward": {"train_months": 12, "oos_months": 3, "window_count": 8, "embargo_hours": 24},
        "final_holdout_results_accessed": False,
    }
    manifest = {
        "schema_version": 1,
        "artifact_type": "strategy_phase1_proposal_replay",
        "status": "NO_ACTIVE_STRATEGY",
        "generated_at": datetime.now(UTC).isoformat(),
        "holdout_results_accessed": False,
        "source_tree_hash": tree["source_tree_hash"],
        "data_hash": database_hash,
        "config_hash": canonical_hash(config_payload),
        "pipeline_version": "proposal-pipeline-v1",
        "artifact_files": [
            "PHASE1_MANIFEST.json",
            "source_tree_manifest.json",
            "config_manifest.json",
            "proposal-research-report.json",
            "trial-ledger.jsonl",
        ],
    }
    (args.output / "PHASE1_MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (args.output / "source_tree_manifest.json").write_text(
        json.dumps(tree, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (args.output / "config_manifest.json").write_text(
        json.dumps(config_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (args.output / "proposal-research-report.json").write_text(
        json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8"
    )
    print(json.dumps({candidate: result["portfolio"] for candidate, result in results.items()}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
