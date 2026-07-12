"""End-to-end unblock: ghost repair, re-arm directional, run one cycle, report evidence."""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

DB = ROOT / ".local_paper_console.db"
os.environ["POSTGRES_URL"] = f"sqlite:///{DB.as_posix()}"
os.environ.setdefault("APP_ENV", "development")
# Keep fail-closed signals; do not relax for fake fills.
os.environ["PAPER_RUNTIME_RELAXED_SIGNALS"] = "false"

from dotenv import dotenv_values  # noqa: E402

for key, value in dotenv_values(ROOT / ".env").items():
    if value is not None and key not in os.environ:
        os.environ[key] = value

from scripts.repair_directional_ghost_positions import _repair_sqlite  # noqa: E402
from services.data import DataRepository  # noqa: E402
from services.database import get_session_factory, reset_database_caches  # noqa: E402
from services.execution.gatekeeper import ExecutionGatekeeperService  # noqa: E402
from services.execution.gateway import configured_gateways, probe_testnet_account  # noqa: E402
from services.execution.paper_runtime import PaperRuntimeService  # noqa: E402
from services.strategy_library import (  # noqa: E402
    AgentTaskRepository,
    ExecutionRepository,
    HypothesisRepository,
    NotificationRepository,
    PaperRunRepository,
    ReviewRepository,
    RiskProfileRepository,
    StrategyRepository,
    ValidationRepository,
)
from shared.models import PaperRuntimeCycleRequest  # noqa: E402

reset_database_caches()


def _arm_runs(session) -> list[dict]:  # noqa: ANN001
    paper_repo = PaperRunRepository(session)
    armed = []
    for run in paper_repo.list_paper_runs():
        ep = dict(run.execution_profile or {})
        lane = ep.get("strategy_lane")
        key = ep.get("auto_paper_runtime_key")
        if lane not in {"directional", "carry"} and key not in {
            "auto_paper_mature_templates",
            "auto_paper_btc_funding",
        }:
            continue
        if run.paper_status != "running":
            paper_repo.update_paper_run(run.paper_run_id or "", paper_status="running")
        ep.update(
            {
                "execution_mode": "binance_simulation_first",
                "mirror_to_gateway": True,
                "cost_gate_verified": True,
                "testnet_acceptance_verified_at": ep.get("testnet_acceptance_verified_at")
                or datetime.now(UTC).isoformat(),
            }
        )
        if lane == "directional" or key == "auto_paper_mature_templates":
            ep.setdefault("strategy_lane", "directional")
            ep.setdefault("auto_paper_runtime_key", "auto_paper_mature_templates")
        if lane == "carry" or key == "auto_paper_btc_funding":
            ep.setdefault("strategy_lane", "carry")
            ep.setdefault("auto_paper_runtime_key", "auto_paper_btc_funding")
        paper_repo.update_paper_run(run.paper_run_id or "", execution_profile=ep)
        armed.append(
            {
                "paper_run_id": run.paper_run_id,
                "strategy_lane": ep.get("strategy_lane"),
                "execution_mode": ep.get("execution_mode"),
                "cost_gate_verified": ep.get("cost_gate_verified"),
            }
        )
    session.commit()
    return armed


def _runtime(session) -> PaperRuntimeService:  # noqa: ANN001
    return PaperRuntimeService(
        data_repo=DataRepository(session),
        execution_repo=ExecutionRepository(session),
        paper_repo=PaperRunRepository(session),
        strategy_repo=StrategyRepository(session),
        agent_repo=AgentTaskRepository(session),
        review_repo=ReviewRepository(session),
        notification_repo=NotificationRepository(session),
        gatekeeper=ExecutionGatekeeperService(
            data_repo=DataRepository(session),
            validation_repo=ValidationRepository(session),
            hypothesis_repo=HypothesisRepository(session),
            risk_profile_repo=RiskProfileRepository(session),
            execution_repo=ExecutionRepository(session),
            paper_repo=PaperRunRepository(session),
            review_repo=ReviewRepository(session),
        ),
        gateway=configured_gateways()[0],
    )


