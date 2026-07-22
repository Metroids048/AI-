"""Read-only audit of decision-pipeline stages and Gatekeeper rejection codes."""

from __future__ import annotations

import argparse
import csv
import json
import os
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class FunnelStageRow:
    stage: str
    entered: int
    passed: int
    eliminated: int
    elimination_rate_percent: float


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
    metrics: dict[str, int]
    funnel_rows: list[FunnelStageRow]
    blocker_counts: dict[str, int]
    blocker_breakdowns: dict[str, dict[str, dict[str, int]]]


STATUS_STAGE = {
    "universe_status_rejected": "universe",
    "technical_signals_insufficient": "technical_signals",
    "multi_timeframe_disagreement": "multi_timeframe",
    "confirmation_unavailable_fail_closed": "multi_timeframe",
    "ensemble_discarded": "ensemble",
    "meta_label_bet_skipped": "meta_label",
    "vetoed": "llm_veto",
}

ENTRY_PIPELINE_STATUSES = frozenset(
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


def _signal_counts(trace: dict[str, Any]) -> Counter[str]:
    counts: Counter[str] = Counter()
    signals = trace.get("signals")
    if not isinstance(signals, list):
        return counts
    for signal in signals:
        if not isinstance(signal, dict):
            continue
        side = str(signal.get("side") or "").lower()
        if side in {"long", "short"}:
            counts[side] += 1
    return counts


def _direction(trace: dict[str, Any]) -> str:
    ensemble = trace.get("ensemble")
    if isinstance(ensemble, dict):
        fused_direction = str(ensemble.get("fused_direction") or "").lower()
        if fused_direction in {"long", "short"}:
            return fused_direction
    signal_counts = _signal_counts(trace)
    if signal_counts["long"] > signal_counts["short"]:
        return "long"
    if signal_counts["short"] > signal_counts["long"]:
        return "short"
    return "mixed" if signal_counts["long"] else "none"


def _regime(trace: dict[str, Any]) -> str:
    volatility = trace.get("volatility")
    if isinstance(volatility, dict):
        value = volatility.get("regime")
        if value:
            return str(value)
    return "unknown"


def _hour_utc(value: Any) -> str:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value))
        except ValueError:
            return "unknown"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).strftime("%Y-%m-%dT%H:00Z")


def _final_blocker(*, status: str, action: str, reason: str | None, trace: dict[str, Any]) -> str | None:
    if status in {"universe_status_rejected", "technical_signals_insufficient"}:
        return "NO_BASE_SIGNAL"
    if status in {"multi_timeframe_disagreement", "confirmation_unavailable_fail_closed"}:
        return "MTF_DISAGREEMENT"
    if status == "ensemble_discarded":
        return "ENSEMBLE_DISCARD"
    if status == "meta_label_bet_skipped":
        return "META_LABEL_SKIP"
    veto_result = trace.get("veto_result")
    if status == "vetoed" or (isinstance(veto_result, dict) and veto_result.get("veto") is True):
        return "LLM_VETO"
    if status == "bet_taken" and action == "rejected":
        return "RISK_BLOCK"
    if status == "bet_taken" and not action.startswith("open_"):
        return "POST_PIPELINE_NO_INTENT"
    return None


