#!/usr/bin/env python3
"""验证 Gate 17 修复方案 A+C 的效果

运行此脚本检查改动后的 regime 分布和 RANGE ensemble eligible 占比。

使用方法:
    python scripts/verify_gate17_fix.py

预期效果:
    - RANGE regime 占比: 从 100% 降到 60-70%
    - RANGE ensemble eligible=0 占比: 从 76.2% 降到 < 30%
"""

import sqlite3


def main() -> None:
    conn = sqlite3.connect(".local_paper_console.db")
    cursor = conn.cursor()

    # 统计最近 2 小时的 regime 分布
    cursor.execute("""
        SELECT
            CASE
                WHEN correlation_matrix_ref LIKE '%''market_regime'': ''range''%' THEN 'range'
                WHEN correlation_matrix_ref LIKE '%''market_regime'': ''trend_up''%' THEN 'trend_up'
                WHEN correlation_matrix_ref LIKE '%''market_regime'': ''trend_down''%' THEN 'trend_down'
                WHEN correlation_matrix_ref LIKE '%''market_regime'': ''uncertain''%' THEN 'uncertain'
                ELSE 'other'
            END as regime,
            COUNT(*) as cnt
        FROM signal_ensembles
        WHERE created_at > datetime('now', '-2 hours')
        GROUP BY regime
    """)

    print("=" * 80)
    print("Gate 17 修复验证 - 最近2小时 regime 分布")
    print("=" * 80)
    results = cursor.fetchall()
    total = sum(cnt for _, cnt in results)

    if total == 0:
        print("\n⚠️  最近2小时没有新的 ensemble 记录，请稍后再运行此脚本。")
        conn.close()
        return

    print(f"\n总计: {total} 条 ensemble 记录\n")

    range_cnt = 0
    for regime, cnt in results:
        pct = cnt / total * 100 if total > 0 else 0
        print(f"  {regime:<15s} {cnt:>5d} ({pct:>5.1f}%)")
        if regime == "range":
            range_cnt = cnt
            range_pct = pct

    # 统计 RANGE ensemble 的 eligible_count 分布
    cursor.execute("""
        SELECT
            CASE
                WHEN correlation_matrix_ref LIKE '%''eligible_count'': 0%' THEN 'eligible=0'
                ELSE 'eligible>0'
            END as status,
            COUNT(*) as cnt
        FROM signal_ensembles
        WHERE created_at > datetime('now', '-2 hours')
        AND correlation_matrix_ref LIKE '%''market_regime'': ''range''%'
        GROUP BY status
    """)

    print("\n" + "=" * 80)
    print("RANGE ensemble eligible 分布")
    print("=" * 80)
    results2 = cursor.fetchall()
    total_range = sum(cnt for _, cnt in results2)

    if total_range == 0:
        print("\n⚠️  最近2小时没有 RANGE regime 的 ensemble 记录。")
    else:
        print(f"\nRANGE 总计: {total_range} 条记录\n")
        eligible_zero_cnt = 0
        for status, cnt in results2:
            pct = cnt / total_range * 100 if total_range > 0 else 0
            print(f"  {status:<15s} {cnt:>5d} ({pct:>5.1f}%)")
            if status == "eligible=0":
                eligible_zero_pct = pct
                eligible_zero_cnt = cnt

    # 验收判定
    print("\n" + "=" * 80)
    print("验收结果")
    print("=" * 80)

    if total == 0:
        print("\n❌ 数据不足，无法判定")
    else:
        range_pass = range_pct <= 70.0
        eligible_pass = total_range == 0 or (eligible_zero_cnt > 0 and eligible_zero_pct < 30.0)

        print(f"\n1. RANGE regime 占比: {range_pct:.1f}%")
        print("   目标: ≤ 70%")
        print(f"   结果: {'✅ PASS' if range_pass else '❌ FAIL'}")

        if total_range > 0:
            print(f"\n2. RANGE ensemble eligible=0 占比: {eligible_zero_pct:.1f}%")
            print("   目标: < 30%")
            print(f"   结果: {'✅ PASS' if eligible_pass else '❌ FAIL'}")

        if range_pass and eligible_pass:
            print("\n🎉 修复方案 A+C 验收通过！")
        else:
            print("\n⚠️  部分指标未达标，可能需要进一步调整。")

    conn.close()


if __name__ == "__main__":
    main()
