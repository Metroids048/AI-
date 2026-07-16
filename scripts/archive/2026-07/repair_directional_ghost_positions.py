"""Clear foreign Demo mirrors and ReduceOnly ghosts from directional PaperRuns.

Does not touch Binance account balances or strategy enable flags.
Uses local SQLite by default (.local_paper_console.db) unless POSTGRES_URL is set
to a reachable database.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def _repair_sqlite(db_path: Path, *, directional_run_id: str | None = None) -> dict:
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")
    runs = []
    for row in con.execute("SELECT paper_run_id, execution_profile FROM paper_runs"):
        ep = json.loads(row["execution_profile"] or "{}")
        lane = ep.get("strategy_lane")
        key = ep.get("auto_paper_runtime_key")
        if lane == "directional" or key == "auto_paper_mature_templates":
            runs.append(row["paper_run_id"])
    if directional_run_id:
        runs = [directional_run_id]
    cleared = []
    for run_id in runs:
        latest = con.execute(
            """
            SELECT p.symbol, p.side, p.quantity, p.entry_price, p.mark_price
            FROM position_snapshots p
            JOIN (
                SELECT symbol, MAX(rowid) AS rid
                FROM position_snapshots
                WHERE run_type='paper' AND run_id=?
                GROUP BY symbol
            ) t ON p.rowid = t.rid
            """,
            (run_id,),
        ).fetchall()
        for pos in latest:
            if abs(float(pos["quantity"] or 0)) <= 0:
                continue
            owned = con.execute(
                """
                SELECT gateway_order_id FROM order_executions
                WHERE paper_run_id=? AND symbol=? AND execution_status='filled'
                  AND (close_only_mode=0 OR close_only_mode IS NULL)
                  AND gateway_order_id IS NOT NULL AND gateway_order_id != ''
                ORDER BY rowid DESC LIMIT 1
                """,
                (run_id, pos["symbol"]),
            ).fetchone()
            if owned and owned["gateway_order_id"]:
                continue
            con.execute(
                """
                INSERT INTO position_snapshots (
                    position_snapshot_id, run_type, run_id, symbol, side, quantity,
                    entry_price, mark_price, unrealized_pnl, snapshot_time
                ) VALUES (?, 'paper', ?, ?, ?, 0.0, ?, ?, 0.0, ?)
                """,
                (
                    str(uuid4()),
                    run_id,
                    pos["symbol"],
                    pos["side"],
                    float(pos["entry_price"] or 0),
                    float(pos["mark_price"] or pos["entry_price"] or 0),
                    now,
                ),
            )
            cleared.append({"paper_run_id": run_id, "symbol": pos["symbol"]})
        metrics_row = con.execute(
            "SELECT paper_metrics_summary FROM paper_runs WHERE paper_run_id=?",
            (run_id,),
        ).fetchone()
        if metrics_row:
            metrics = json.loads(metrics_row["paper_metrics_summary"] or "{}")
            metrics["open_position_symbols"] = []
            metrics["protective_trailing"] = {}
            metrics["exit_ladder"] = {}
            metrics["last_ghost_repair_at"] = now
            con.execute(
                "UPDATE paper_runs SET paper_metrics_summary=? WHERE paper_run_id=?",
                (json.dumps(metrics), run_id),
            )
    con.commit()
    con.close()
    return {"database": str(db_path), "cleared": cleared, "repaired_at": now}


def main() -> int:
    db = ROOT / ".local_paper_console.db"
    if not db.exists():
        print(json.dumps({"error": f"missing {db}"}, ensure_ascii=False))
        return 1
    report = _repair_sqlite(db)
    out = ROOT / "docs" / "audits" / "_directional_ghost_repair.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