def _funnel_row(stage: str, entered: int, passed: int) -> FunnelStageRow:
    eliminated = max(entered - passed, 0)
    return FunnelStageRow(
        stage=stage,
        entered=entered,
        passed=passed,
        eliminated=eliminated,
        elimination_rate_percent=round(eliminated / entered * 100.0, 2) if entered else 0.0,
    )


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
                "SELECT ds.paper_run_id, ds.symbol, ds.action, ds.pipeline_status, ds.reason, "
                "ds.decision_trace, ds.cycle_time "
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
    entry_rows: list[tuple[Any, str, str, str, str | None, dict[str, Any], Any]] = []
    for row in decisions:
        status = row.pipeline_status
        status_counts[str(status or "unknown")] += 1
        trace = _json_object(row.decision_trace)
        stage_counts[_stage(status=status, action=str(row.action), trace=trace)] += 1
        normalized_status = str(status or "unknown")
        if normalized_status in ENTRY_PIPELINE_STATUSES:
            entry_rows.append(
                (
                    row.paper_run_id,
                    str(row.symbol),
                    str(row.action),
                    normalized_status,
                    str(row.reason) if row.reason is not None else None,
                    trace,
                    row.cycle_time,
                )
            )
    rejection_counts: Counter[str] = Counter()
    for row in orders:
        rejection_counts.update(_json_list(row.rejection_codes))

    entry_status_counts = Counter(
        str(row.pipeline_status)
        for row in decisions
        if str(row.pipeline_status or "unknown") in ENTRY_PIPELINE_STATUSES
    )
    raw_signal_counts: Counter[str] = Counter()
    blocker_counts: Counter[str] = Counter()
    blocker_breakdowns: dict[str, dict[str, Counter[str]]] = {
        "symbol": {},
        "hour_utc": {},
        "regime": {},
        "direction": {},
    }
    trade_intents = 0
    unique_cycles: set[tuple[str, str]] = set()
    for paper_run_id, symbol, action, status, reason, trace, cycle_time in entry_rows:
        raw_signal_counts.update(_signal_counts(trace))
        if action.startswith("open_"):
            trade_intents += 1
        unique_cycles.add((str(paper_run_id), str(cycle_time)))
        blocker = _final_blocker(
            status=status,
            action=action,
            reason=reason,
            trace=trace,
        )
        if blocker is None:
            continue
        blocker_counts[blocker] += 1
        dimensions = {
            "symbol": symbol,
            "hour_utc": _hour_utc(cycle_time),
            "regime": _regime(trace),
            "direction": _direction(trace),
        }
        for dimension, value in dimensions.items():
            blocker_breakdowns[dimension].setdefault(value, Counter())[blocker] += 1

    total_entry_evaluations = len(entry_rows)
    no_base_signal = (
        entry_status_counts["universe_status_rejected"] + entry_status_counts["technical_signals_insufficient"]
    )
    mtf_disagreement = (
        entry_status_counts["multi_timeframe_disagreement"]
        + entry_status_counts["confirmation_unavailable_fail_closed"]
    )
    mtf_evaluated = total_entry_evaluations - no_base_signal
    mtf_pass = mtf_evaluated - mtf_disagreement
    ensemble_discard = entry_status_counts["ensemble_discarded"]
    ensemble_evaluated = mtf_pass
    ensemble_pass = ensemble_evaluated - ensemble_discard
    meta_label_skip = entry_status_counts["meta_label_bet_skipped"]
    meta_label_evaluated = ensemble_pass
    meta_label_pass = meta_label_evaluated - meta_label_skip
    llm_veto = entry_status_counts["vetoed"]
    llm_evaluated = meta_label_pass
    llm_pass = llm_evaluated - llm_veto
    risk_evaluated = llm_pass
    risk_block = blocker_counts["RISK_BLOCK"] + blocker_counts["POST_PIPELINE_NO_INTENT"]
    risk_pass = max(risk_evaluated - risk_block, 0)
    metrics = {
        "cycles": len(unique_cycles),
        "symbols_evaluated": total_entry_evaluations,
        "raw_long_signals": raw_signal_counts["long"],
        "raw_short_signals": raw_signal_counts["short"],
        "no_base_signal": no_base_signal,
        "mtf_evaluated": mtf_evaluated,
        "mtf_pass": mtf_pass,
        "mtf_disagreement": mtf_disagreement,
        "ensemble_evaluated": ensemble_evaluated,
        "ensemble_pass": ensemble_pass,
        "ensemble_discard": ensemble_discard,
        "meta_label_evaluated": meta_label_evaluated,
        "meta_label_pass": meta_label_pass,
        "meta_label_skip": meta_label_skip,
        "llm_evaluated": llm_evaluated,
        "llm_pass": llm_pass,
        "llm_veto": llm_veto,
        "risk_evaluated": risk_evaluated,
        "risk_pass": risk_pass,
        "risk_block": risk_block,
        "trade_intents": trade_intents,
    }
    funnel_rows = [
        _funnel_row("base_signal", total_entry_evaluations, mtf_evaluated),
        _funnel_row("multi_timeframe", mtf_evaluated, mtf_pass),
        _funnel_row("ensemble", ensemble_evaluated, ensemble_pass),
        _funnel_row("meta_label", meta_label_evaluated, meta_label_pass),
        _funnel_row("llm_veto", llm_evaluated, llm_pass),
        _funnel_row("gatekeeper", risk_evaluated, risk_pass),
        _funnel_row("trade_intent", trade_intents, trade_intents),
    ]
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
        metrics=metrics,
        funnel_rows=funnel_rows,
        blocker_counts=dict(sorted(blocker_counts.items())),
        blocker_breakdowns={
            dimension: {value: dict(sorted(counts.items())) for value, counts in sorted(values.items())}
            for dimension, values in blocker_breakdowns.items()
        },
    )


