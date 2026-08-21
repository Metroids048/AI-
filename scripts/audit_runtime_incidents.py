"""Read-only classification of persisted V2 incidents.

This command never updates SQLite.  It separates incidents that are linked to
current V2 exposure from historical/unlinked audit rows so a healthy current
reconciliation is not obscured by an old recovery burst.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

HISTORICAL_INCIDENT_TYPES = frozenset(
    {
        "MANUAL_INTERVENTION_REQUIRED",
        "PROTECTION_RECOVERY_FAILED",
        "HISTORICAL_LEDGER_GAP",
    }
)


def classify_incident(
    incident: dict[str, Any],
    *,
    active_position_ids: set[str],
    active_intent_ids: set[str],
) -> str:
    """Classify without changing the immutable incident status column."""

    raw_context = incident.get("context")
    context: dict[str, Any] = raw_context if isinstance(raw_context, dict) else {}
    if context.get("entry_block_all") is True:
        return "CURRENT_EXPOSURE_BLOCKER"
    if str(incident.get("position_id") or "") in active_position_ids:
        return "CURRENT_EXPOSURE_LINKED"
    if str(incident.get("intent_id") or "") in active_intent_ids:
        return "CURRENT_INTENT_LINKED"
    if str(incident.get("incident_type") or "") in HISTORICAL_INCIDENT_TYPES:
        return "HISTORICAL_EVIDENCE_ONLY"
    return "REVIEW_REQUIRED"


def build_report(connection: sqlite3.Connection) -> dict[str, Any]:
    connection.row_factory = sqlite3.Row
    active_positions = {
        str(row[0])
        for row in connection.execute(
            "SELECT position_id FROM v2_managed_positions WHERE state NOT IN ('CLOSED', 'QUARANTINED')"
        )
    }
    active_intents = {
        str(row[0])
        for row in connection.execute(
            "SELECT intent_id FROM v2_execution_intents "
            "WHERE state IN ('INTENT_CREATED', 'EXCHANGE_SUBMITTING', "
            "'EXCHANGE_UNKNOWN', 'EXCHANGE_ACKNOWLEDGED')"
        )
    }
    rows = connection.execute(
        "SELECT incident_id, incident_type, severity, resolved, created_at, "
        "intent_id, position_id, context FROM v2_execution_incidents "
        "ORDER BY created_at DESC"
    ).fetchall()
    classifications: Counter[str] = Counter()
    unresolved_classifications: Counter[str] = Counter()
    unresolved_by_type: Counter[str] = Counter()
    samples: list[dict[str, Any]] = []
    for row in rows:
        raw_context = row[7]
        try:
            context = json.loads(raw_context) if raw_context else {}
        except (TypeError, json.JSONDecodeError):
            context = {}
        incident = {
            "incident_id": str(row[0]),
            "incident_type": str(row[1]),
            "severity": str(row[2]),
            "resolved": bool(row[3]),
            "created_at": str(row[4]),
            "intent_id": row[5],
            "position_id": row[6],
            "context": context,
        }
        classification = classify_incident(
            incident,
            active_position_ids=active_positions,
            active_intent_ids=active_intents,
        )
        classifications[classification] += 1
        if not incident["resolved"]:
            unresolved_classifications[classification] += 1
            unresolved_by_type[incident["incident_type"]] += 1
        if len(samples) < 20 and not incident["resolved"]:
            samples.append(
                {
                    "incident_id": incident["incident_id"],
                    "incident_type": incident["incident_type"],
                    "classification": classification,
                    "created_at": incident["created_at"],
                }
            )
    return {
        "read_only": True,
        "active_position_count": len(active_positions),
        "active_intent_count": len(active_intents),
        "incident_count": len(rows),
        "classification_counts": dict(classifications),
        "unresolved_classification_counts": dict(unresolved_classifications),
        "unresolved_by_type": dict(unresolved_by_type),
        "unresolved_samples": samples,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-path",
        type=Path,
        default=Path(".local_paper_console.db"),
        help="SQLite database path; the command is read-only",
    )
    args = parser.parse_args()
    if not args.database_path.exists():
        parser.error(f"database not found: {args.database_path}")
    connection = sqlite3.connect(f"file:{args.database_path.resolve()}?mode=ro", uri=True)
    try:
        print(json.dumps(build_report(connection), ensure_ascii=True, indent=2))
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
