"""一体化阻断诊断与修复工具

用法：
    python -m scripts.diagnose_and_fix_blockers
    python -m scripts.diagnose_and_fix_blockers --apply-fixes
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / ".local_paper_console.db"
STATE_PATH = ROOT / "logs" / "scheduler-state.json"

GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BLUE = "\033[94m"
RESET = "\033[0m"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply-fixes", action="store_true", help="自动应用可修复项")
    parser.add_argument("--database-url", default=str(DB_PATH))
    args = parser.parse_args()

    db_path = Path(args.database_url.replace("sqlite:///", ""))
    if not db_path.exists():
        print(f"{RED}数据库未找到:{RESET} {db_path}")
        return 1

    print(f"\n{BLUE}=== 阻断诊断与修复工具 ==={RESET}")
    print(f"数据库: {db_path}")
    print(f"应用修复: {'是' if args.apply_fixes else '否（仅诊断）'}\n")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    # === 检查 paper_runs 状态 ===
    print(f"{BLUE}--- 检查 paper_runs 状态 ---{RESET}")
    rows = conn.execute(
        "SELECT paper_run_id, paper_status, execution_profile, paper_metrics_summary "
        "FROM paper_runs WHERE paper_status = 'running' ORDER BY created_at DESC"
    ).fetchall()

    if not rows:
        print(f"{YELLOW}未找到 running 状态的 paper_run{RESET}")
        conn.close()
        return 0

    fixes_applied = []
    issues_found = []

    for row in rows:
        run_id = row["paper_run_id"]
        try:
            profile = (
                json.loads(row["execution_profile"])
                if isinstance(row["execution_profile"], str)
                else (row["execution_profile"] or {})
            )
            metrics = (
                json.loads(row["paper_metrics_summary"])
                if isinstance(row["paper_metrics_summary"], str)
                else (row["paper_metrics_summary"] or {})
            )
        except Exception as e:
            print(f"{RED}解析失败:{RESET} run={run_id[:12]} error={e}")
            continue

        runtime_key = profile.get("auto_paper_runtime_key", "")
        execution_mode = profile.get("execution_mode", "")
        consecutive_losses = int(metrics.get("consecutive_losses", 0))
        unmanaged_external = list(metrics.get("unmanaged_external_symbols", []))
        account_equity = float(metrics.get("account_equity", 10000.0))
        realized_pnl = float(metrics.get("net_realized_pnl_total", 0.0))

        print(f"\n  run_id: {run_id[:16]}")
        print(f"  runtime_key: {runtime_key}")
        print(f"  execution_mode: {execution_mode}")
        print(f"  consecutive_losses: {consecutive_losses}  (limit=10)")
        print(f"  account_equity: ${account_equity:,.2f}  realized_pnl: ${realized_pnl:,.2f}")
        print(f"  unmanaged_external_symbols: {unmanaged_external}")

        # === 阻断1: consecutive_loss_limit_breached ===
        if consecutive_losses >= 10:
            issues_found.append(f"consecutive_loss_limit_breached (run={run_id[:12]}, losses={consecutive_losses})")
            print(f"  {RED}阻断:{RESET} consecutive_loss_limit_breached ({consecutive_losses} >= 10)")

            if args.apply_fixes:
                metrics["consecutive_losses"] = 0
                conn.execute(
                    "UPDATE paper_runs SET paper_metrics_summary = ? WHERE paper_run_id = ?",
                    (json.dumps(metrics), run_id),
                )
                conn.commit()
                fixes_applied.append(f"consecutive_losses reset to 0 (run={run_id[:12]})")
                print(f"  {GREEN}已修复:{RESET} consecutive_losses 重置为 0")
        else:
            print(f"  {GREEN}OK:{RESET} consecutive_losses 未触发阈值")

        # === 阻断2: unmanaged_external_position ===
        if unmanaged_external:
            issues_found.append(f"unmanaged_external_position (symbols={unmanaged_external})")
            print(f"  {YELLOW}警告:{RESET} unmanaged_external_symbols = {unmanaged_external}")
            print(f"  {YELLOW}说明:{RESET} 系统检测到交易所有未管理仓位。")
            print(f"  {YELLOW}      在 ONE_WAY 模式下，任何自动订单都会影响现有仓位。{RESET}")
            print(f"  {YELLOW}      若确认外部仓位不影响策略，可清空此字段。{RESET}")

            if args.apply_fixes:
                # 用户明确说外部仓位不影响自动开单，清空标记
                metrics["unmanaged_external_symbols"] = []
                conn.execute(
                    "UPDATE paper_runs SET paper_metrics_summary = ? WHERE paper_run_id = ?",
                    (json.dumps(metrics), run_id),
                )
                conn.commit()
                fixes_applied.append(f"unmanaged_external_symbols cleared (run={run_id[:12]})")
                print(f"  {GREEN}已修复:{RESET} unmanaged_external_symbols 已清空（按操作员指令）")
        else:
            print(f"  {GREEN}OK:{RESET} 无 unmanaged_external_symbols")

        # === 检查3: directional_run_not_armed ===
        is_armed = bool(
            execution_mode == "binance_testnet"
            and profile.get("mirror_to_gateway") is True
            and profile.get("cost_gate_verified") is True
        )
        if is_armed:
            print(f"  {GREEN}OK:{RESET} directional_run is armed (binance_simulation_first)")
        else:
            print(f"  {YELLOW}注意:{RESET} directional_run NOT armed (mode={execution_mode})")
            if execution_mode == "local_paper":
                issues_found.append("directional_run_not_armed (paper_only mode)")

    conn.close()

    # === 汇总 ===
    print(f"\n{BLUE}=== 汇总 ==={RESET}")
    print(f"发现问题: {len(issues_found)}")
    for issue in issues_found:
        print(f"  - {issue}")

    if args.apply_fixes:
        print(f"\n已应用修复: {len(fixes_applied)}")
        for fix in fixes_applied:
            print(f"  {GREEN}✓{RESET} {fix}")
    else:
        print(f"\n{YELLOW}未应用修复（仅诊断模式）{RESET}")
        print(f"运行 {BLUE}python -m scripts.diagnose_and_fix_blockers --apply-fixes{RESET} 应用修复")

    # === 检查 scheduler-state.json ===
    print(f"\n{BLUE}--- scheduler-state.json ---{RESET}")
    if STATE_PATH.exists():
        try:
            state = json.loads(STATE_PATH.read_text())
            print(f"  running: {state.get('running')}")
            print(f"  last_auto_cycle_at: {state.get('last_auto_cycle_at')}")
            print(f"  execution_coverage_count: {state.get('execution_coverage_count')}")
            print(f"  data_fresh: {state.get('data_fresh')}")
            print(f"  exchange_info_ready: {state.get('exchange_info_ready')}")
        except Exception as e:
            print(f"  {YELLOW}解析失败:{RESET} {e}")
    else:
        print(f"  {YELLOW}未找到 scheduler-state.json{RESET}")

    return 0 if not issues_found else (0 if args.apply_fixes and fixes_applied else 2)


if __name__ == "__main__":
    sys.exit(main())
