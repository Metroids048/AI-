"""Tests for portable runtime-ledger export/import (ADR-073)."""

from __future__ import annotations

import gzip
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from scripts.export_runtime_ledger import export_runtime_ledger
from scripts.import_runtime_ledger import import_runtime_ledger


def _seed_source(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE strategies (
          id TEXT PRIMARY KEY,
          strategy_key TEXT,
          entry_rules TEXT
        );
        CREATE TABLE paper_runs (
          paper_run_id TEXT PRIMARY KEY,
          strategy_id TEXT,
          created_at TEXT,
          execution_profile TEXT
        );
        CREATE TABLE order_executions (
          order_execution_id TEXT PRIMARY KEY,
          strategy_id TEXT,
          paper_run_id TEXT,
          symbol TEXT,
          created_at TEXT,
          entry_context TEXT,
          rejection_codes TEXT
        );
        CREATE TABLE decision_snapshots (
          decision_snapshot_id TEXT PRIMARY KEY,
          paper_run_id TEXT,
          symbol TEXT,
          action TEXT,
          pipeline_status TEXT,
          decision_trace TEXT,
          cycle_time TEXT,
          created_at TEXT
        );
        CREATE TABLE position_snapshots (
          position_snapshot_id TEXT PRIMARY KEY,
          run_id TEXT,
          symbol TEXT,
          snapshot_time TEXT,
          quantity REAL
        );
        """
    )
    now = datetime.now(UTC).replace(tzinfo=None)
    recent = (now - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    old = (now - timedelta(days=60)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "INSERT INTO strategies VALUES (?, ?, ?)",
        ("s1", "signal_observation_technical", json.dumps({"api_key": "should-strip"})),
    )
    conn.execute(
        "INSERT INTO paper_runs VALUES (?, ?, ?, ?)",
        ("pr1", "s1", recent, json.dumps({"secret": "nope"})),
    )
    conn.execute(
        "INSERT INTO order_executions VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "oe1",
            "s1",
            "pr1",
            "BTC/USDT",
            recent,
            json.dumps({"api_key": "abc", "note": "ok"}),
            json.dumps(["x"]),
        ),
    )
    conn.execute(
        "INSERT INTO order_executions VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("oe_old", "s1", "pr1", "ETH/USDT", old, "{}", "[]"),
    )
    conn.execute(
        "INSERT INTO decision_snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("ds1", "pr1", "BTC/USDT", "open_long", "accepted", "{}", recent, recent),
    )
    conn.execute(
        "INSERT INTO position_snapshots VALUES (?, ?, ?, ?, ?)",
        ("ps1", "pr1", "BTC/USDT", recent, 0.01),
    )
    conn.commit()
    conn.close()


def test_export_import_redacts_and_filters_window(tmp_path: Path) -> None:
    source = tmp_path / "hot.db"
    out_dir = tmp_path / "ledger"
    target = tmp_path / "local_runtime_ledger.db"
    _seed_source(source)

    manifest = export_runtime_ledger(source_db=source, out_dir=out_dir, lookback_days=30)
    assert manifest["table_counts"]["order_executions"] == 1
    assert manifest["table_counts"]["strategies"] == 1
    assert (out_dir / "ledger.sqlite.gz").is_file()
    assert (out_dir / "manifest.json").is_file()

    result = import_runtime_ledger(
        ledger_gz=out_dir / "ledger.sqlite.gz",
        target_db=target,
        manifest_path=out_dir / "manifest.json",
    )
    assert Path(str(result["target_db"])).is_file()

    conn = sqlite3.connect(target)
    try:
        n_orders = conn.execute("SELECT COUNT(*) FROM order_executions").fetchone()[0]
        assert n_orders == 1
        ctx = conn.execute("SELECT entry_context FROM order_executions").fetchone()[0]
        assert "***REDACTED***" in str(ctx)
        assert "abc" not in str(ctx)
        strategy_rules = conn.execute("SELECT entry_rules FROM strategies").fetchone()[0]
        assert "***REDACTED***" in str(strategy_rules)
    finally:
        conn.close()

    # Corrupt hash must fail closed.
    with gzip.open(out_dir / "ledger.sqlite.gz", "ab") as handle:
        handle.write(b"x")
    try:
        import_runtime_ledger(
            ledger_gz=out_dir / "ledger.sqlite.gz",
            target_db=tmp_path / "bad.db",
            manifest_path=out_dir / "manifest.json",
        )
        raised = False
    except SystemExit:
        raised = True
    assert raised
