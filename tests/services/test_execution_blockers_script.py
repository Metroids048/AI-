from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from scripts.check_execution_blockers import _check_db_blockers
from services.data.universe import AUTO_SIMULATION_EXECUTION_SYMBOLS, execution_scope_hash
from services.execution.bootstrap import AUTO_PAPER_TECHNICAL_KEY


def _create_runtime_db(
    path: Path,
    *,
    execution_profile: dict,
    paper_metrics_summary: dict | None = None,
) -> None:
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE agent_tasks (task_type TEXT, task_status TEXT, output_payload TEXT, created_at TEXT)"
    )
    connection.execute(
        "CREATE TABLE paper_runs ("
        "paper_status TEXT, execution_profile TEXT, paper_metrics_summary TEXT, created_at TEXT"
        ")"
    )
    connection.execute(
        "CREATE TABLE risk_events ("
        "id TEXT, level TEXT, title TEXT, resolution_status TEXT, expires_at TEXT, affected_scope TEXT"
        ")"
    )
    symbols = list(AUTO_SIMULATION_EXECUTION_SYMBOLS)
    acceptance = {
        "requested_symbols": symbols,
        "completed_symbols": symbols,
        "filled_order_count": 2 * len(symbols),
        "final_open_position_count": 0,
        "final_open_order_count": 0,
    }
    connection.execute(
        "INSERT INTO agent_tasks VALUES (?, ?, ?, ?)",
        ("testnet_acceptance", "completed", json.dumps(acceptance), "2026-07-24T00:00:00+00:00"),
    )
    connection.execute(
        "INSERT INTO paper_runs VALUES (?, ?, ?, ?)",
        (
            "running",
            json.dumps(execution_profile),
            json.dumps(paper_metrics_summary or {}),
            "2026-07-24T00:00:00+00:00",
        ),
    )
    connection.commit()
    connection.close()


def test_db_blockers_rejects_verified_acceptance_when_directional_run_is_paper_only(tmp_path: Path) -> None:
    database = tmp_path / "runtime.db"
    _create_runtime_db(
        database,
        execution_profile={
            "auto_paper_runtime_key": AUTO_PAPER_TECHNICAL_KEY,
            "execution_mode": "paper_only",
            "mirror_to_gateway": False,
            "cost_gate_verified": False,
        },
    )

    blockers = _check_db_blockers(database)

    assert "testnet_acceptance_not_verified" not in blockers
    assert "directional_run_not_armed" in blockers


def test_db_blockers_accepts_exact_scope_armed_directional_run(tmp_path: Path) -> None:
    database = tmp_path / "runtime.db"
    symbols = list(AUTO_SIMULATION_EXECUTION_SYMBOLS)
    _create_runtime_db(
        database,
        execution_profile={
            "auto_paper_runtime_key": AUTO_PAPER_TECHNICAL_KEY,
            "execution_mode": "binance_simulation_first",
            "mirror_to_gateway": True,
            "cost_gate_verified": True,
            "acceptance_symbols": symbols,
            "acceptance_scope_hash": execution_scope_hash(symbols),
        },
    )

    blockers = _check_db_blockers(database)

    assert blockers == []


def test_db_blockers_reports_unmanaged_external_position_from_latest_directional_cycle(tmp_path: Path) -> None:
    database = tmp_path / "runtime.db"
    symbols = list(AUTO_SIMULATION_EXECUTION_SYMBOLS)
    _create_runtime_db(
        database,
        execution_profile={
            "auto_paper_runtime_key": AUTO_PAPER_TECHNICAL_KEY,
            "execution_mode": "binance_simulation_first",
            "mirror_to_gateway": True,
            "cost_gate_verified": True,
            "acceptance_symbols": symbols,
            "acceptance_scope_hash": execution_scope_hash(symbols),
        },
        paper_metrics_summary={"unmanaged_external_symbols": ["BTC/USDT"]},
    )

    blockers = _check_db_blockers(database)

    assert "unmanaged_external_position" in blockers