def _markdown(report: FunnelAuditReport) -> str:
    lines = [
        "# Strategy Liveness Funnel",
        "",
        f"- Generated: {report.generated_at}",
        f"- Since: {report.since}",
        f"- Strategy: {report.strategy_key or 'all'}",
        f"- Persisted decision snapshots: {report.total_decisions}",
        f"- Entry evaluations: {report.metrics.get('symbols_evaluated', 0)}",
        "- Actual pipeline order: base signal -> multi-timeframe -> ensemble -> "
        "meta-label -> LLM -> Gatekeeper -> TradeIntent",
        "",
        "## Sequential Funnel",
        "",
        "| stage | entered | passed | eliminated | elimination rate |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for row in report.funnel_rows:
        lines.append(
            f"| {row.stage} | {row.entered} | {row.passed} | {row.eliminated} | {row.elimination_rate_percent:.2f}% |"
        )
    lines.extend(["", "## Core Metrics", "", "| metric | count |", "| --- | ---: |"])
    for metric, count in report.metrics.items():
        lines.append(f"| {metric} | {count} |")
    lines.extend(["", "## Final Blockers", "", "| blocker | count |", "| --- | ---: |"])
    for blocker, count in report.blocker_counts.items():
        lines.append(f"| {blocker} | {count} |")
    lines.extend(["", "## Gatekeeper Rejections", "", "| code | count |", "| --- | ---: |"])
    for code, count in report.rejection_code_counts.items():
        lines.append(f"| {code} | {count} |")
    lines.extend(["", "## Terminal Pipeline Statuses", "", "| status | count |", "| --- | ---: |"])
    for status, count in report.pipeline_status_counts.items():
        lines.append(f"| {status} | {count} |")
    for dimension, values in report.blocker_breakdowns.items():
        lines.extend(
            [
                "",
                f"## Blockers By {dimension}",
                "",
                f"| {dimension} | blocker | count |",
                "| --- | --- | ---: |",
            ]
        )
        for value, counts in values.items():
            for blocker, count in counts.items():
                lines.append(f"| {value} | {blocker} | {count} |")
    return "\n".join(lines) + "\n"


def write_artifacts(
    report: FunnelAuditReport,
    *,
    funnel_csv_path: Path,
    blocker_csv_path: Path,
    markdown_path: Path,
) -> None:
    for path in (funnel_csv_path, blocker_csv_path, markdown_path):
        path.parent.mkdir(parents=True, exist_ok=True)
    with funnel_csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("stage", "entered", "passed", "eliminated", "elimination_rate_percent"))
        for row in report.funnel_rows:
            writer.writerow((row.stage, row.entered, row.passed, row.eliminated, row.elimination_rate_percent))
    with blocker_csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("dimension", "dimension_value", "blocker", "count", "percent_of_dimension"))
        for dimension, values in report.blocker_breakdowns.items():
            for value, counts in values.items():
                dimension_total = sum(counts.values())
                for blocker, count in counts.items():
                    percentage = round(count / dimension_total * 100.0, 2) if dimension_total else 0.0
                    writer.writerow((dimension, value, blocker, count, percentage))
    markdown_path.write_text(_markdown(report), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.environ.get("POSTGRES_URL", "sqlite:///.local_paper_console.db"))
    parser.add_argument("--lookback-days", type=int, default=7)
    parser.add_argument("--since", default=None, help="ISO-8601 timestamp; overrides --lookback-days")
    parser.add_argument("--strategy-key", default=None)
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument("--write-artifacts", action="store_true")
    parser.add_argument("--artifact-dir", type=Path, default=Path("artifacts"))
    parser.add_argument("--report-path", type=Path, default=Path("docs/audit/strategy-liveness-funnel.md"))
    args = parser.parse_args()
    since = datetime.fromisoformat(args.since) if args.since else datetime.now(UTC) - timedelta(days=args.lookback_days)
    if since.tzinfo is None:
        since = since.replace(tzinfo=UTC)
    report = run_audit(database_url=args.database_url, since=since, strategy_key=args.strategy_key)
    if args.write_artifacts:
        write_artifacts(
            report,
            funnel_csv_path=args.artifact_dir / "strategy-decision-funnel.csv",
            blocker_csv_path=args.artifact_dir / "blocker-distribution.csv",
            markdown_path=args.report_path,
        )
    print(json.dumps(asdict(report), indent=2) if args.format == "json" else _markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
