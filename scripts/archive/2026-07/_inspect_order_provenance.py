"""Deep-dive order provenance for directional reachability."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / ".local_paper_console.db"


def main() -> None:
    con = sqlite3.connect(str(DB))
    con.row_factory = sqlite3.Row
    # Count by paper_run and presence of decision_pipeline
    rows = con.execute(
        """
        SELECT order_execution_id, paper_run_id, symbol, direction, gateway_order_id,
               close_only_mode, entry_context, execution_status, created_at
        FROM order_executions
        WHERE gateway_order_id IS NOT NULL AND gateway_order_id != ''
        ORDER BY rowid DESC
        LIMIT 200
        """
    ).fetchall()
    summary = {
        "total_with_gateway": len(rows),
        "by_paper_run": {},
        "with_decision_pipeline": 0,
        "with_pipeline_status": 0,
        "with_strategy_lane_carry": 0,
        "with_strategy_lane_directional": 0,
        "open_non_close_only": 0,
        "samples": [],
    }
    for r in rows:
        d = dict(r)
        ctx = d.get("entry_context")
        if isinstance(ctx, str):
            try:
                ctx = json.loads(ctx)
            except Exception:  # noqa: BLE001
                ctx = {}
        trace = (ctx or {}).get("decision_pipeline")
        has_trace = isinstance(trace, dict)
        if has_trace:
            summary["with_decision_pipeline"] += 1
            if trace.get("pipeline_status"):
                summary["with_pipeline_status"] += 1
            lane = trace.get("strategy_lane")
            if lane == "carry":
                summary["with_strategy_lane_carry"] += 1
            if lane == "directional":
                summary["with_strategy_lane_directional"] += 1
        if not d.get("close_only_mode"):
            summary["open_non_close_only"] += 1
        pr = d.get("paper_run_id") or "none"
        bucket = summary["by_paper_run"].setdefault(
            pr,
            {"count": 0, "open": 0, "close": 0, "with_pipeline": 0, "acceptance_like": 0},
        )
        bucket["count"] += 1
        if d.get("close_only_mode"):
            bucket["close"] += 1
        else:
            bucket["open"] += 1
        if has_trace:
            bucket["with_pipeline"] += 1
        # acceptance scripts often lack decision_pipeline
        if not has_trace:
            bucket["acceptance_like"] += 1
        if len(summary["samples"]) < 20:
            summary["samples"].append(
                {
                    "order_execution_id": d.get("order_execution_id"),
                    "paper_run_id": d.get("paper_run_id"),
                    "symbol": d.get("symbol"),
                    "gateway_order_id": d.get("gateway_order_id"),
                    "close_only_mode": bool(d.get("close_only_mode")),
                    "execution_status": d.get("execution_status"),
                    "has_decision_pipeline": has_trace,
                    "pipeline_status": (trace or {}).get("pipeline_status") if has_trace else None,
                    "strategy_lane": (trace or {}).get("strategy_lane") if has_trace else None,
                    "entry_context_keys": sorted((ctx or {}).keys())[:30],
                    "created_at": d.get("created_at"),
                }
            )

    # Map paper runs
    runs = {
        r["paper_run_id"]: dict(r)
        for r in con.execute("SELECT paper_run_id, strategy_id, execution_profile FROM paper_runs").fetchall()
    }
    run_meta = {}
    for pid, raw in runs.items():
        ep = raw.get("execution_profile")
        if isinstance(ep, str):
            try:
                ep = json.loads(ep)
            except Exception:  # noqa: BLE001
                ep = {}
        run_meta[pid] = {
            "strategy_id": raw.get("strategy_id"),
            "strategy_lane": (ep or {}).get("strategy_lane"),
            "execution_mode": (ep or {}).get("execution_mode"),
            "cost_gate_verified": (ep or {}).get("cost_gate_verified"),
            "mirror_to_gateway": (ep or {}).get("mirror_to_gateway"),
            "runtime_key": (ep or {}).get("runtime_key") or (ep or {}).get("auto_runtime_key"),
        }
    summary["paper_run_meta"] = run_meta

    # Check if directional run has ANY orders with gateway + pipeline
    directional_id = "457c6ecd-88a1-434a-b422-06769536773e"
    dir_orders = con.execute(
        """
        SELECT order_execution_id, symbol, gateway_order_id, close_only_mode, entry_context
        FROM order_executions
        WHERE paper_run_id = ?
        ORDER BY rowid DESC LIMIT 50
        """,
        (directional_id,),
    ).fetchall()
    dir_summary = {"total": len(dir_orders), "with_gateway": 0, "with_pipeline": 0, "samples": []}
    for r in dir_orders:
        d = dict(r)
        ctx = d.get("entry_context")
        if isinstance(ctx, str):
            try:
                ctx = json.loads(ctx)
            except Exception:  # noqa: BLE001
                ctx = {}
        trace = (ctx or {}).get("decision_pipeline")
        if d.get("gateway_order_id"):
            dir_summary["with_gateway"] += 1
        if isinstance(trace, dict):
            dir_summary["with_pipeline"] += 1
        if len(dir_summary["samples"]) < 10:
            dir_summary["samples"].append(
                {
                    "id": d.get("order_execution_id"),
                    "symbol": d.get("symbol"),
                    "gateway_order_id": d.get("gateway_order_id"),
                    "close_only": bool(d.get("close_only_mode")),
                    "pipeline_status": trace.get("pipeline_status") if isinstance(trace, dict) else None,
                    "strategy_lane": trace.get("strategy_lane") if isinstance(trace, dict) else None,
                }
            )
    summary["directional_run_457c6ecd"] = dir_summary

    out = ROOT / "docs" / "audits" / "_phase_a_order_provenance.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    con.close()


if __name__ == "__main__":
    main()
