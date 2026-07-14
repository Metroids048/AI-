"""Compare fixed 2R exits vs ExitLadder on the same layered entry policy.

Evidence-only: writes an audit report and never mutates auto-trading config.

Backward-compatible wrapper. The reusable comparison logic now lives in
``services.validation.technical_replay.compare_exit_policies`` and the generic
``scripts/compare_exit_policies_cli.py``; this script keeps its original CLI and
defaults to the live entry baseline (as it always did) by delegating to them.
"""

from __future__ import annotations

import argparse
import os
from datetime import UTC, datetime
from pathlib import Path

from scripts.compare_exit_policies_cli import ENTRY_BASELINE_LIVE, run_comparison
from scripts.run_top20_technical_validation import (
    _closed_four_hour_boundary,
    _load_or_backfill,
    _load_stored,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare fixed 2R vs ExitLadder exits.")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--reuse-stored-data", action="store_true")
    args = parser.parse_args()
    if args.days < 60:
        raise SystemExit("--days must be at least 60")

    root = Path(__file__).resolve().parents[1]
    default_database_url = f"sqlite:///{(root / '.local' / 'technical-validation.db').as_posix()}"
    database_url = args.database_url or default_database_url
    os.environ["POSTGRES_URL"] = database_url
    from scripts.prepare_database import prepare_database

    prepare_database(database_url)
    end_at = _closed_four_hour_boundary(datetime.now(UTC))
    market_data = (
        _load_stored(days=args.days, end_at=end_at)
        if args.reuse_stored_data
        else _load_or_backfill(days=args.days, end_at=end_at)
    )

    report = run_comparison(market_data=market_data, entry_baseline=ENTRY_BASELINE_LIVE, max_workers=8)

    output = args.output or (
        root / "docs" / "audits" / f"{datetime.now(UTC).date().isoformat()}-exitladder-replay-comparison.md"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report.to_markdown(), encoding="utf-8")
    print(output)
    print(f"fixed_net_expectancy={report.policy_a.net_expectancy:.6f}")
    print(f"ladder_net_expectancy={report.policy_b.net_expectancy:.6f}")
    print(f"ladder_hits={report.policy_b.ladder_level_hits}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
