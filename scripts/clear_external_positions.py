"""清理 UNMANAGED_EXTERNAL_POSITION 记录，解除 entry 阻塞

这些记录是系统切换 execution_mode 后遗留的旧记录，
导致 V2 reconciliation 认为有外部持仓而阻止新开仓。
"""

import sqlite3
import sys


def clear_external_positions(db_path: str, dry_run: bool = True):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 1. 找出所有 UNMANAGED_EXTERNAL_POSITION 记录
    cursor.execute("""
        SELECT position_record_id, symbol, position_side, quantity,
               exchange_account, management_status, opened_at
        FROM position_records
        WHERE management_status = 'UNMANAGED_EXTERNAL_POSITION'
    """)
    external_positions = cursor.fetchall()

    if not external_positions:
        print("✅ 没有找到 UNMANAGED_EXTERNAL_POSITION 记录")
        conn.close()
        return

    print(f"\n找到 {len(external_positions)} 个外部持仓记录:\n")
    for pos in external_positions:
        print(f"  {pos[1]} {pos[2]} qty={pos[3]:.4f} account={pos[4]} time={pos[6]}")

    if dry_run:
        print(f"\n🔍 [DRY RUN] 将删除这 {len(external_positions)} 条记录")
        print("   运行时添加 --execute 参数以真正执行删除")
        conn.close()
        return

    # 2. 删除这些记录
    cursor.execute("""
        DELETE FROM position_records
        WHERE management_status = 'UNMANAGED_EXTERNAL_POSITION'
    """)
    deleted = cursor.rowcount
    conn.commit()

    print(f"\n✅ 已删除 {deleted} 条 UNMANAGED_EXTERNAL_POSITION 记录")
    print("   系统应该在下一个周期自动解除 entry 阻塞")

    conn.close()


if __name__ == "__main__":
    db_path = ".local_paper_console.db"
    dry_run = "--execute" not in sys.argv

    if dry_run:
        print("=" * 80)
        print("清理外部持仓记录（预览模式）")
        print("=" * 80)
    else:
        print("=" * 80)
        print("清理外部持仓记录（执行模式）")
        print("=" * 80)
        print("\n⚠️  警告：将真正删除数据库记录\n")

    clear_external_positions(db_path, dry_run)
