"""激活 allow_entry_with_unmanaged_positions 标志

允许在 Binance 账户有外部仓位时仍然评估入场信号。
用法: py -3 -m scripts.enable_entry_with_external_positions
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / ".local_paper_console.db"
TARGET_KEY = "auto_paper_mature_templates"

GREEN = "\033[92m"
YELLOW = "\033[93m"
RESET = "\033[0m"


def main() -> int:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        "SELECT paper_run_id, execution_profile FROM paper_runs WHERE paper_status = 'running' ORDER BY created_at DESC"
    ).fetchall()

    updated = 0
    for row in rows:
        try:
            profile = json.loads(row["execution_profile"] or "{}")
        except Exception:
            continue

        if profile.get("auto_paper_runtime_key") != TARGET_KEY:
            continue

        if profile.get("allow_entry_with_unmanaged_positions"):
            print(f"  {GREEN}已启用:{RESET} run={row['paper_run_id'][:12]} flag 已存在")
            continue

        profile["allow_entry_with_unmanaged_positions"] = True
        conn.execute(
            "UPDATE paper_runs SET execution_profile = ? WHERE paper_run_id = ?",
            (json.dumps(profile), row["paper_run_id"]),
        )
        print(f"  {GREEN}已设置:{RESET} run={row['paper_run_id'][:12]} allow_entry_with_unmanaged_positions=True")
        updated += 1

    conn.commit()
    conn.close()

    if updated == 0:
        print(f"  {YELLOW}无需变更或未找到目标 run{RESET}")
    else:
        print(f"\n{GREEN}完成，已更新 {updated} 条记录。{RESET}")
        print(f'{YELLOW}⚠️  重启系统后生效：cmd.exe /d /s /c "一键启动.cmd < nul"{RESET}')

    return 0


if __name__ == "__main__":
    sys.exit(main())
