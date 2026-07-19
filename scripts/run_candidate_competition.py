"""Produce a read-only 5-candidate x 3-symbol 70/30 OOS replay competition.

This script consumes stored OHLCV only.  It neither writes active-evidence
pointers nor changes an execution configuration, so its report cannot promote a
candidate into an automatic trading lane.
"""

from __future__ import annotations

import argparse
import json
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

from scripts.compute_signal_edge_stats import evidence_failure_reasons
from scripts.run_top20_technical_validation import _closed_four_hour_boundary, _load_stored, _template
from services.data.universe import TECHNICAL_RESEARCH_SYMBOLS
from services.strategy_library.candidates.registry import get_candidate, list_candidates
from services.validation.metrics import bootstrap_ci
from services.validation.technical_replay import TechnicalStrategyValidationService
from shared.models import Timeframe


def _oos_metrics(service: TechnicalStrategyValidationService, metrics):  # noqa: ANN001, ANN202
    if metrics.evaluation_start is None or metrics.evaluation_end is None:
        return metrics
    split_at = metrics.evaluation_start + (metrics.evaluation_end - metrics.evaluation_start) * 0.70
    return service._metrics_for_period(metrics, start_at=split_at, end_at=metrics.evaluation_end)


def _oos_periods_per_year(oos) -> float:  # noqa: ANN001
    """Replicate the annualization factor from TechnicalStrategyValidationService._metrics()."""
    trades = oos.trades
    if len(trades) >= 2:
        span_years = (trades[-1].closed_at - trades[0].closed_at).total_seconds() / (365.25 * 86400)
        return max(1.0, len(trades) / span_years) if span_years > 0 else float(len(trades))
    return 1.0


def _row(*, candidate_id: str, symbol: str, full, oos) -> dict:  # noqa: ANN001
    pnls = [float(t.net_return) for t in oos.trades]
    periods_per_year = _oos_periods_per_year(oos)
    sharpe_ci, expectancy_ci = bootstrap_ci(pnls, periods_per_year=periods_per_year)
    return {
        "candidate_id": candidate_id,
        "symbol": symbol,
        "sample_count": int(full.total_trades),
        "oos_sample_count": int(oos.total_trades),
        "oos_trade_count": len(pnls),
        "win_rate": float(oos.win_rate),
        "net_expectancy": float(oos.net_expectancy),
        "sharpe": float(oos.sharpe),
        "profit_factor": float(oos.profit_factor),
        "max_drawdown": float(oos.max_drawdown),
        "sharpe_ci_90": list(sharpe_ci),
        "expectancy_ci_90": list(expectancy_ci),
        "failed_reasons": evidence_failure_reasons(oos),
        "data_issues": list(oos.data_issues),
        "evaluation_start": oos.evaluation_start.isoformat() if oos.evaluation_start else None,
        "evaluation_end": oos.evaluation_end.isoformat() if oos.evaluation_end else None,
    }


def _run_combination(
    candidate_id: str,
    symbol: str,
    market_data: dict,
    warmup_bars: int,
) -> dict:
    """Replay one candidate/symbol pair in an isolated worker process."""
    service = TechnicalStrategyValidationService(oos_fraction=0.30, walk_forward_windows=3, max_workers=1)
    strategy = _template(
        strategy_key=f"offline_competition:{candidate_id}",
        rules=get_candidate(candidate_id).get_config(),
        timeframe=Timeframe.M15,
    )
    entry_bars = market_data["15m"]
    start_at = entry_bars[warmup_bars].timestamp
    end_at = entry_bars[-1].timestamp
    full = service.replay(
        strategy=strategy,
        market_data={symbol: market_data},
        start_at=start_at,
        end_at=end_at,
    )
    return _row(candidate_id=candidate_id, symbol=symbol, full=full, oos=_oos_metrics(service, full))


_SMALL_SAMPLE_THRESHOLD = 30


