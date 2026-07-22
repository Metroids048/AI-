"""Capture one read-only wall-clock liveness observation for the 24h audit."""

from __future__ import annotations

import argparse
import json
import sqlite3
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.report_scheduler_coordination import report as coordination_report


def _api_health(url: str) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310 - fixed local operator URL
            return {"reachable": True, "status_code": response.status, "body": json.load(response)}
    except Exception as exc:  # noqa: BLE001 - evidence capture must serialize local endpoint failures
        return {"reachable": False, "error": str(exc)}


def capture(*, database: Path, scheduler_state: Path, health_url: str, observed_at: datetime) -> dict[str, Any]:
    state = json.loads(scheduler_state.read_text(encoding="utf-8")) if scheduler_state.is_file() else {}
    resolved = database.resolve()
    connection = sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro", uri=True)
    try:
        order_count = connection.execute("SELECT COUNT(*) FROM order_executions").fetchone()[0]
        decision_count = connection.execute("SELECT COUNT(*) FROM decision_snapshots").fetchone()[0]
    finally:
        connection.close()
    coordination = coordination_report(database)
    health = _api_health(health_url)
    healthy = bool(
        health.get("reachable")
        and state.get("running")
        and state.get("heartbeat_at")
        and coordination["duplicate_slot_count"] == 0
    )
    return {
        "observed_at": observed_at.astimezone(UTC).isoformat(),
        "healthy": healthy,
        "api_health": health,
        "scheduler": state,
        "coordination": coordination,
        "order_count": order_count,
        "decision_count": decision_count,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path(".local_paper_console.db"))
    parser.add_argument("--scheduler-state", type=Path, default=Path("logs/scheduler-state.json"))
    parser.add_argument("--health-url", default="http://127.0.0.1:8000/health")
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/live-24h"))
    args = parser.parse_args()
    observed_at = datetime.now(UTC)
    result = capture(
        database=args.database,
        scheduler_state=args.scheduler_state,
        health_url=args.health_url,
        observed_at=observed_at,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir / f"observation-{observed_at.strftime('%Y%m%dT%H%M%S%fZ')}.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": output.as_posix(), "healthy": result["healthy"]}, ensure_ascii=False))
    return 0 if result["healthy"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
