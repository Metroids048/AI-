"""P0.3 observation snapshot — local Paper DB."""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / ".local_paper_console.db"
RUN = "78ba69a7"


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def main() -> int:
    if not DB.exists():
        print(f"missing db: {DB}", file=sys.stderr)
        return 1
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    now = datetime.now(UTC).isoformat()
    print(f"=== P0.3 observe @ {now} ===")

    print("\n-- exchange_orders (last 10) --")
    if _table_exists(conn, "exchange_orders"):
        for row in conn.execute(
            """
            SELECT exchange_order_id, symbol, side, state, created_at, local_order_execution_id
            FROM exchange_orders
            ORDER BY created_at DESC LIMIT 10
            """
        ):
            print(dict(row))
        print("count=", conn.execute("SELECT COUNT(*) FROM exchange_orders").fetchone()[0])
    else:
        print("(missing)")

    print("\n-- exchange_fill_receipts (last 10) --")
    if _table_exists(conn, "exchange_fill_receipts"):
        for row in conn.execute("SELECT * FROM exchange_fill_receipts ORDER BY created_at DESC LIMIT 10"):
            print(dict(row))
        print(
            "count=",
            conn.execute("SELECT COUNT(*) FROM exchange_fill_receipts").fetchone()[0],
        )
    else:
        print("(missing)")

    print("\n-- order_executions with gateway id (last 8) --")
    for row in conn.execute(
        """
        SELECT symbol, execution_status, gateway_order_id, rejection_reason, created_at, paper_run_id
        FROM order_executions
        WHERE gateway_order_id IS NOT NULL AND gateway_order_id != ''
        ORDER BY created_at DESC LIMIT 8
        """
    ):
        print(dict(row))

    print("\n-- funnel terminals (last 12 directional) --")
    for row in conn.execute(
        """
        SELECT symbol, bar_time, reason_code, status, created_at
        FROM decision_funnel_terminals
        WHERE paper_run_id LIKE ?
        ORDER BY created_at DESC LIMIT 12
        """,
        (f"{RUN}%",),
    ):
        print(dict(row))

    print("\n-- paper_runs directional profile BTC/ETH --")
    for row in conn.execute(
        "SELECT paper_run_id, execution_profile FROM paper_runs WHERE paper_run_id LIKE ?",
        (f"{RUN}%",),
    ):
        profile = json.loads(row["execution_profile"] or "{}")
        assets = profile.get("universe_assets") or []
        slim = [
            {
                "symbol": a.get("symbol") or a.get("platform_symbol"),
                "tradable_status": a.get("tradable_status"),
                "min_notional": a.get("min_notional"),
                "reason": a.get("reason"),
            }
            for a in assets
            if (a.get("symbol") or a.get("platform_symbol")) in {"BTC/USDT", "ETH/USDT"}
        ]
        print(
            {
                "paper_run_id": row["paper_run_id"],
                "execution_mode": profile.get("execution_mode"),
                "mirror": profile.get("mirror_to_gateway"),
                "sampling": profile.get("simulation_sampling_fallback_enabled"),
                "assets": slim,
            }
        )

    state_path = ROOT / "logs" / "scheduler-state.json"
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        print("\n-- scheduler-state --")
        print(
            {
                "running": state.get("running"),
                "heartbeat_at": state.get("heartbeat_at"),
                "last_auto_cycle_at": state.get("last_auto_cycle_at"),
                "exchange_info_ready": state.get("exchange_info_ready"),
                "scheduler_error": state.get("scheduler_error"),
                "last_scheduled_for": state.get("last_scheduled_for"),
            }
        )
        last = (state.get("task_last_results") or {}).get("paper_runtime_cycle") or {}
        results = last.get("results") or []
        for result in results[:2]:
            print(
                {
                    "paper_run_id": result.get("paper_run_id"),
                    "cycle_time": result.get("cycle_time"),
                    "skipped": result.get("skipped_symbols"),
                    "opened": result.get("opened_positions"),
                }
            )
            for action in (result.get("actions") or [])[:4]:
                trace = action.get("decision_trace") or {}
                print(
                    {
                        "symbol": action.get("symbol"),
                        "action": action.get("action"),
                        "reason": action.get("reason"),
                        "sampling_reason": trace.get("sampling_fallback_rejection_reason"),
                        "evaluated_at": (trace.get("sampling_metrics") or {}).get("evaluated_at"),
                    }
                )
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
