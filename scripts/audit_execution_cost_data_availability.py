"""Check whether historical order-book evidence exists for a maker/limit replay."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any


def inspect_database(database: Path) -> dict[str, Any]:
    connection = sqlite3.connect(f"file:{database.resolve().as_posix()}?mode=ro", uri=True)
    try:
        tables = [
            row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        ]
        relevant = [
            name
            for name in tables
            if any(token in name.lower() for token in ("book", "depth", "quote", "spread", "ticker"))
        ]
        return {
            "database": str(database),
            "read_only": True,
            "tables": tables,
            "order_book_like_tables": relevant,
            "maker_limit_model_status": "BLOCKED_NO_HISTORICAL_ORDER_BOOK"
            if not relevant
            else "DATA_AVAILABLE_FOR_REVIEW",
            "required_unobserved_fields": [
                "bid_ask_at_signal",
                "queue_or_fill_probability",
                "timeout",
                "adverse_selection",
                "drift",
            ],
        }
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path(".strategy_refactor_history.db"))
    parser.add_argument(
        "--output", type=Path, default=Path("docs/audits/2026-08-16-maker-limit-data-availability.json")
    )
    args = parser.parse_args()
    report = inspect_database(args.database)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(report["maker_limit_model_status"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
