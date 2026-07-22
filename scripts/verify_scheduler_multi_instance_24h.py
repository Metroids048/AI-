"""Accelerated 24-hour scheduler safety verification over 96 fifteen-minute slots."""

from __future__ import annotations

import argparse
import csv
import json
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from services.execution.scheduler_coordination import SchedulerCoordinator
from services.strategy_library.models import Base


def verify(*, started_at: datetime) -> dict:
    with tempfile.TemporaryDirectory(prefix="ai-quant-scheduler-24h-") as temporary_directory:
        database = Path(temporary_directory) / "coordination.db"
        engine = create_engine(f"sqlite:///{database.as_posix()}")
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        schedulers = (
            SchedulerCoordinator(session_factory=factory, instance_id="scheduler-a"),
            SchedulerCoordinator(session_factory=factory, instance_id="scheduler-b"),
        )
        rows = []
        for slot_index in range(96):
            scheduled_for = started_at + timedelta(minutes=15 * slot_index)
            claims = [
                scheduler.claim_cycle(job_name="paper_runtime_cycle", scheduled_for=scheduled_for)
                for scheduler in schedulers
            ]
            winner_count = sum(claim.claimed for claim in claims)
            rows.append(
                {
                    "slot_index": slot_index,
                    "scheduled_for": scheduled_for.isoformat(),
                    "winner_count": winner_count,
                    "winner": next(
                        (
                            scheduler.instance_id
                            for scheduler, claim in zip(schedulers, claims, strict=True)
                            if claim.claimed
                        ),
                        None,
                    ),
                }
            )
        stored_cycles = schedulers[0].list_cycles(job_name="paper_runtime_cycle")
        result = {
            "verification_kind": "accelerated_24h_multi_instance",
            "window_started_at": started_at.isoformat(),
            "window_ended_at": (started_at + timedelta(hours=24)).isoformat(),
            "slot_interval_minutes": 15,
            "expected_slots": 96,
            "claimed_slots": len(stored_cycles),
            "duplicate_winner_slots": sum(row["winner_count"] != 1 for row in rows),
            "passed": len(stored_cycles) == 96 and all(row["winner_count"] == 1 for row in rows),
            "rows": rows,
        }
        engine.dispose()
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("artifacts/scheduler-24h-verification.json"))
    parser.add_argument("--csv-output", type=Path, default=Path("artifacts/scheduler-24h-slots.csv"))
    args = parser.parse_args()
    result = verify(started_at=datetime(2026, 7, 22, tzinfo=UTC))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    args.csv_output.parent.mkdir(parents=True, exist_ok=True)
    with args.csv_output.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=("slot_index", "scheduled_for", "winner_count", "winner"))
        writer.writeheader()
        writer.writerows(result["rows"])
    print(json.dumps({key: value for key, value in result.items() if key != "rows"}, ensure_ascii=False))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
