"""Audit full lifecycle completion of directional trading positions.

Answers the question: "Are there any trades that open but never close properly?"

Classifies all OrderExecution records into four categories:
- A: Opened and properly closed via strategy exit logic (normal lifecycle complete)
- B: Opened and currently in-progress (not yet hit exit conditions, normal)
- C: Opened and stuck (exceed 2x time_exit_hours without any exit, zombie positions)
- D: Opened locally but never found on exchange (ledger fork evidence)

Usage:
    python scripts/audit_full_lifecycle_completion.py [--days N] [--database-url URL]
"""

from __future__ import annotations

import argparse
import os
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from services.strategy_library.repository import ExecutionRepository, StrategyRepository
from shared.models import OrderExecution


def _as_aware(dt: datetime) -> datetime:
    """Convert naive datetime to aware UTC (in case sqlite returns naive)."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def audit_lifecycle_completion(
    *,
    days: int = 90,
    database_url: str | None = None,
) -> dict[str, list[OrderExecution]]:
    """Audit all directional trades and classify their lifecycle status.

    Returns:
        Dict with keys: 'completed', 'in_progress', 'stuck', 'ledger_fork'
    """
    database_url = database_url or os.getenv("POSTGRES_URL", "sqlite:///.local/paper-runtime.db")
    engine = create_engine(database_url)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()

    exec_repo = ExecutionRepository(session=session)
    strat_repo = StrategyRepository(session=session)

    # Fetch all orders from the audit window
    since = datetime.now(UTC) - timedelta(days=days)
    all_orders = exec_repo.list_orders()  # No date filter at repo level (risk noted in handoff)

    # Filter client-side for the audit window and exclude link_verification/demo-only
    audit_orders = [
        o for o in all_orders
        if _as_aware(o.created_at) >= since
        and o.strategy_lane not in {"link_verification", "binance_demo_audit_only"}
    ]

    # Group by (symbol, side) to pair opens with closes
    opens: dict[tuple[str, str], list[OrderExecution]] = defaultdict(list)
    closes: dict[tuple[str, str], list[OrderExecution]] = defaultdict(list)

    for order in audit_orders:
        key = (order.symbol, order.side.value)
        if order.reduce_only:
            closes[key].append(order)
        else:
            opens[key].append(order)

    # Classify each open order
    completed: list[OrderExecution] = []
    in_progress: list[OrderExecution] = []
    stuck: list[OrderExecution] = []
    ledger_fork: list[OrderExecution] = []

    for (symbol, side), open_list in opens.items():
        close_list = closes.get((symbol, side), [])

        for open_order in open_list:
            # Find matching close (same symbol, opposite reduce_only, created after open)
            matching_close = next(
                (c for c in close_list if _as_aware(c.created_at) > _as_aware(open_order.created_at)),
                None,
            )

            if matching_close:
                # A: Opened and properly closed
                completed.append(open_order)
                continue

            # Check if this is a zombie position (stuck for >2x time_exit_hours)
            strategy_key = open_order.strategy_key
            strategy = strat_repo.get_by_key(strategy_key)

            if strategy:
                time_exit_hours = strategy.rules.exit_rules.get("time_exit_hours", 24)
                max_hold_time = timedelta(hours=time_exit_hours * 2)
                age = datetime.now(UTC) - _as_aware(open_order.created_at)

                if age > max_hold_time:
                    # C: Stuck (zombie position)
                    stuck.append(open_order)
                else:
                    # B: In-progress (normal, just hasn't hit exit yet)
                    in_progress.append(open_order)
            else:
                # No strategy found (should not happen, but treat as stuck)
                stuck.append(open_order)

            # D: Ledger fork check (if order_id never appeared on exchange)
            # This would require querying the exchange API; for now we only detect via absence of fill
            if not open_order.filled_quantity or open_order.filled_quantity == 0:
                ledger_fork.append(open_order)

    session.close()

    return {
        "completed": completed,
        "in_progress": in_progress,
        "stuck": stuck,
        "ledger_fork": ledger_fork,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit directional trade lifecycle completion")
    parser.add_argument("--days", type=int, default=90, help="Audit window in days (default: 90)")
    parser.add_argument("--database-url", help="Override POSTGRES_URL env var")
    args = parser.parse_args()

    print(f"Auditing trade lifecycle over past {args.days} days...")
    print(f"Database: {args.database_url or os.getenv('POSTGRES_URL', 'sqlite:///.local/paper-runtime.db')}")
    print()

    results = audit_lifecycle_completion(days=args.days, database_url=args.database_url)

    # Print summary
    print("=" * 80)
    print("LIFECYCLE AUDIT SUMMARY")
    print("=" * 80)
    print(f"A (Completed):     {len(results['completed']):>4} trades opened and properly closed")
    print(f"B (In-progress):   {len(results['in_progress']):>4} trades opened and still holding (normal)")
    print(f"C (Stuck):         {len(results['stuck']):>4} trades opened >2x time_exit_hours, no close (ZOMBIE)")
    print(f"D (Ledger fork):   {len(results['ledger_fork']):>4} trades opened locally but never filled")
    print("=" * 80)
    print()

    # Detailed output for problematic categories
    if results["stuck"]:
        print("⚠️  STUCK POSITIONS (Category C):")
        print("-" * 80)
        for order in results["stuck"]:
            age_hours = (datetime.now(UTC) - _as_aware(order.created_at)).total_seconds() / 3600
            print(f"  {order.symbol:12} {order.side.value:4} | "
                  f"Opened: {order.created_at.isoformat()[:19]} | "
                  f"Age: {age_hours:.1f}h | "
                  f"Strategy: {order.strategy_key}")
        print()

    if results["ledger_fork"]:
        print("⚠️  LEDGER FORK (Category D):")
        print("-" * 80)
        for order in results["ledger_fork"]:
            print(f"  {order.symbol:12} {order.side.value:4} | "
                  f"Opened: {order.created_at.isoformat()[:19]} | "
                  f"Order ID: {order.order_id} | "
                  f"Filled: {order.filled_quantity or 0}")
        print()

    # Interpretation guide
    print("INTERPRETATION:")
    print("-" * 80)
    if len(results["completed"]) == 0 and (len(results["stuck"]) > 0 or len(results["ledger_fork"]) > 0):
        print("⚠️  NO TRADES COMPLETED + STUCK/FORK EXISTS")
        print("    → Execution layer has a bug preventing normal trade lifecycle.")
        print("    → Fix this BEFORE investigating strategy/signal quality.")
    elif len(results["completed"]) > 0 and len(results["stuck"]) == 0:
        print("✅  TRADES COMPLETING NORMALLY")
        print("    → Execution layer is working.")
        print("    → If overall performance is poor, focus on signal quality (Module 5/6/7).")
    elif len(results["completed"]) > 0 and len(results["stuck"]) > 0:
        print("⚠️  MIXED: SOME COMPLETE, SOME STUCK")
        print("    → Review stuck trades for common pattern (symbol/time/exit_rules).")
    else:
        print("ℹ️  ALL TRADES IN-PROGRESS OR VERY RECENT")
        print("    → Extend --days or wait longer to accumulate lifecycle samples.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
