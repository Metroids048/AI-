"""Read-only report of scheduler leases and claimed execution slots."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def report(database: Path) -> dict:
    resolved = database.expanduser().resolve()
    connection = sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        leases = [dict(row) for row in connection.execute("SELECT * FROM scheduler_leases ORDER BY lease_name")]
        cycles = [
            dict(row)
            for row in connection.execute("SELECT * FROM scheduler_cycles ORDER BY scheduled_for, scheduler_cycle_id")
        ]
        return {
            "database": resolved.as_posix(),
            "lease_count": len(leases),
            "cycle_count": len(cycles),
            "duplicate_slot_count": len(cycles) - len({(row["job_name"], row["scheduled_for"]) for row in cycles}),
            "leases": leases,
            "cycles": cycles,
        }
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path(".local_paper_console.db"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = report(args.database)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
