"""Expire same-host scheduler leases/claims whose owner PID is dead."""

from __future__ import annotations

import os
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (OSError, SystemError):
        return False
    return True


def main(db_path: str) -> int:
    path = Path(db_path)
    if not path.exists():
        return 0
    conn = sqlite3.connect(path)
    now = datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" ")
    released = 0
    for lease_name, pid in list(conn.execute("SELECT lease_name, process_id FROM scheduler_leases")):
        try:
            owner_pid = int(pid)
        except (TypeError, ValueError, OverflowError, SystemError):
            owner_pid = -1
        if owner_pid > 0 and _alive(owner_pid):
            continue
        conn.execute(
            "DELETE FROM scheduler_leases WHERE lease_name=? AND process_id=?",
            (lease_name, pid),
        )
        released += 1
    for cycle_id, pid in list(
        conn.execute("SELECT scheduler_cycle_id, process_id FROM scheduler_cycles WHERE status='claimed'")
    ):
        try:
            owner_pid = int(pid)
        except (TypeError, ValueError, OverflowError, SystemError):
            owner_pid = -1
        if owner_pid > 0 and _alive(owner_pid):
            continue
        conn.execute(
            "UPDATE scheduler_cycles SET status=?, completed_at=?, failure_reason=? WHERE scheduler_cycle_id=?",
            ("failed", now, "owner_process_dead_reclaimed_offline", cycle_id),
        )
        released += 1
    conn.commit()
    print(f"reclaimed={released}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else ".local_paper_console.db"))
