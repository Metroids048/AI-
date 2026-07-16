"""Read-only audit of decision-pipeline stages and Gatekeeper rejection codes."""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any


@dataclass(frozen=True)
class FunnelAuditReport:
    generated_at: str
    since: str
    strategy_key: str | None
    total_decisions: int
    stage_counts: dict[str, int]
    stage_percentages: dict[str, float]
    pipeline_status_counts: dict[str, int]
    rejection_code_counts: dict[str, int]


STATUS_STAGE = {
    "universe_status_rejected": "universe",
    "technical_signals_insufficient": "technical_signals",
    "multi_timeframe_disagreement": "multi_timeframe",
    "confirmation_unavailable_fail_closed": "multi_timeframe",
    "ensemble_discarded": "ensemble",
    "meta_label_bet_skipped": "meta_label",
    "vetoed": "llm_veto",
}


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _json_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [str(item) for item in parsed]
        except json.JSONDecodeError:
            return [value]
    return []


def _stage(*, status: str | None, action: str, trace: dict[str, Any]) -> str:
    if action.startswith("open_"):
        return "opened"
    if action.startswith("close_"):
        return "closed"
    if trace.get("edge_stats_source") == "validated_edge_stats_missing_or_stale":
        return "validated_edge"
    return STATUS_STAGE.get(str(status), "gatekeeper" if action == "rejected" else "other")


def run_audit(
    *,
    database_url: str,
    since: datetime,
    strategy_key: str | None = None,
) -> FunnelAuditReport:
    from sqlalchemy import create_engine, text

    engine = create_engine(database_url)
    since_param = since.replace(tzinfo=None).isoformat(sep=" ") if database_url.startswith("sqlite") else since
    strategy_clause = " AND s.strategy_key = :strategy_key" if strategy_key else ""
    params: dict[str, Any] = {"since": since_param}
    if strategy_key:
        params["strategy_key"] = strategy_key
    with engine.connect() as connection:
        decisions = connection.execute(
            text(
                "SELECT ds.action, ds.pipeline_status, ds.decision_trace "
                "FROM decision_snapshots ds "
                "JOIN paper_runs pr ON pr.paper_run_id = ds.paper_run_id "
                "JOIN strategies s ON s.id = pr.strategy_id "
                "WHERE ds.cycle_time >= :since" + strategy_clause
            ),
            params,
        ).fetchall()
        orders = connection.execute(
            text(
                "SELECT oe.rejection_codes FROM order_executions oe "
                "JOIN paper_runs pr ON pr.paper_run_id = oe.paper_run_id "
                "JOIN strategies s ON s.id = pr.strategy_id "
                "WHERE oe.created_at >= :since" + strategy_clause
            ),
            params,
        ).fetchall()

    stage_counts: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    for row in decisions:
        status = row.pipeline_status
        status_counts[str(status or "unknown")] += 1
        stage_counts[_stage(status=status, action=str(row.action), trace=_json_object(row.decision_trace))] += 1
    rejection_counts: Counter[str] = Counter()
    for row in orders:
        rejection_counts.update(_json_list(row.rejection_codes))
    total = len(decisions)
    return FunnelAuditReport(
        generated_at=datetime.now(UTC).isoformat(),
        since=since.isoformat(),
        strategy_key=strategy_key,
        total_decisions=total,
        stage_counts=dict(sorted(stage_counts.items())),
        stage_percentages={key: round(value / total * 100.0, 2) for key, value in sorted(stage_counts.items())}
        if total
        else {},
        pipeline_status_counts=dict(sorted(status_counts.items())),
        rejection_code_counts=dict(sorted(rejection_counts.items())),
    )


def _markdown(report: FunnelAuditReport) -> str:
    lines = [
        "# Decision Funnel Audit",
        "",
        f"- Generated: {report.generated_at}",
        f"- Since: {report.since}",
        f"- Strategy: {report.strategy_key or 'all'}",
        f"- Decisions: {report.total_decisions}",
        "",
        "## Funnel",
        "",
        "| stage | count | percent |",
        "| --- | ---: | ---: |",
    ]
    for stage, count in report.stage_counts.items():
        lines.append(f"| {stage} | {count} | {report.stage_percentages[stage]:.2f}% |")
    lines.extend(["", "## Gatekeeper Rejections", "", "| code | count |", "| --- | ---: |"])
    for code, count in report.rejection_code_counts.items():
        lines.append(f"| {code} | {count} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.environ.get("POSTGRES_URL", "sqlite:///.local_paper_console.db"))
    parser.add_argument("--lookback-days", type=int, default=7)
    parser.add_argument("--since", default=None, help="ISO-8601 timestamp; overrides --lookback-days")
    parser.add_argument("--strategy-key", default=None)
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    args = parser.parse_args()
    since = datetime.fromisoformat(args.since) if args.since else datetime.now(UTC) - timedelta(days=args.lookback_days)
    if since.tzinfo is None:
        since = since.replace(tzinfo=UTC)
    report = run_audit(database_url=args.database_url, since=since, strategy_key=args.strategy_key)
    print(json.dumps(asdict(report), indent=2) if args.format == "json" else _markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
