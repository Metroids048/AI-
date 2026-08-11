from __future__ import annotations

import importlib.util
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path


def _load_verifier():  # noqa: ANN202
    path = Path(__file__).resolve().parents[2] / "scripts" / "verify_p1_same_cycle_research_shadow.py"
    assert path.exists(), "P1 read-only runtime evidence verifier is missing"
    spec = importlib.util.spec_from_file_location("verify_p1_same_cycle_research_shadow", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_runtime_fixture(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE v2_execution_cycles (
            cycle_id TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            bar_timestamp TEXT NOT NULL,
            decision_terminal TEXT,
            execution_mode TEXT NOT NULL
        );
        CREATE TABLE v2_execution_decisions (
            decision_id TEXT PRIMARY KEY,
            cycle_id TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE v2_execution_intents (
            intent_id TEXT PRIMARY KEY,
            candidate_key TEXT NOT NULL,
            candidate_type TEXT NOT NULL DEFAULT 'SAMPLING',
            created_at TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE v2_exchange_orders (
            order_record_id TEXT PRIMARY KEY,
            intent_id TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE v2_managed_positions (
            position_id TEXT PRIMARY KEY,
            intent_id TEXT NOT NULL,
            projected_at TEXT NOT NULL,
            protected_at TEXT,
            closed_at TEXT,
            version INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE v2_protection_records (
            protection_id TEXT PRIMARY KEY,
            position_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            activated_at TEXT,
            version INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    connection.execute(
        "INSERT INTO v2_execution_cycles VALUES (?, ?, ?, ?, ?)",
        (
            "cycle-before",
            "BTC/USDT",
            "2026-08-10 11:45:00",
            "RISK_APPROVED",
            "BINANCE_TESTNET",
        ),
    )
    connection.execute(
        "INSERT INTO v2_execution_decisions VALUES (?, ?, ?, ?)",
        (
            "decision-before",
            "cycle-before",
            json.dumps({"terminal_stage": "RISK_APPROVED", "reason_code": "POSITION_ALREADY_OPEN"}),
            "2026-08-10 11:50:00",
        ),
    )
    market_reference = "market-ref-1"
    observations = [
        {
            "scheduler_session_id": "runtime-session",
            "scheduler_cycle_id": "scheduler-cycle",
            "cycle_id": "cycle-after",
            "symbol": "BTC/USDT",
            "bar_close_time": "2026-08-10T12:00:00+00:00",
            "market_snapshot_reference": market_reference,
            "strategy_id": strategy_id,
            "lane": "RESEARCH_SHADOW",
            "decision_status": "SHADOW_NO_SIGNAL",
        }
        for strategy_id in (
            "trend_pullback_v2",
            "range_sweep_reversion_v1",
            "failed_breakout_reversal_v1",
        )
    ]
    payload = {
        "active_strategy_id": "testnet_sampling_v2",
        "active_decision": "RISK_APPROVED",
        "active_terminal_reason": "POSITION_ALREADY_OPEN",
        "market_snapshot_reference": market_reference,
        "research_shadow": {
            "schema_version": "p1-same-cycle-research-shadow-v1",
            "lane": "RESEARCH_SHADOW",
            "scheduler_session_id": "runtime-session",
            "scheduler_cycle_id": "scheduler-cycle",
            "cycle_id": "cycle-after",
            "symbol": "BTC/USDT",
            "bar_close_time": "2026-08-10T12:00:00+00:00",
            "market_snapshot_reference": market_reference,
            "active_strategy_id": "testnet_sampling_v2",
            "observations": observations,
        },
    }
    connection.execute(
        "INSERT INTO v2_execution_cycles VALUES (?, ?, ?, ?, ?)",
        (
            "cycle-after",
            "BTC/USDT",
            "2026-08-10 12:00:00",
            "RISK_APPROVED",
            "BINANCE_TESTNET",
        ),
    )
    connection.execute(
        "INSERT INTO v2_execution_decisions VALUES (?, ?, ?, ?)",
        ("decision-after", "cycle-after", json.dumps(payload), "2026-08-10 12:00:05"),
    )
    connection.commit()
    connection.close()


def test_collect_evidence_matches_same_cycle_and_reports_zero_shadow_mutations(tmp_path) -> None:
    verifier = _load_verifier()
    database = tmp_path / "runtime.db"
    _create_runtime_fixture(database)
    database_before = database.read_bytes()

    evidence = verifier.collect_evidence(
        database,
        cutover=datetime(2026, 8, 10, 12, 0, tzinfo=UTC),
    )

    assert evidence["change_effect"] == {
        "before_shadow_observations": 0,
        "after_shadow_observations": 3,
        "same_cycle_matches": 3,
        "unmatched": 0,
    }
    assert evidence["mutation_ledger"] == {
        "intent_created": 0,
        "exchange_orders_created": 0,
        "positions_created": 0,
        "positions_modified": 0,
        "protection_orders_created": 0,
        "protection_orders_modified": 0,
    }
    assert evidence["examples"][0]["cycle_id"] == "cycle-after"
    assert evidence["examples"][0]["active_terminal_reason"] == "POSITION_ALREADY_OPEN"
    assert set(evidence["examples"][0]["shadow_decisions"]) == {
        "trend_pullback_v2",
        "range_sweep_reversion_v1",
        "failed_breakout_reversal_v1",
    }
    assert database.read_bytes() == database_before


def test_collect_evidence_detects_research_lineage_mutations(tmp_path) -> None:
    verifier = _load_verifier()
    database = tmp_path / "runtime.db"
    _create_runtime_fixture(database)
    connection = sqlite3.connect(database)
    connection.execute(
        "INSERT INTO v2_execution_intents VALUES (?, ?, ?, ?, ?)",
        ("research-intent", "trend_pullback_v2:BTC/USDT", "RESEARCH", "2026-08-10 12:01:00", 0),
    )
    connection.execute(
        "INSERT INTO v2_exchange_orders VALUES (?, ?, ?)",
        ("research-order", "research-intent", "2026-08-10 12:02:00"),
    )
    connection.execute(
        "INSERT INTO v2_managed_positions VALUES (?, ?, ?, ?, ?, ?)",
        (
            "research-position",
            "research-intent",
            "2026-08-10 12:03:00",
            "2026-08-10 12:04:00",
            None,
            1,
        ),
    )
    connection.execute(
        "INSERT INTO v2_protection_records VALUES (?, ?, ?, ?, ?)",
        (
            "research-protection",
            "research-position",
            "2026-08-10 12:04:00",
            "2026-08-10 12:05:00",
            1,
        ),
    )
    connection.commit()
    connection.close()

    evidence = verifier.collect_evidence(
        database,
        cutover=datetime(2026, 8, 10, 12, 0, tzinfo=UTC),
    )

    assert evidence["mutation_ledger"] == {
        "intent_created": 1,
        "exchange_orders_created": 1,
        "positions_created": 1,
        "positions_modified": 1,
        "protection_orders_created": 1,
        "protection_orders_modified": 1,
    }


def test_collect_evidence_rejects_tampered_observation_envelope(tmp_path) -> None:
    verifier = _load_verifier()
    database = tmp_path / "runtime.db"
    _create_runtime_fixture(database)
    connection = sqlite3.connect(database)
    row = connection.execute(
        "SELECT decision_id, payload FROM v2_execution_decisions WHERE decision_id = ?",
        ("decision-after",),
    ).fetchone()
    assert row is not None
    payload = json.loads(row[1])
    payload["market_snapshot_reference"] = "top-level-reference-does-not-match"
    payload["research_shadow"]["observations"][0]["cycle_id"] = "wrong-cycle"
    payload["research_shadow"]["observations"][1]["lane"] = "ACTIVE_CONTROL"
    payload["research_shadow"]["observations"][2]["strategy_id"] = "unknown-research"
    connection.execute(
        "UPDATE v2_execution_decisions SET payload = ? WHERE decision_id = ?",
        (json.dumps(payload), row[0]),
    )
    connection.commit()
    connection.close()

    evidence = verifier.collect_evidence(
        database,
        cutover=datetime(2026, 8, 10, 12, 0, tzinfo=UTC),
    )

    assert evidence["change_effect"]["after_shadow_observations"] == 3
    assert evidence["change_effect"]["same_cycle_matches"] == 0
    assert evidence["change_effect"]["unmatched"] == 3
    assert evidence["examples"] == []


def test_collect_evidence_rejects_missing_runtime_identity(tmp_path) -> None:
    verifier = _load_verifier()
    database = tmp_path / "runtime.db"
    _create_runtime_fixture(database)
    connection = sqlite3.connect(database)
    row = connection.execute(
        "SELECT decision_id, payload FROM v2_execution_decisions WHERE decision_id = ?",
        ("decision-after",),
    ).fetchone()
    assert row is not None
    payload = json.loads(row[1])
    payload["research_shadow"].pop("scheduler_session_id")
    payload["research_shadow"].pop("scheduler_cycle_id")
    payload["active_strategy_id"] = "wrong-active-strategy"
    connection.execute(
        "UPDATE v2_execution_decisions SET payload = ? WHERE decision_id = ?",
        (json.dumps(payload), row[0]),
    )
    connection.commit()
    connection.close()

    evidence = verifier.collect_evidence(
        database,
        cutover=datetime(2026, 8, 10, 12, 0, tzinfo=UTC),
    )

    assert evidence["change_effect"]["same_cycle_matches"] == 0
    assert evidence["change_effect"]["unmatched"] == 3


def test_collect_evidence_rejects_tampered_top_level_active_strategy(tmp_path) -> None:
    verifier = _load_verifier()
    database = tmp_path / "runtime.db"
    _create_runtime_fixture(database)
    connection = sqlite3.connect(database)
    row = connection.execute(
        "SELECT decision_id, payload FROM v2_execution_decisions WHERE decision_id = ?",
        ("decision-after",),
    ).fetchone()
    assert row is not None
    payload = json.loads(row[1])
    payload["active_strategy_id"] = "tampered-active-strategy"
    connection.execute(
        "UPDATE v2_execution_decisions SET payload = ? WHERE decision_id = ?",
        (json.dumps(payload), row[0]),
    )
    connection.commit()
    connection.close()

    evidence = verifier.collect_evidence(
        database,
        cutover=datetime(2026, 8, 10, 12, 0, tzinfo=UTC),
    )

    assert evidence["change_effect"]["same_cycle_matches"] == 0
    assert evidence["change_effect"]["unmatched"] == 3
    assert evidence["examples"] == []


def test_collect_evidence_rejects_non_testnet_cycle(tmp_path) -> None:
    verifier = _load_verifier()
    database = tmp_path / "runtime.db"
    _create_runtime_fixture(database)
    connection = sqlite3.connect(database)
    connection.execute(
        "UPDATE v2_execution_cycles SET execution_mode = ? WHERE cycle_id = ?",
        ("LOCAL_PAPER", "cycle-after"),
    )
    connection.commit()
    connection.close()

    evidence = verifier.collect_evidence(
        database,
        cutover=datetime(2026, 8, 10, 12, 0, tzinfo=UTC),
    )

    assert evidence["change_effect"]["same_cycle_matches"] == 0
    assert evidence["change_effect"]["unmatched"] == 3
    assert evidence["examples"] == []
