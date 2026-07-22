"""Read-only Review-layer A-E shadow-ablation audit over persisted decisions."""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from services.review.strategy_ablation import evaluate_shadow_variants

DIRECTIONAL_PIPELINE_STATUSES = frozenset(
    {
        "universe_status_rejected",
        "technical_signals_insufficient",
        "multi_timeframe_disagreement",
        "confirmation_unavailable_fail_closed",
        "ensemble_discarded",
        "meta_label_bet_skipped",
        "vetoed",
        "bet_taken",
    }
)


@dataclass(frozen=True)
class ShadowAuditRow:
    paper_run_id: str
    symbol: str
    cycle_time_utc: datetime
    pipeline_status: str
    variant: str
    candidate: bool | None
    side: str | None
    long_weight: float
    short_weight: float
    llm_advisory: bool
    reason: str
    evidence_gaps: tuple[str, ...]


@dataclass(frozen=True)
class ShadowVariantSummary:
    variant: str
    evaluated_count: int
    candidate_count: int
    blocked_count: int
    unknown_count: int
    long_count: int
    short_count: int


@dataclass(frozen=True)
class StrategyAblationReport:
    generated_at: datetime
    since: datetime
    database: str
    decision_count: int
    results: tuple[ShadowAuditRow, ...]
    variant_summaries: tuple[ShadowVariantSummary, ...]


def _as_utc(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _trace(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value))
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def run_audit(database: Path, *, since: datetime) -> StrategyAblationReport:
    resolved = database.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    since_utc = since.replace(tzinfo=UTC) if since.tzinfo is None else since.astimezone(UTC)
    connection = sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        raw_rows = connection.execute(
            "SELECT paper_run_id, symbol, pipeline_status, decision_trace, cycle_time "
            "FROM decision_snapshots ORDER BY cycle_time"
        ).fetchall()
    finally:
        connection.close()
    decisions: list[tuple[sqlite3.Row, dict[str, Any]]] = []
    for row in raw_rows:
        trace = _trace(row[3])
        status = str(trace.get("pipeline_status") or row[2] or "unknown")
        if _as_utc(row[4]) >= since_utc and status in DIRECTIONAL_PIPELINE_STATUSES:
            decisions.append((row, trace))
    results: list[ShadowAuditRow] = []
    for row, trace in decisions:
        if not trace.get("pipeline_status") and row[2] is not None:
            trace = {**trace, "pipeline_status": str(row[2])}
        for variant in evaluate_shadow_variants(trace):
            results.append(
                ShadowAuditRow(
                    paper_run_id=str(row[0]),
                    symbol=str(row[1]),
                    cycle_time_utc=_as_utc(row[4]),
                    pipeline_status=str(row[2] or "unknown"),
                    variant=variant.variant,
                    candidate=variant.candidate,
                    side=variant.side,
                    long_weight=variant.long_weight,
                    short_weight=variant.short_weight,
                    llm_advisory=variant.llm_advisory,
                    reason=variant.reason,
                    evidence_gaps=variant.evidence_gaps,
                )
            )
    by_variant: dict[str, list[ShadowAuditRow]] = {}
    for row in results:
        by_variant.setdefault(row.variant, []).append(row)
    summaries: list[ShadowVariantSummary] = []
    for variant, rows in by_variant.items():
        side_counts = Counter(row.side for row in rows if row.candidate is True)
        summaries.append(
            ShadowVariantSummary(
                variant=variant,
                evaluated_count=len(rows),
                candidate_count=sum(row.candidate is True for row in rows),
                blocked_count=sum(row.candidate is False for row in rows),
                unknown_count=sum(row.candidate is None for row in rows),
                long_count=side_counts["long"],
                short_count=side_counts["short"],
            )
        )
    return StrategyAblationReport(
        generated_at=datetime.now(UTC),
        since=since_utc,
        database=resolved.as_posix(),
        decision_count=len(decisions),
        results=tuple(results),
        variant_summaries=tuple(summaries),
    )


def _markdown(report: StrategyAblationReport) -> str:
    lines = [
        "# Strategy Shadow Ablation",
        "",
        f"- Generated: {report.generated_at.isoformat()}",
        f"- Since: {report.since.isoformat()}",
        f"- Decisions: {report.decision_count}",
        "- This is candidate recall only; PnL, 1R/2R, expectancy, and drawdown "
        "require persisted exits and complete outcome bars.",
        "- `unknown` is retained when historical traces cannot reconstruct a variant without guessing.",
        "",
        "| variant | evaluated | candidates | blocked | unknown | long | short |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in report.variant_summaries:
        lines.append(
            f"| {item.variant} | {item.evaluated_count} | {item.candidate_count} | "
            f"{item.blocked_count} | {item.unknown_count} | {item.long_count} | {item.short_count} |"
        )
    return "\n".join(lines) + "\n"


def write_artifacts(
    report: StrategyAblationReport,
    *,
    csv_path: Path,
    markdown_path: Path,
) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            (
                "paper_run_id",
                "symbol",
                "cycle_time_utc",
                "pipeline_status",
                "variant",
                "candidate",
                "side",
                "long_weight",
                "short_weight",
                "llm_advisory",
                "reason",
                "evidence_gaps",
            )
        )
        for row in report.results:
            writer.writerow(
                (
                    row.paper_run_id,
                    row.symbol,
                    row.cycle_time_utc.isoformat(),
                    row.pipeline_status,
                    row.variant,
                    "unknown" if row.candidate is None else str(row.candidate).lower(),
                    row.side or "",
                    row.long_weight,
                    row.short_weight,
                    str(row.llm_advisory).lower(),
                    row.reason,
                    json.dumps(row.evidence_gaps, ensure_ascii=False),
                )
            )
    markdown_path.write_text(_markdown(report), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path(".local_paper_console.db"))
    parser.add_argument("--lookback-days", type=float, default=7.0)
    parser.add_argument("--csv-path", type=Path, default=Path("artifacts/shadow-ablation-results.csv"))
    parser.add_argument("--report-path", type=Path, default=Path("docs/audit/shadow-ablation-report.md"))
    args = parser.parse_args()
    report = run_audit(args.database, since=datetime.now(UTC) - timedelta(days=args.lookback_days))
    write_artifacts(report, csv_path=args.csv_path, markdown_path=args.report_path)
    print(_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
