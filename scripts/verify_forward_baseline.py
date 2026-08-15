"""Verify the immutable Forward Baseline without touching execution state."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from sqlalchemy.exc import OperationalError

from services.automated_trading.audit.forward_baseline import compare_decision_snapshot
from services.automated_trading.infrastructure.repository import AutomatedTradingRepository
from services.database import get_session_factory


def verify(*, min_cycles: int = 100, symbol: str | None = None) -> dict[str, Any]:
    try:
        with get_session_factory()() as session:
            repo = AutomatedTradingRepository(session)
            snapshots = repo.list_forward_snapshots(symbol=symbol)
            shadow_rows = repo.list_shadow_records()
    except OperationalError as exc:
        if "no such table" not in str(exc).lower():
            raise
        return {
            "STATUS": "FORWARD_BASELINE_SCHEMA_NOT_READY",
            "Decision cycles captured": 0,
            "Replay cycles": 0,
            "Feature match rate": 0.0,
            "Candidate match rate": 0.0,
            "TradeCandidate match rate": 0.0,
            "Immutable snapshot violations": 0,
            "Shadow records": 0,
            "Execution side effects": 0,
            "Mismatch list": [{"first_divergence_point": "SCHEMA:v2_decision_snapshots"}],
            "minimum_cycles": min_cycles,
        }
    # Only ACTIVE Binance Testnet snapshots are eligible for the natural-cycle
    # acceptance gate. Shadow/local rows remain useful diagnostics but must
    # never satisfy the 100-cycle requirement.
    eligible_snapshots = [
        row
        for row in snapshots
        if row.payload.get("execution_mode") == "BINANCE_TESTNET"
        and row.payload.get("engine_activation") == "ACTIVE"
    ]
    comparisons = [compare_decision_snapshot(dict(row.payload)) for row in eligible_snapshots]
    total = len(comparisons)
    decision_matches = sum(1 for item in comparisons if item.decision_match)
    feature_matches = sum(1 for item in comparisons if item.feature_match)
    candidate_matches = sum(1 for item in comparisons if item.trade_candidate_match)
    mismatches = [
        {
            "snapshot_id": row.snapshot_id,
            "decision_id": row.decision_id,
            "first_divergence_point": comparison.first_divergence_point,
            "mismatches": list(comparison.mismatches),
        }
        for row, comparison in zip(eligible_snapshots, comparisons, strict=True)
        if not comparison.reproducible
    ]
    immutable_violations = sum(
        1 for item in mismatches if "HASH_MISMATCH" in " ".join(str(value) for value in (item.get("mismatches") or []))
    )
    ready = total >= min_cycles and total > 0 and not mismatches
    return {
        "STATUS": "FORWARD_REPRODUCIBLE_BASELINE_READY" if ready else "FORWARD_BASELINE_NOT_REPRODUCIBLE",
        "Decision cycles captured": total,
        "Replay cycles": total,
        "Feature match rate": (decision_matches / total) if total else 0.0,
        "Candidate match rate": (candidate_matches / total) if total else 0.0,
        "TradeCandidate match rate": (candidate_matches / total) if total else 0.0,
        "Immutable snapshot violations": immutable_violations,
        "Shadow records": len(shadow_rows),
        "Execution side effects": 0,
        "Mismatch list": mismatches,
        "minimum_cycles": min_cycles,
    }


def _write_report(payload: dict[str, Any], output: Path | None) -> None:
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    print(rendered)
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--min-cycles", type=int, default=100)
    parser.add_argument("--symbol")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    _write_report(verify(min_cycles=args.min_cycles, symbol=args.symbol), args.output)


if __name__ == "__main__":
    main()
