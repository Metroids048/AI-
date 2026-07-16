from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

from scripts.audit_decision_funnel import run_audit


def test_funnel_audit_filters_strategy_and_counts_pipeline_and_gatekeeper_stages(tmp_path) -> None:
    path = tmp_path / "funnel.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE strategies (id TEXT PRIMARY KEY, strategy_key TEXT NOT NULL);
        CREATE TABLE paper_runs (paper_run_id TEXT PRIMARY KEY, strategy_id TEXT NOT NULL);
        CREATE TABLE decision_snapshots (
            paper_run_id TEXT, action TEXT, pipeline_status TEXT, decision_trace TEXT,
            cycle_time DATETIME
        );
        CREATE TABLE order_executions (
            paper_run_id TEXT, execution_status TEXT, rejection_codes TEXT, created_at DATETIME
        );
        """
    )
    now = datetime.now(UTC).replace(tzinfo=None)
    old = now - timedelta(days=10)
    connection.executemany("INSERT INTO strategies VALUES (?, ?)", [("s1", "main"), ("s2", "other")])
    connection.executemany("INSERT INTO paper_runs VALUES (?, ?)", [("p1", "s1"), ("p2", "s2")])
    connection.executemany(
        "INSERT INTO decision_snapshots VALUES (?, ?, ?, ?, ?)",
        [
            ("p1", "skip_no_trade_decision", "technical_signals_insufficient", "{}", now.isoformat(sep=" ")),
            ("p1", "skip_no_trade_decision", "multi_timeframe_disagreement", "{}", now.isoformat(sep=" ")),
            (
                "p1",
                "rejected",
                "bet_taken",
                json.dumps({"edge_stats_source": "validated_edge_stats_missing_or_stale"}),
                now.isoformat(sep=" "),
            ),
            ("p1", "open_long", "bet_taken", "{}", now.isoformat(sep=" ")),
            ("p1", "skip_no_trade_decision", "ensemble_discarded", "{}", old.isoformat(sep=" ")),
            ("p2", "open_long", "bet_taken", "{}", now.isoformat(sep=" ")),
        ],
    )
    connection.execute(
        "INSERT INTO order_executions VALUES (?, ?, ?, ?)",
        (
            "p1",
            "rejected",
            json.dumps(["validated_edge_stats_missing_or_stale", "correlated_exposure_limit_exceeded"]),
            now.isoformat(sep=" "),
        ),
    )
    connection.commit()
    connection.close()

    report = run_audit(
        database_url=f"sqlite:///{path.as_posix()}",
        since=datetime.now(UTC) - timedelta(days=1),
        strategy_key="main",
    )

    assert report.total_decisions == 4
    assert report.stage_counts == {
        "technical_signals": 1,
        "multi_timeframe": 1,
        "validated_edge": 1,
        "opened": 1,
    }
    assert report.rejection_code_counts == {
        "correlated_exposure_limit_exceeded": 1,
        "validated_edge_stats_missing_or_stale": 1,
    }
