from __future__ import annotations

import sqlite3

from scripts.audit_runtime_incidents import build_report, classify_incident


def test_classifies_linked_incident_as_current_exposure() -> None:
    incident = {
        "incident_type": "MANUAL_INTERVENTION_REQUIRED",
        "position_id": "position-1",
        "intent_id": None,
        "context": {},
    }

    assert (
        classify_incident(
            incident,
            active_position_ids={"position-1"},
            active_intent_ids=set(),
        )
        == "CURRENT_EXPOSURE_LINKED"
    )


def test_keeps_unlinked_recovery_incident_as_historical_evidence() -> None:
    incident = {
        "incident_type": "PROTECTION_RECOVERY_FAILED",
        "position_id": None,
        "intent_id": None,
        "context": {},
    }

    assert (
        classify_incident(
            incident,
            active_position_ids=set(),
            active_intent_ids=set(),
        )
        == "HISTORICAL_EVIDENCE_ONLY"
    )


def test_entry_block_context_remains_a_current_blocker() -> None:
    incident = {
        "incident_type": "MANUAL_INTERVENTION_REQUIRED",
        "position_id": None,
        "intent_id": None,
        "context": {"entry_block_all": True},
    }

    assert (
        classify_incident(
            incident,
            active_position_ids=set(),
            active_intent_ids=set(),
        )
        == "CURRENT_EXPOSURE_BLOCKER"
    )


def test_build_report_reads_active_intents_from_intent_table_without_writes() -> None:
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE v2_managed_positions (
            position_id TEXT PRIMARY KEY,
            state TEXT NOT NULL
        );
        CREATE TABLE v2_execution_intents (
            intent_id TEXT PRIMARY KEY,
            state TEXT NOT NULL
        );
        CREATE TABLE v2_execution_incidents (
            incident_id TEXT PRIMARY KEY,
            incident_type TEXT NOT NULL,
            severity TEXT NOT NULL,
            resolved INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            intent_id TEXT,
            position_id TEXT,
            context TEXT
        );
        INSERT INTO v2_execution_intents VALUES ('intent-1', 'EXCHANGE_UNKNOWN');
        INSERT INTO v2_execution_incidents VALUES
            ('incident-1', 'PROTECTION_RECOVERY_FAILED', 'HIGH', 0,
             '2026-08-21T00:00:00Z', 'intent-1', NULL, '{}');
        """
    )

    report = build_report(connection)

    assert report["read_only"] is True
    assert report["active_intent_count"] == 1
    assert report["unresolved_classification_counts"] == {"CURRENT_INTENT_LINKED": 1}
    assert connection.execute("SELECT COUNT(*) FROM v2_execution_incidents").fetchone()[0] == 1
