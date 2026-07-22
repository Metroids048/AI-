"""Run the bounded fixed-Top20 Binance Futures Testnet acceptance flow.

Runs the acceptance service directly (no HTTP server required) and persists
the result into the local agent_tasks table so that find_verified_testnet_acceptance
can clear the testnet_acceptance_not_verified blocker.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

from services.data.universe import AUTO_SIMULATION_EXECUTION_SYMBOLS
from services.execution.gateway import BinanceUsdtPerpetualGateway
from services.execution.testnet_acceptance import TestnetAcceptanceService
from services.execution.testnet_cleanup import testnet_account_cleanup
from shared.config import settings
from shared.models import TestnetAcceptanceRunRequest, TestnetAcceptanceRunResult

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / ".local_paper_console.db"

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"


def _persist_result(
    result: TestnetAcceptanceRunResult,
    task_id: str,
    now_iso: str,
    *,
    authorization_reason: str,
) -> None:
    """Save acceptance result to agent_tasks so blockers check can find it."""
    if not DB_PATH.exists():
        print(f"{YELLOW}WARN: DB not found at {DB_PATH}, skipping persistence{RESET}")
        return
    payload = json.dumps(
        {
            "run_status": result.run_status,
            "requested_symbols": result.requested_symbols,
            "completed_symbols": result.completed_symbols,
            "filled_order_count": result.filled_order_count,
            "failed_symbol": result.failed_symbol,
            "final_open_position_count": result.final_open_position_count,
            "final_open_order_count": result.final_open_order_count,
            "error_summary": result.error_summary,
        },
        ensure_ascii=False,
    )
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(
            "INSERT OR REPLACE INTO agent_tasks "
            "(agent_task_id, agent_type, task_type, task_status, input_payload, output_payload, "
            "priority, attempt_history, provider_trace, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                task_id,
                "system",
                "testnet_acceptance",
                "completed",
                json.dumps(
                    {
                        "execute_external_orders": True,
                        "authorization_reason": authorization_reason,
                        "order_origin": "operator_authorized_testnet_acceptance",
                    },
                    ensure_ascii=False,
                ),
                payload,
                5,
                "[]",
                "{}",
                now_iso,
            ),
        )
        conn.commit()
        print(f"{GREEN}Persisted agent_task {task_id} → agent_tasks table{RESET}")
    finally:
        conn.close()

    if (
        result.run_status == "completed"
        and result.final_open_position_count == 0
        and result.final_open_order_count == 0
        and sorted(result.completed_symbols) == sorted(AUTO_SIMULATION_EXECUTION_SYMBOLS)
    ):
        os.environ["POSTGRES_URL"] = f"sqlite:///{DB_PATH.as_posix()}"
        from services.database import get_session_factory, reset_database_caches
        from services.execution.testnet_authorization import arm_validated_directional_run

        reset_database_caches()
        with get_session_factory()() as session:
            armed = arm_validated_directional_run(
                session,
                symbols=list(AUTO_SIMULATION_EXECUTION_SYMBOLS),
                verified_at=now_iso,
            )
        print(f"{GREEN}Armed validated directional paper runs ({armed} run(s)){RESET}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute-external-orders",
        action="store_true",
        help="Explicitly authorize bounded Binance Simulation open/close acceptance orders.",
    )
    parser.add_argument(
        "--reason",
        help="Operator-visible reason recorded for this externally mutating acceptance run.",
    )
    args = parser.parse_args(argv)
    if not args.execute_external_orders or not (args.reason or "").strip():
        print(
            json.dumps(
                {
                    "status": "external_order_authorization_required",
                    "required_flags": ["--execute-external-orders", "--reason"],
                },
                ensure_ascii=False,
            )
        )
        return 2
    if not settings.binance_use_testnet or settings.live_trading_enabled:
        print(json.dumps({"status": "blocked_safety_boundary"}))
        return 2
    gateway = BinanceUsdtPerpetualGateway(use_testnet=True)
    cleanup_result = testnet_account_cleanup(gateway)
    if not cleanup_result["skipped"]:
        print(
            json.dumps(
                {
                    "pre_acceptance_cleanup": "performed",
                    "cancelled_orders": cleanup_result["cancelled_orders"],
                    "closed_positions": cleanup_result["closed_positions"],
                },
                ensure_ascii=False,
            )
        )
    service = TestnetAcceptanceService(gateway=gateway)
    task_id = str(uuid.uuid4())
    now_iso = datetime.now(UTC).isoformat()
    result = service.run(
        TestnetAcceptanceRunRequest(
            idempotency_key=f"btc-eth-{uuid.uuid4().hex[:12]}",
            symbols=list(AUTO_SIMULATION_EXECUTION_SYMBOLS),
            max_notional_usdt=120,
        )
    )
    output = {
        "order_origin": "operator_authorized_testnet_acceptance",
        "authorization_reason": args.reason.strip(),
        "run_status": result.run_status,
        "requested_symbols": result.requested_symbols,
        "completed_count": len(result.completed_symbols),
        "completed_symbols": result.completed_symbols,
        "filled_order_count": result.filled_order_count,
        "failed_symbol": result.failed_symbol,
        "compensation_attempted": result.compensation_attempted,
        "final_open_position_count": result.final_open_position_count,
        "final_open_order_count": result.final_open_order_count,
        "symbols": [
            {
                "symbol": item.symbol,
                "status": item.run_status,
                "stage": item.final_stage,
                "compensation_succeeded": item.compensation_succeeded,
                "failure_class": item.failure_class,
            }
            for item in result.symbol_results
        ],
        "error_summary": result.error_summary,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))

    _persist_result(result, task_id, now_iso, authorization_reason=args.reason.strip())

    if result.run_status == "completed":
        print(f"\n{GREEN}PASS — run testnet blocker check next:{RESET}")
        print("  python -m scripts.check_execution_blockers")
        return 0
    print(f"\n{RED}FAILED — run_status={result.run_status}{RESET}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
