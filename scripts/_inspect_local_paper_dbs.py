"""Inspect local SQLite for directional Binance reachability evidence."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def inspect(db_path: str) -> dict:
    p = ROOT / db_path
    if not p.exists():
        return {"path": db_path, "missing": True}
    con = sqlite3.connect(str(p))
    con.row_factory = sqlite3.Row
    tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    out: dict = {"path": db_path, "size": p.stat().st_size, "tables": tables, "paper_runs": [], "orders_sample": []}
    if "paper_runs" in tables:
        rows = con.execute("SELECT * FROM paper_runs").fetchall()
        for r in rows:
            d = dict(r)
            ep = d.get("execution_profile")
            if isinstance(ep, str):
                try:
                    ep = json.loads(ep)
                except Exception:  # noqa: BLE001
                    ep = {}
            out["paper_runs"].append(
                {
                    "paper_run_id": d.get("paper_run_id"),
                    "strategy_id": d.get("strategy_id"),
                    "status": d.get("status"),
                    "execution_mode": (ep or {}).get("execution_mode"),
                    "mirror_to_gateway": (ep or {}).get("mirror_to_gateway"),
                    "cost_gate_verified": (ep or {}).get("cost_gate_verified"),
                    "strategy_lane": (ep or {}).get("strategy_lane"),
                }
            )
    for t in ("order_executions", "orders"):
        if t not in tables:
            continue
        rows = con.execute(f"SELECT * FROM {t} ORDER BY rowid DESC LIMIT 100").fetchall()
        gw = 0
        directional_gw = 0
        for r in rows:
            d = dict(r)
            gid = d.get("gateway_order_id")
            ctx = d.get("entry_context")
            if isinstance(ctx, str):
                try:
                    ctx = json.loads(ctx)
                except Exception:  # noqa: BLE001
                    ctx = {}
            trace = (ctx or {}).get("decision_pipeline") or {}
            lane = trace.get("strategy_lane") if isinstance(trace, dict) else None
            is_carry = lane == "carry" or (isinstance(trace, dict) and "funding_bps" in trace)
            if gid:
                gw += 1
                if not is_carry:
                    directional_gw += 1
                    if len(out["orders_sample"]) < 15:
                        out["orders_sample"].append(
                            {
                                "id": d.get("order_execution_id") or d.get("id"),
                                "symbol": d.get("symbol"),
                                "gateway_order_id": gid,
                                "strategy_lane": lane,
                                "pipeline_status": trace.get("pipeline_status") if isinstance(trace, dict) else None,
                                "close_only": d.get("close_only_mode"),
                                "status": d.get("status"),
                            }
                        )
        out[f"{t}_gateway_count_recent100"] = gw
        out[f"{t}_directional_gateway_recent100"] = directional_gw
        break
    con.close()
    return out


def main() -> None:
    results = []
    for db in (
        ".dev_ai_quant.db",
        ".local_paper_console.db",
        ".browser_paper_smoke.db",
        ".local/test-runtime/paper_console.db",
    ):
        results.append(inspect(db))
    out = ROOT / "docs" / "audits" / "_phase_a_sqlite_raw.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
