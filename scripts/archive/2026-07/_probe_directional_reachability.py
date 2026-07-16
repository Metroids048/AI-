"""Read-only probe for directional Binance reachability (Phase A)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)


def main() -> int:
    from shared.config import settings

    report: dict = {
        "env": {
            "binance_auto_execute": bool(settings.binance_auto_execute),
            "binance_use_testnet": bool(settings.binance_use_testnet),
            "live_trading_enabled": bool(settings.live_trading_enabled),
            "credentials_configured": bool(settings.binance_api_key and settings.binance_api_secret),
            "postgres_url_set": bool(getattr(settings, "postgres_url", None) or os.getenv("POSTGRES_URL")),
        },
        "code_path": {
            "default_mirror_to_gateway": False,
            "directional_trace_writes_strategy_lane": False,
            "should_execute_non_carry_rule": (
                "pipeline_status truthy AND no rejection_codes "
                "(when mirror armed + cost_gate_verified + env gates)"
            ),
            "carry_rule": "estimated_net_edge_bps >= min_estimated_net_edge_bps",
        },
        "paper_runs": [],
        "directional_orders_with_gateway": [],
        "probe": None,
        "verdict": None,
        "verdict_reason": None,
    }

    # Code evidence: does _trace write strategy_lane?
    from services.execution import decision_pipeline as dp

    sample = dp._trace(
        status="bet_taken",
        signals=[],
        ensemble=None,
        meta_label=None,
        veto_result=None,
        volatility={},
    )
    report["code_path"]["directional_trace_writes_strategy_lane"] = "strategy_lane" in sample
    report["code_path"]["sample_trace_keys"] = sorted(sample.keys())

    from services.execution.bootstrap import default_mirror_to_gateway

    report["code_path"]["default_mirror_to_gateway"] = bool(default_mirror_to_gateway())

    # DB paper runs + orders
    try:
        from services.database import get_session_factory
        from services.strategy_library import ExecutionRepository, PaperRunRepository

        with get_session_factory()() as session:
            paper_repo = PaperRunRepository(session)
            exec_repo = ExecutionRepository(session)
            runs = paper_repo.list_paper_runs()
            for run in runs:
                profile = dict(run.execution_profile or {})
                report["paper_runs"].append(
                    {
                        "paper_run_id": run.paper_run_id,
                        "strategy_id": run.strategy_id,
                        "status": str(run.status),
                        "strategy_lane": profile.get("strategy_lane"),
                        "execution_mode": profile.get("execution_mode"),
                        "mirror_to_gateway": profile.get("mirror_to_gateway"),
                        "cost_gate_verified": profile.get("cost_gate_verified"),
                        "runtime_key": profile.get("runtime_key") or profile.get("auto_runtime_key"),
                    }
                )
            # Scan recent orders for directional gateway evidence
            try:
                orders = exec_repo.list_orders(limit=200)
            except TypeError:
                orders = exec_repo.list_orders()
            directional_hits = []
            for order in orders[:200]:
                ctx = dict(getattr(order, "entry_context", None) or {})
                trace = ctx.get("decision_pipeline") or {}
                lane = trace.get("strategy_lane") if isinstance(trace, dict) else None
                # Directional heuristic: has pipeline_status and not carry
                is_carry = lane == "carry" or (
                    isinstance(trace, dict) and "estimated_net_edge_bps" in trace and "funding_bps" in trace
                )
                gw = getattr(order, "gateway_order_id", None)
                if not is_carry and gw:
                    directional_hits.append(
                        {
                            "order_execution_id": getattr(order, "order_execution_id", None),
                            "symbol": getattr(order, "symbol", None),
                            "gateway_order_id": gw,
                            "pipeline_status": trace.get("pipeline_status") if isinstance(trace, dict) else None,
                            "strategy_lane": lane,
                            "close_only_mode": bool(getattr(order, "close_only_mode", False)),
                        }
                    )
            report["directional_orders_with_gateway"] = directional_hits[:30]
            report["directional_gateway_order_count"] = len(directional_hits)
    except Exception as exc:  # noqa: BLE001
        report["db_error"] = str(exc)

    # Optional testnet probe
    if report["env"]["credentials_configured"] and report["env"]["binance_use_testnet"]:
        try:
            from services.execution.gateway import probe_testnet_account

            status = probe_testnet_account(order_limit=5)
            report["probe"] = {
                "connected": bool(status.connected),
                "trading_mode": status.trading_mode,
                "error": status.error,
                "position_count": len(status.positions or []),
                "order_count": len(status.recent_orders or []),
            }
        except Exception as exc:  # noqa: BLE001
            report["probe"] = {"connected": False, "error": str(exc)}

    # Verdict
    directional_runs = [
        r
        for r in report["paper_runs"]
        if r.get("strategy_lane") == "directional"
        or (r.get("runtime_key") and "technical" in str(r.get("runtime_key")))
        or (r.get("strategy_id") and "technical" in str(r.get("strategy_id")))
        or (r.get("strategy_id") and "mature" in str(r.get("strategy_id")))
    ]
    armed = [
        r
        for r in directional_runs
        if (
            r.get("execution_mode") == "binance_simulation_first" or r.get("mirror_to_gateway")
        )
        and r.get("cost_gate_verified")
    ]
    env_ok = (
        report["env"]["binance_auto_execute"]
        and report["env"]["binance_use_testnet"]
        and not report["env"]["live_trading_enabled"]
        and report["env"]["credentials_configured"]
    )

    if report.get("directional_gateway_order_count"):
        report["verdict"] = "已触达"
        report["verdict_reason"] = (
            f"Found {report['directional_gateway_order_count']} non-carry orders with gateway_order_id."
        )
    elif not env_ok:
        report["verdict"] = "武装不全"
        report["verdict_reason"] = "Global env gates incomplete (auto_execute/testnet/credentials/live)."
    elif not armed:
        report["verdict"] = "武装不全"
        report["verdict_reason"] = (
            "No directional paper run with binance_simulation_first/mirror + cost_gate_verified."
        )
    else:
        report["verdict"] = "仅本地记账"
        report["verdict_reason"] = (
            "Directional run appears armed and env OK, but no non-carry gateway_order_id found in recent orders. "
            "Code path allows directional mirror when pipeline_status is set; absence of fills may be signal filters."
        )

    out = ROOT / "docs" / "audits" / "_phase_a_probe_raw.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
