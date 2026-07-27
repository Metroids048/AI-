"""Check all execution_blockers without requiring the HTTP server to be running.

Mirrors the logic from apps/api/routers/runs.py::get_trading_status.
Usage:
    python -m scripts.check_execution_blockers
    python -m scripts.check_execution_blockers --database-url sqlite:///.local_paper_console.db
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

from services.data.universe import AUTO_SIMULATION_EXECUTION_SYMBOLS, execution_scope_hash
from services.execution.bootstrap import AUTO_PAPER_TECHNICAL_KEY
from services.execution.gateway import BinanceUsdtPerpetualGateway
from shared.config import settings

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / ".local_paper_console.db"
STATE_PATH = ROOT / "logs" / "scheduler-state.json"

GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
RESET = "\033[0m"


def _ok(msg: str) -> None:
    print(f"  {GREEN}OK{RESET}  {msg}")


def _warn(msg: str) -> None:
    print(f"  {YELLOW}WARN{RESET} {msg}")


def _fail(msg: str) -> None:
    print(f"  {RED}FAIL{RESET} {msg}")


def _check_settings_blockers() -> list[str]:
    blockers: list[str] = []
    if not settings.binance_use_testnet or settings.live_trading_enabled:
        blockers.append("safety_boundary")
    credentials_configured = bool(settings.binance_api_key and settings.binance_api_secret)
    if not credentials_configured:
        blockers.append("missing_credentials")
    if credentials_configured and not BinanceUsdtPerpetualGateway.capability.supports_order_submit:
        blockers.append("gateway_unavailable")
    if not settings.binance_auto_execute:
        blockers.append("auto_execute_disabled")
    return blockers


def _load_scheduler_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {}


def _check_scheduler_blockers(
    state: dict,
    *,
    now: datetime | None = None,
) -> list[str]:
    if not state.get("running"):
        return [state.get("reason") or "scheduler_offline"]
    heartbeat_raw = state.get("heartbeat_at")
    try:
        heartbeat_at = datetime.fromisoformat(str(heartbeat_raw).replace("Z", "+00:00"))
        if heartbeat_at.tzinfo is None:
            heartbeat_at = heartbeat_at.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return ["scheduler_heartbeat_missing"]
    heartbeat_period = float(state.get("heartbeat_interval_seconds") or settings.market_data_heartbeat_seconds)
    max_age_seconds = max(2 * heartbeat_period, 120.0)
    reference = now or datetime.now(UTC)
    if (reference - heartbeat_at.astimezone(UTC)).total_seconds() > max_age_seconds:
        return ["scheduler_heartbeat_stale"]
    return []


def _check_market_data_blockers(state: dict) -> list[str]:
    blockers: list[str] = []
    expected = len(AUTO_SIMULATION_EXECUTION_SYMBOLS)
    coverage = state.get("execution_coverage_count") or state.get("top20_coverage_count") or 0
    if int(coverage) != expected:
        blockers.append("market_data_coverage_incomplete")
    if not state.get("exchange_info_ready"):
        blockers.append("exchange_info_not_ready")
    if not state.get("data_fresh"):
        blockers.append("market_data_stale")
    return blockers


def _check_db_blockers(db_path: Path) -> list[str]:
    blockers: list[str] = []
    if not db_path.exists():
        return ["database_not_found"]

    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row

        now_iso = datetime.now(UTC).isoformat()
        try:
            rows = conn.execute(
                "SELECT id, level, title, resolution_status, expires_at, affected_scope FROM risk_events "
                "WHERE level IN ('high','critical') "
                "AND resolution_status IN ('detected','acknowledged') "
                "AND (expires_at IS NULL OR expires_at > ?) LIMIT 5",
                (now_iso,),
            ).fetchall()
            if rows:
                blockers.append("blocking_risk_event")
                for row in rows:
                    print(
                        f"  {YELLOW}risk_event detail:{RESET} id={row[0]} level={row[1]} title={row[2]!r} "
                        f"status={row[3]} expires_at={row[4]} scope={row[5]}"
                    )
        except Exception:
            # risk_events table absent in local SQLite dev DB → no blocking events
            pass

        expected = list(AUTO_SIMULATION_EXECUTION_SYMBOLS)
        try:
            acceptance_rows = conn.execute(
                "SELECT output_payload FROM agent_tasks "
                "WHERE task_type = 'testnet_acceptance' AND task_status = 'completed' "
                "ORDER BY created_at DESC LIMIT 20"
            ).fetchall()
        except Exception:
            acceptance_rows = []

        acceptance_found = False
        for row in acceptance_rows:
            try:
                raw = row["output_payload"]
                payload = json.loads(raw) if isinstance(raw, str) else (raw or {})
                requested = list(payload.get("requested_symbols") or payload.get("completed_symbols") or [])
                completed = list(payload.get("completed_symbols") or [])
                if (
                    payload.get("final_open_position_count") == 0
                    and payload.get("final_open_order_count") == 0
                    and requested == expected
                    and completed == expected
                    and int(payload.get("filled_order_count") or 0) >= 2 * len(expected)
                ):
                    acceptance_found = True
                    break
            except Exception:
                continue

        if not acceptance_found:
            blockers.append("testnet_acceptance_not_verified")
        else:
            expected_scope_hash = execution_scope_hash(expected)
            try:
                run_rows = conn.execute(
                    "SELECT execution_profile, paper_metrics_summary FROM paper_runs "
                    "WHERE paper_status = 'running' ORDER BY created_at DESC"
                ).fetchall()
            except Exception:
                run_rows = []

            directional_run_armed = False
            for row in run_rows:
                try:
                    raw_profile = row["execution_profile"]
                    profile = json.loads(raw_profile) if isinstance(raw_profile, str) else (raw_profile or {})
                except Exception:
                    continue
                if profile.get("auto_paper_runtime_key") != AUTO_PAPER_TECHNICAL_KEY:
                    continue
                directional_run_armed = bool(
                    profile.get("execution_mode") == "binance_testnet"
                    and profile.get("mirror_to_gateway") is True
                    and profile.get("cost_gate_verified") is True
                    and list(profile.get("acceptance_symbols") or []) == expected
                    and profile.get("acceptance_scope_hash") == expected_scope_hash
                )
                try:
                    raw_metrics = row["paper_metrics_summary"]
                    metrics = json.loads(raw_metrics) if isinstance(raw_metrics, str) else (raw_metrics or {})
                except Exception:
                    metrics = {}
                unmanaged_symbols = list(metrics.get("unmanaged_external_symbols") or [])
                if unmanaged_symbols:
                    blockers.append("unmanaged_external_position")
                    print(f"  {YELLOW}unmanaged exchange positions:{RESET} " + ", ".join(unmanaged_symbols))
                break

            if not directional_run_armed:
                blockers.append("directional_run_not_armed")

        conn.close()
    except Exception as exc:
        blockers.append(f"database_error:{exc}")

    return blockers


def main(db_path: Path = DB_PATH) -> int:
    active_symbols = list(AUTO_SIMULATION_EXECUTION_SYMBOLS)
    print("\n=== Execution Blockers Check ===")
    print(f"AUTO_SIMULATION_EXECUTION_SYMBOLS : {active_symbols}")
    print(f"DB                                : {db_path}")
    print(f"Scheduler state                   : {STATE_PATH}\n")

    all_blockers: list[str] = []

    # Layer 1: Settings / config
    print("--- Layer 1: Settings / Config ---")
    s_blockers = _check_settings_blockers()
    if not s_blockers:
        credentials_ok = bool(settings.binance_api_key and settings.binance_api_secret)
        _ok(f"binance_use_testnet={settings.binance_use_testnet}  live_trading_enabled={settings.live_trading_enabled}")
        _ok(f"credentials_configured={credentials_ok}")
        _ok(f"binance_auto_execute={settings.binance_auto_execute}")
    else:
        for b in s_blockers:
            _fail(f"blocker: {b}")
    all_blockers.extend(s_blockers)

    # Layer 2: Scheduler state
    print("\n--- Layer 2: Scheduler State ---")
    state = _load_scheduler_state()
    if not state:
        _warn("scheduler-state.json not found or empty — treating scheduler as offline")
        all_blockers.append("scheduler_state_missing")
    else:
        sched_blockers = _check_scheduler_blockers(state)
        mdata_blockers = _check_market_data_blockers(state)
        if not sched_blockers:
            _ok(f"scheduler running={state.get('running')}  last_auto_cycle_at={state.get('last_auto_cycle_at')}")
        else:
            for b in sched_blockers:
                _fail(f"blocker: {b}")
        if not mdata_blockers:
            cov = state.get("execution_coverage_count") or state.get("top20_coverage_count")
            _ok(
                f"market_data coverage={cov}/{len(active_symbols)}"
                f"  data_fresh={state.get('data_fresh')}"
                f"  exchange_info_ready={state.get('exchange_info_ready')}"
            )
        else:
            details = (
                f"execution_coverage_count={state.get('execution_coverage_count')}"
                f"  top20_coverage_count={state.get('top20_coverage_count')}"
                f"  data_fresh={state.get('data_fresh')}"
                f"  exchange_info_ready={state.get('exchange_info_ready')}"
            )
            for b in mdata_blockers:
                _fail(f"blocker: {b}  ({details})")
        all_blockers.extend(sched_blockers)
        all_blockers.extend(mdata_blockers)

    # Layer 3: Database
    print("\n--- Layer 3: Database ---")
    db_blockers = _check_db_blockers(db_path)
    if not db_blockers:
        _ok(f"no blocking_risk_event  testnet_acceptance_verified  directional_run_armed for {active_symbols}")
    else:
        for b in db_blockers:
            _fail(f"blocker: {b}")

    if "testnet_acceptance_not_verified" in db_blockers:
        print(
            f"\n  {YELLOW}ACTION REQUIRED:{RESET} Trigger new acceptance run for exactly {active_symbols}\n"
            f'  POST /api/runs/testnet-acceptance  body: {{"symbols": {active_symbols}}}\n'
            f"  NOTE: old run da7edfd9 covered BTC/ETH/SOL (3 symbols) — exact-match fails for BTC/ETH (2 symbols)"
        )

    if "directional_run_not_armed" in db_blockers:
        print(
            f"\n  {YELLOW}ACTION REQUIRED:{RESET} The exact-scope acceptance exists, but the running BTC/ETH "
            "directional lane is still paper_only or has stale authorization metadata.\n"
            "  Restart the exchange-first runtime so bootstrap can re-arm the retained directional run; "
            "then rerun this check."
        )

    all_blockers.extend(db_blockers)

    # Summary
    print("\n=== Summary ===")
    if not all_blockers:
        print(f"{GREEN}execution_ready=True — no blockers{RESET}")
        return 0

    print(f"{RED}execution_ready=False — {len(all_blockers)} blocker(s):{RESET}")
    for b in all_blockers:
        print(f"  - {b}")
    return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Check execution blockers without running the HTTP server.")
    parser.add_argument("--database-url", default=None, help="e.g. sqlite:///.local_paper_console.db")
    args = parser.parse_args()

    db_path = DB_PATH
    if args.database_url and args.database_url.startswith("sqlite:///"):
        db_path = Path(args.database_url[len("sqlite:///") :])

    sys.exit(main(db_path=db_path))
