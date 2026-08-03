"""查询最近的信号决策事件，找出为什么没有开单。

用法：
    py -3 -m scripts.query_signal_rejections
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path


def _find_db() -> Path | None:
    root = Path(__file__).resolve().parent.parent
    candidates = [
        root / ".local_paper_console.db",
        root / "paper_console.db",
        root / "local_paper_console.db",
    ]
    for c in candidates:
        if c.exists():
            return c
    # fallback: any *.db in root
    for p in sorted(root.glob("*.db")):
        return p
    return None


def main() -> None:
    db_path = _find_db()
    if db_path is None:
        print("找不到数据库文件 (*.db)")
        return
    print(f"数据库: {db_path}\n")
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row

    # 确认表存在与列
    cols = {r[1] for r in con.execute("PRAGMA table_info(decision_events)").fetchall()}
    if not cols:
        print("decision_events 表不存在")
    else:
        print("=== 最近 25 条决策事件 (decision_events) ===")
        rows = con.execute(
            """
            SELECT symbol, timeframe, event_type, block_code,
                   candle_close_time, payload, created_at
            FROM decision_events
            ORDER BY created_at DESC
            LIMIT 25
            """
        ).fetchall()
        if not rows:
            print("  (无记录)")
        for r in rows:
            reason = ""
            try:
                p = json.loads(r["payload"]) if r["payload"] else {}
                reason = p.get("decision_reason") or p.get("reason") or p.get("block_reason") or ""
                # 抓管道里各门槛的通过情况
                pipe = p.get("decision_pipeline") or {}
                if pipe and not reason:
                    reason = json.dumps(pipe, ensure_ascii=False)[:300]
            except Exception as exc:  # noqa: BLE001
                reason = f"(payload解析失败: {exc})"
            print(
                f"  {r['created_at']} | {r['symbol']:<10} {r['timeframe']:<4} "
                f"| {r['event_type']:<24} | block={r['block_code']}"
            )
            if reason:
                print(f"       └─ {reason}")

    # 事件类型统计
    print("\n=== event_type / block_code 分布（最近500条）===")
    stats = con.execute(
        """
        SELECT event_type, block_code, COUNT(*) AS n
        FROM (SELECT event_type, block_code FROM decision_events
              ORDER BY created_at DESC LIMIT 500)
        GROUP BY event_type, block_code
        ORDER BY n DESC
        """
    ).fetchall()
    for r in stats:
        print(f"  {r['n']:>4}x  {r['event_type']:<26} block={r['block_code']}")

    con.close()


if __name__ == "__main__":
    main()