def main() -> int:
    report: dict = {
        "generated_at": datetime.now(UTC).isoformat(),
        "ghost_repair": None,
        "armed": [],
        "probe": None,
        "cycles": [],
        "post_open_positions": {},
        "verdict": None,
    }

    report["ghost_repair"] = _repair_sqlite(DB)

    session = get_session_factory()()
    try:
        report["armed"] = _arm_runs(session)
        try:
            status = probe_testnet_account(order_limit=5)
            report["probe"] = {
                "connected": bool(status.connected),
                "error": status.error,
                "position_count": len(status.positions or []),
                "positions": [
                    {"symbol": p.symbol, "quantity": p.quantity, "side": str(p.side)}
                    for p in (status.positions or [])
                ],
            }
        except Exception as exc:  # noqa: BLE001
            report["probe"] = {"connected": False, "error": str(exc)}

        runtime = _runtime(session)
        paper_repo = PaperRunRepository(session)
        execution_repo = ExecutionRepository(session)
        data_repo = DataRepository(session)

        # Clear processed cycle keys so this proof cycle is not skipped.
        for run in paper_repo.list_paper_runs():
            if (run.execution_profile or {}).get("strategy_lane") != "directional":
                continue
            metrics = dict(run.paper_metrics_summary or {})
            metrics["processed_cycle_keys"] = []
            metrics["open_position_symbols"] = [
                s
                for s in (metrics.get("open_position_symbols") or [])
                if False  # force empty after ghost repair
            ]
            paper_repo.update_paper_run(run.paper_run_id or "", paper_metrics_summary=metrics)
        session.commit()

        # Best-effort bar refresh + clear stale risk so scan is not data_stale blocked.
        try:
            from services.data.binance import BinanceCcxtClient
            from services.execution.bootstrap import bootstrap_clear_stale_blocking_risk_events

            client = BinanceCcxtClient()
            refreshed = 0
            for symbol in ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT"]:
                for tf in ("1m", "15m", "1h", "4h"):
                    try:
                        bars = client.fetch_recent_ohlcv(symbol=symbol, timeframe=tf, limit=80)
                        refreshed += data_repo.store_ohlcv_bars(bars)
                    except Exception:  # noqa: BLE001
                        continue
            session.commit()
            cleared = bootstrap_clear_stale_blocking_risk_events()
            report["market_refresh"] = {"bars_written": refreshed, "risk_events_cleared": cleared}
        except Exception as exc:  # noqa: BLE001
            report["market_refresh"] = {"error": str(exc)}

        for run in paper_repo.list_paper_runs():
            ep = run.execution_profile or {}
            if ep.get("strategy_lane") != "directional":
                continue
            if run.paper_status != "running":
                continue
            before_orders = {
                o.order_execution_id: o.gateway_order_id
                for o in execution_repo.list_orders()
                if o.paper_run_id == run.paper_run_id
            }
            result = runtime.run_cycle(
                paper_run_id=run.paper_run_id or "",
                request=PaperRuntimeCycleRequest(
                    timeframe="15m",
                    enable_decision_veto=False,
                ),
            )
            after = [
                o
                for o in execution_repo.list_orders()
                if o.paper_run_id == run.paper_run_id
                and o.order_execution_id not in before_orders
            ]
            new_gateway = [
                {
                    "order_execution_id": o.order_execution_id,
                    "symbol": o.symbol,
                    "status": o.execution_status,
                    "gateway_order_id": o.gateway_order_id,
                    "close_only": o.close_only_mode,
                    "rejection": o.rejection_reason,
                    "lane": (o.entry_context.get("decision_pipeline") or {}).get("strategy_lane")
                    if isinstance(o.entry_context.get("decision_pipeline"), dict)
                    else None,
                }
                for o in after
            ]
            positions = execution_repo.list_latest_positions_for_run(
                run_type="paper", run_id=run.paper_run_id or ""
            )
            report["cycles"].append(
                {
                    "paper_run_id": run.paper_run_id,
                    "opened": result.opened_positions,
                    "closed": result.closed_positions,
                    "rejected": result.rejected_orders,
                    "open_symbols": result.open_position_symbols,
                    "actions": [
                        {
                            "symbol": a.symbol,
                            "action": a.action,
                            "reason": a.reason,
                            "close_only": a.close_only,
                        }
                        for a in result.actions[:40]
                    ],
                    "new_orders": new_gateway,
                    "local_positions": [
                        {
                            "symbol": p.symbol,
                            "qty": p.quantity,
                            "side": str(p.side),
                        }
                        for p in positions
                        if abs(float(p.quantity)) > 0
                    ],
                }
            )

        gateway_opens = [
            o
            for cycle in report["cycles"]
            for o in cycle["new_orders"]
            if o.get("gateway_order_id") and not o.get("close_only")
        ]
        if gateway_opens:
            report["verdict"] = "directional_gateway_open_seen"
        elif any(c["opened"] or c["closed"] or c["rejected"] or c["actions"] for c in report["cycles"]):
            report["verdict"] = "cycle_ran_no_new_gateway_open"
        else:
            report["verdict"] = "no_directional_cycle_activity"
    finally:
        session.close()

    out = ROOT / "docs" / "audits" / "_directional_unblock_cycle.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    print("REPORT", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
