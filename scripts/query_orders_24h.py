#!/usr/bin/env python3
"""查询过去24小时的订单执行记录"""

import sqlite3


def main():
    conn = sqlite3.connect(".local_paper_console.db")
    cursor = conn.cursor()

    query = """
    SELECT created_at, symbol, direction, execution_status, order_origin,
           gateway_order_id, gateway_status, rejection_reason
    FROM order_executions
    WHERE created_at >= datetime('now','-1 day')
    ORDER BY created_at DESC;
    """

    cursor.execute(query)
    rows = cursor.fetchall()

    print(f"\n=== 过去24小时订单执行记录 (共 {len(rows)} 条) ===\n")

    if not rows:
        print("❌ 没有找到任何订单记录！")
        print("\n这说明：Gatekeeper通过了10笔，但order_executions表里一条都没有。")
        print("结论：链路断在 Gatekeeper 通过之后到创建 order_executions 之间。")
    else:
        print("时间 | 币种 | 方向 | 执行状态 | 订单来源 | 网关订单ID | 网关状态 | 拒绝原因")
        print("-" * 120)
        for row in rows:
            print(" | ".join(str(x) if x is not None else "NULL" for x in row))

    conn.close()


if __name__ == "__main__":
    main()
