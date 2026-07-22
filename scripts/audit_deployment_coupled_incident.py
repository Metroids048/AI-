"""Read-only incident timeline extraction for deployment-coupled Paper trading.

Layer: Review. This module never imports an execution adapter and opens SQLite
with ``mode=ro`` so an audit cannot submit orders or mutate the runtime ledger.
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class TradeTimelineRow:
    order_execution_id: str
    created_at_utc: datetime
    created_at_local: datetime
    strategy_id: str
    strategy_key: str | None
    symbol: str
    direction: str
    close_only_mode: bool
    close_reason: str | None
    cycle_id: str | None
    decision_id: str | None
    signal_candle_close_time: str | None
    gateway_name: str | None
    gateway_order_id: str | None
    gateway_status: str | None
    rejection_reason: str | None
    normalized_order: dict[str, Any]
    entry_context: dict[str, Any]


@dataclass(frozen=True)
class TradeCluster:
    started_at_utc: datetime
    ended_at_utc: datetime
    started_at_local: datetime
    ended_at_local: datetime
    order_count: int
    entry_count: int
    exit_count: int
    duplicate_cycle_ids: tuple[str, ...]


@dataclass(frozen=True)
class BlockerWindow:
    cluster_ended_at_utc: datetime
    next_cluster_started_at_utc: datetime | None
    first_decision_at_utc: datetime | None
    first_pipeline_status: str | None
    first_reason: str | None
    first_symbol: str | None
    decision_count: int
    reason_counts: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class _DecisionRow:
    cycle_time_utc: datetime
    symbol: str
    action: str
    pipeline_status: str | None
    reason: str | None


@dataclass(frozen=True)
class IncidentAudit:
    database: str
    timezone_name: str
    trade_timeline: tuple[TradeTimelineRow, ...]
    trade_clusters: tuple[TradeCluster, ...]
    cycle_times_utc: tuple[str, ...]
    blocker_windows: tuple[BlockerWindow, ...]


def _parse_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _as_utc(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
            (table,),
        ).fetchone()
        is not None
    )


def _first_text(*values: Any) -> str | None:
    for value in values:
        if value is not None and str(value).strip():
            return str(value)
    return None


def _load_trade_timeline(
    connection: sqlite3.Connection,
    *,
    local_timezone: ZoneInfo,
) -> tuple[TradeTimelineRow, ...]:
    if not _table_exists(connection, "order_executions"):
        return ()
    strategy_keys: dict[str, str] = {}
    if _table_exists(connection, "strategies"):
        strategy_keys = {
            str(row[0]): str(row[1]) for row in connection.execute("SELECT id, strategy_key FROM strategies").fetchall()
        }
    rows = connection.execute("SELECT * FROM order_executions ORDER BY created_at").fetchall()
    timeline: list[TradeTimelineRow] = []
    for row in rows:
        record = dict(row)
        context = _parse_json(record.get("entry_context"))
        normalized = _parse_json(record.get("normalized_order"))
        created_at = _as_utc(record["created_at"])
        close_reason = _first_text(
            context.get("close_reason"),
            context.get("paper_runtime_action") if bool(record.get("close_only_mode")) else None,
            normalized.get("close_reason"),
        )
        timeline.append(
            TradeTimelineRow(
                order_execution_id=str(record.get("order_execution_id") or ""),
                created_at_utc=created_at,
                created_at_local=created_at.astimezone(local_timezone),
                strategy_id=str(record.get("strategy_id") or ""),
                strategy_key=strategy_keys.get(str(record.get("strategy_id") or "")),
                symbol=str(record.get("symbol") or ""),
                direction=str(record.get("direction") or ""),
                close_only_mode=bool(record.get("close_only_mode")),
                close_reason=close_reason,
                cycle_id=_first_text(record.get("cycle_id")),
                decision_id=_first_text(record.get("decision_id")),
                signal_candle_close_time=_first_text(
                    context.get("signal_candle_close_time"),
                    normalized.get("signal_candle_close_time"),
                ),
                gateway_name=_first_text(record.get("gateway_name")),
                gateway_order_id=_first_text(record.get("gateway_order_id")),
                gateway_status=_first_text(record.get("gateway_status")),
                rejection_reason=_first_text(record.get("rejection_reason")),
                normalized_order=normalized,
                entry_context=context,
            )
        )
    return tuple(timeline)


def _cluster_trades(
    timeline: tuple[TradeTimelineRow, ...],
    *,
    cluster_gap: timedelta,
) -> tuple[TradeCluster, ...]:
    if not timeline:
        return ()
    grouped: list[list[TradeTimelineRow]] = [[timeline[0]]]
    for row in timeline[1:]:
        if row.created_at_utc - grouped[-1][-1].created_at_utc > cluster_gap:
            grouped.append([row])
        else:
            grouped[-1].append(row)
    clusters: list[TradeCluster] = []
    for rows in grouped:
        cycle_counts = Counter(row.cycle_id for row in rows if row.cycle_id)
        clusters.append(
            TradeCluster(
                started_at_utc=rows[0].created_at_utc,
                ended_at_utc=rows[-1].created_at_utc,
                started_at_local=rows[0].created_at_local,
                ended_at_local=rows[-1].created_at_local,
                order_count=len(rows),
                entry_count=sum(not row.close_only_mode for row in rows),
                exit_count=sum(row.close_only_mode for row in rows),
                duplicate_cycle_ids=tuple(sorted(key for key, count in cycle_counts.items() if count > 1)),
            )
        )
    return tuple(clusters)


def _load_decisions(connection: sqlite3.Connection) -> tuple[_DecisionRow, ...]:
    if not _table_exists(connection, "decision_snapshots"):
        return ()
    rows = connection.execute(
        "SELECT cycle_time, symbol, action, pipeline_status, reason FROM decision_snapshots ORDER BY cycle_time"
    ).fetchall()
    return tuple(
        _DecisionRow(
            cycle_time_utc=_as_utc(row[0]),
            symbol=str(row[1] or ""),
            action=str(row[2] or ""),
            pipeline_status=_first_text(row[3]),
            reason=_first_text(row[4]),
        )
        for row in rows
    )


def _blocker_windows(
    clusters: tuple[TradeCluster, ...],
    decisions: tuple[_DecisionRow, ...],
) -> tuple[BlockerWindow, ...]:
    windows: list[BlockerWindow] = []
    for index, cluster in enumerate(clusters):
        next_start = clusters[index + 1].started_at_utc if index + 1 < len(clusters) else None
        matching = tuple(
            row
            for row in decisions
            if row.cycle_time_utc > cluster.ended_at_utc and (next_start is None or row.cycle_time_utc < next_start)
        )
        reasons = Counter(
            f"{row.pipeline_status or 'unknown'}:{row.reason or row.action or 'unknown'}" for row in matching
        )
        first = matching[0] if matching else None
        windows.append(
            BlockerWindow(
                cluster_ended_at_utc=cluster.ended_at_utc,
                next_cluster_started_at_utc=next_start,
                first_decision_at_utc=first.cycle_time_utc if first else None,
                first_pipeline_status=first.pipeline_status if first else None,
                first_reason=first.reason if first else None,
                first_symbol=first.symbol if first else None,
                decision_count=len(matching),
                reason_counts=tuple(sorted(reasons.items(), key=lambda item: (-item[1], item[0]))),
            )
        )
    return tuple(windows)


def analyze_runtime_ledger(
    database: Path,
    *,
    timezone_name: str = "Asia/Shanghai",
    cluster_gap_minutes: int = 30,
) -> IncidentAudit:
    resolved = database.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    connection = sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        local_timezone = ZoneInfo(timezone_name)
        timeline = _load_trade_timeline(connection, local_timezone=local_timezone)
        clusters = _cluster_trades(
            timeline,
            cluster_gap=timedelta(minutes=cluster_gap_minutes),
        )
        decisions = _load_decisions(connection)
        return IncidentAudit(
            database=resolved.as_posix(),
            timezone_name=timezone_name,
            trade_timeline=timeline,
            trade_clusters=clusters,
            cycle_times_utc=tuple(sorted({row.cycle_time_utc.isoformat() for row in decisions})),
            blocker_windows=_blocker_windows(clusters, decisions),
        )
    finally:
        connection.close()


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def write_audit_artifacts(audit: IncidentAudit, output_dir: Path) -> tuple[Path, ...]:
    """Write deterministic Review-layer evidence files from the read-only audit."""
    output_dir.mkdir(parents=True, exist_ok=True)
    timeline_path = output_dir / "trade-burst-timeline.csv"
    with timeline_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            (
                "created_at_utc",
                "created_at_local",
                "order_execution_id",
                "strategy_key",
                "symbol",
                "direction",
                "close_only_mode",
                "close_reason",
                "cycle_id",
                "gateway_order_id",
                "gateway_status",
                "execution_kind",
            )
        )
        for row in audit.trade_timeline:
            writer.writerow(
                (
                    row.created_at_utc.isoformat(),
                    row.created_at_local.isoformat(),
                    row.order_execution_id,
                    row.strategy_key,
                    row.symbol,
                    row.direction,
                    row.close_only_mode,
                    row.close_reason,
                    row.cycle_id,
                    row.gateway_order_id,
                    row.gateway_status,
                    row.entry_context.get("execution_kind"),
                )
            )

    liveness_path = output_dir / "cycle-liveness.csv"
    cycle_times = [_as_utc(value) for value in audit.cycle_times_utc]
    with liveness_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(("cycle_time_utc", "cycle_time_local", "gap_minutes_from_previous"))
        previous: datetime | None = None
        local_timezone = ZoneInfo(audit.timezone_name)
        for cycle_time in cycle_times:
            gap = (cycle_time - previous).total_seconds() / 60 if previous is not None else None
            writer.writerow(
                (
                    cycle_time.isoformat(),
                    cycle_time.astimezone(local_timezone).isoformat(),
                    f"{gap:.3f}" if gap is not None else "",
                )
            )
            previous = cycle_time

    blockers_path = output_dir / "blocker-after-burst.csv"
    with blockers_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            (
                "cluster_ended_at_utc",
                "next_cluster_started_at_utc",
                "first_decision_at_utc",
                "first_pipeline_status",
                "first_reason",
                "decision_count",
                "reason",
                "count",
            )
        )
        for window in audit.blocker_windows:
            reason_counts = window.reason_counts or (("", 0),)
            for reason, count in reason_counts:
                writer.writerow(
                    (
                        window.cluster_ended_at_utc.isoformat(),
                        window.next_cluster_started_at_utc.isoformat() if window.next_cluster_started_at_utc else "",
                        window.first_decision_at_utc.isoformat() if window.first_decision_at_utc else "",
                        window.first_pipeline_status,
                        window.first_reason,
                        window.decision_count,
                        reason,
                        count,
                    )
                )
    return timeline_path, liveness_path, blockers_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path(".local_runtime_ledger.db"))
    parser.add_argument("--timezone", default="Asia/Shanghai")
    parser.add_argument("--cluster-gap-minutes", type=int, default=30)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    audit = analyze_runtime_ledger(
        args.database,
        timezone_name=args.timezone,
        cluster_gap_minutes=args.cluster_gap_minutes,
    )
    if args.output_dir is not None:
        paths = write_audit_artifacts(audit, args.output_dir)
        print(json.dumps({"artifacts": [path.as_posix() for path in paths]}, ensure_ascii=False))
    else:
        print(json.dumps(asdict(audit), indent=2, ensure_ascii=False, default=_json_default))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
