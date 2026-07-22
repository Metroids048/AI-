from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from scripts.audit_deployment_coupled_incident import analyze_runtime_ledger, write_audit_artifacts


def _seed_incident_ledger(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE order_executions (
                order_execution_id TEXT PRIMARY KEY,
                strategy_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                execution_status TEXT NOT NULL,
                cycle_id TEXT,
                decision_id TEXT,
                normalized_order TEXT,
                close_only_mode INTEGER NOT NULL,
                rejection_reason TEXT,
                rejection_codes TEXT,
                entry_context TEXT,
                gateway_name TEXT,
                gateway_order_id TEXT,
                gateway_status TEXT,
                lifecycle_history TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE strategies (
                id TEXT PRIMARY KEY,
                strategy_key TEXT NOT NULL
            );
            CREATE TABLE decision_snapshots (
                decision_snapshot_id TEXT PRIMARY KEY,
                paper_run_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                action TEXT NOT NULL,
                pipeline_status TEXT,
                reason TEXT,
                decision_trace TEXT,
                cycle_time TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE risk_events (
                risk_event_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                severity TEXT NOT NULL,
                source TEXT NOT NULL,
                description TEXT NOT NULL,
                affected_scope TEXT,
                recommended_action TEXT,
                created_at TEXT NOT NULL
            );
            """
        )
        connection.execute("INSERT INTO strategies VALUES ('strategy-1', 'auto-paper-v1')")
        orders = [
            ("o1", "2026-07-21 03:00:00", "open", 0, "cycle-a"),
            ("o2", "2026-07-21 03:05:00", "opposite_signal", 1, "cycle-a"),
            ("o3", "2026-07-21 08:00:00", "open", 0, "cycle-b"),
        ]
        for order_id, created_at, action, close_only, cycle_id in orders:
            connection.execute(
                """
                INSERT INTO order_executions VALUES (
                    ?, 'strategy-1', 'BTC/USDT', 'long', 'submitted', ?, ?, ?, ?,
                    NULL, '[]', ?, 'binance_usdm', ?, 'filled', '[]', ?
                )
                """,
                (
                    order_id,
                    cycle_id,
                    cycle_id,
                    json.dumps({"quantity": 1.0}),
                    close_only,
                    json.dumps(
                        {
                            "paper_runtime_action": action,
                            "signal_candle_close_time": "2026-07-21T03:00:00+00:00",
                        }
                    ),
                    f"exchange-{order_id}",
                    created_at,
                ),
            )
        decisions = (
            ("2026-07-21 03:00:00", "technical_signals", "insufficient_signal"),
            ("2026-07-21 03:05:00", "technical_signals", "insufficient_signal"),
            ("2026-07-21 03:06:00", "gatekeeper", "COOLDOWN_ACTIVE"),
            ("2026-07-21 08:00:00", "technical_signals", "insufficient_signal"),
        )
        for index, (cycle_time, pipeline_status, reason) in enumerate(decisions):
            connection.execute(
                "INSERT INTO decision_snapshots VALUES (?, 'run-1', 'BTC/USDT', 'hold', ?, ?, '{}', ?, ?)",
                (f"d{index}", pipeline_status, reason, cycle_time, cycle_time),
            )
        connection.commit()
    finally:
        connection.close()


def test_analyze_runtime_ledger_groups_local_trade_bursts_and_flags_duplicate_cycle(tmp_path: Path) -> None:
    database = tmp_path / "ledger.db"
    _seed_incident_ledger(database)

    audit = analyze_runtime_ledger(database, timezone_name="Asia/Shanghai", cluster_gap_minutes=30)

    assert len(audit.trade_clusters) == 2
    assert audit.trade_clusters[0].started_at_local.isoformat() == "2026-07-21T11:00:00+08:00"
    assert audit.trade_clusters[1].started_at_local.isoformat() == "2026-07-21T16:00:00+08:00"
    assert audit.trade_clusters[0].order_count == 2
    assert audit.trade_clusters[0].duplicate_cycle_ids == ("cycle-a",)
    assert audit.trade_timeline[1].close_reason == "opposite_signal"
    assert audit.trade_timeline[0].strategy_key == "auto-paper-v1"
    assert audit.trade_timeline[0].normalized_order == {"quantity": 1.0}
    assert audit.trade_timeline[0].entry_context["paper_runtime_action"] == "open"
    assert audit.cycle_times_utc == (
        "2026-07-21T03:00:00+00:00",
        "2026-07-21T03:05:00+00:00",
        "2026-07-21T03:06:00+00:00",
        "2026-07-21T08:00:00+00:00",
    )
    assert audit.blocker_windows[0].first_pipeline_status == "gatekeeper"
    assert audit.blocker_windows[0].first_reason == "COOLDOWN_ACTIVE"
    assert audit.blocker_windows[0].reason_counts == (("gatekeeper:COOLDOWN_ACTIVE", 1),)

    artifacts = write_audit_artifacts(audit, tmp_path / "artifacts")
    assert [path.name for path in artifacts] == [
        "trade-burst-timeline.csv",
        "cycle-liveness.csv",
        "blocker-after-burst.csv",
    ]
    assert all(path.is_file() for path in artifacts)