def _markdown(rows: list[dict], *, generated_at: datetime, days: int) -> str:
    lines = [
        "# 五候选策略公平竞赛报告",
        "",
        f"- Generated: {generated_at.isoformat()}",
        f"- Data: stored BTC/USDT, ETH/USDT, SOL/USDT OHLCV; {days}-day 70/30 chronological split",
        "- Scope: offline replay only; this report does not alter active execution configuration.",
        "- CI: 90% bootstrap confidence intervals (1000 resamples, percentile method).",
        "",
        "| candidate | symbol | samples | OOS samples | OOS win rate | OOS net expectancy | "
        "expectancy CI 90% | Sharpe | Sharpe CI 90% | PF | Max DD | failed reasons |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        s_lo, s_hi = row.get("sharpe_ci_90", [0.0, 0.0])
        e_lo, e_hi = row.get("expectancy_ci_90", [0.0, 0.0])
        lines.append(
            "| {candidate_id} | {symbol} | {sample_count} | {oos_sample_count} | {win_rate:.4f} | "
            "{net_expectancy:.6f} | [{e_lo:.6f}, {e_hi:.6f}] | "
            "{sharpe:.4f} | [{s_lo:.4f}, {s_hi:.4f}] | "
            "{profit_factor:.4f} | {max_drawdown:.4f} | {failed} |".format(
                **row,
                failed=", ".join(row["failed_reasons"]) or "none",
                s_lo=s_lo,
                s_hi=s_hi,
                e_lo=e_lo,
                e_hi=e_hi,
            )
        )

    small_sample_rows = [
        row for row in rows if row.get("oos_trade_count", row.get("oos_sample_count", 0)) < _SMALL_SAMPLE_THRESHOLD
    ]
    if small_sample_rows:
        lines.extend(["", "## ⚠️ 小样本警告", ""])
        for row in small_sample_rows:
            n = row.get("oos_trade_count", row.get("oos_sample_count", 0))
            lines.append(
                f"- ⚠️ 小样本警告：{row['candidate_id']} / {row['symbol']} "
                f"仅 {n} 笔 OOS 交易，置信区间较宽，建议积累更多样本后复核。"
            )

    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default="sqlite:///./.local_paper_console.db")
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--output-dir", type=Path, default=Path("docs/audits"))
    parser.add_argument("--symbols", nargs="+", default=list(TECHNICAL_RESEARCH_SYMBOLS))
    parser.add_argument("--candidate-ids", nargs="+", default=list_candidates())
    args = parser.parse_args()

    os.environ["POSTGRES_URL"] = args.database_url
    end_at = _closed_four_hour_boundary(datetime.now(UTC))
    symbols = tuple(str(symbol) for symbol in args.symbols)
    candidate_ids = tuple(str(candidate_id) for candidate_id in args.candidate_ids)
    unknown = sorted(set(candidate_ids) - set(list_candidates()))
    if unknown:
        raise SystemExit(f"unknown candidate ids: {', '.join(unknown)}")
    market_data = _load_stored(days=args.days, end_at=end_at, symbols=symbols)
    generated_at = datetime.now(UTC)
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{generated_at.date().isoformat()}-five-candidate-competition"
    json_path = output_dir / f"{stem}.json"
    markdown_path = output_dir / f"{stem}.md"
    rows: list[dict] = []
    json_path.write_text(
        json.dumps(
            {
                "status": "running",
                "started_at": generated_at.isoformat(),
                "completed_rows": rows,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    combinations = [
        (candidate_id, symbol, market_data[symbol])
        for candidate_id in candidate_ids
        for symbol in symbols
    ]
    with ProcessPoolExecutor(max_workers=min(3, len(combinations))) as executor:
        futures = {
            executor.submit(_run_combination, candidate_id, symbol, symbol_data, 80): (candidate_id, symbol)
            for candidate_id, symbol, symbol_data in combinations
        }
        for future in as_completed(futures):
            rows.append(future.result())
            json_path.write_text(
                json.dumps(
                    {
                        "status": "running",
                        "started_at": generated_at.isoformat(),
                        "completed_rows": rows,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

    rows.sort(key=lambda row: (row["net_expectancy"], row["oos_sample_count"]), reverse=True)
    payload = {
        "status": "completed",
        "generated_at": generated_at.isoformat(),
        "days": args.days,
        "symbols": list(symbols),
        "candidate_ids": list(candidate_ids),
        "split": "chronological 70/30",
        "rows": rows,
    }
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    markdown_path.write_text(_markdown(rows, generated_at=generated_at, days=args.days), encoding="utf-8")
    print(markdown_path)
    print(json_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
