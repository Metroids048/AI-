"""紧急对账脚本：清理数据库中与交易所不匹配的鬼持仓

使用场景：
- 数据库显示 N 个持仓，但交易所只有 M 个（M < N）
- 手动平仓后未同步
- 程序崩溃导致本地状态脱节

执行效果：
1. 查询币安 Testnet 实际持仓
2. 对比本地 position_records (management_status='MANAGED_STRATEGY')
3. 将交易所不存在的持仓标记为 RECONCILED_GHOST
4. 取消对应的保护单记录（status='CANCELLED_GHOST_POSITION'）
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy.orm import Session

from services.database import get_engine
from services.execution.gateway import BinanceUsdtPerpetualGateway
from shared.config import settings
from shared.models import PositionManagementStatus


def reconcile_ghost_positions(*, dry_run: bool = True) -> dict[str, int]:
    """强制对账并清理鬼持仓

    Args:
        dry_run: True=只检查不修改，False=实际执行清理

    Returns:
        {"ghost_positions": N, "cancelled_protections": M}
    """
    if not settings.binance_use_testnet:
        raise ValueError("❌ 安全检查失败：仅允许在 Testnet 环境运行")

    engine = get_engine()
    gateway = BinanceUsdtPerpetualGateway(use_testnet=True)

    # 1. 查询交易所实际持仓
    exchange_snapshot = gateway.reconcile(live_run_id="emergency-reconcile:ghost-cleanup")
    exchange_positions = {
        item["symbol"].replace("USDT", "/USDT"): {
            "side": item.get("side", "").lower(),
            "quantity": abs(float(item.get("contracts", 0))),
        }
        for item in exchange_snapshot.get("open_positions", [])
        if abs(float(item.get("contracts", 0))) > 0
    }

    print(f"\n{'=' * 60}")
    print(f"📊 交易所实际持仓：{len(exchange_positions)} 个")
    for symbol, pos in exchange_positions.items():
        print(f"  {symbol:12} {pos['side']:5} qty={pos['quantity']:.3f}")

    # 2. 查询本地 MANAGED_STRATEGY 持仓
    with Session(engine) as session:
        from shared.models import PositionRecord, ProtectionRecord

        local_positions = (
            session.query(PositionRecord)
            .filter(PositionRecord.management_status == PositionManagementStatus.MANAGED_STRATEGY)
            .all()
        )

        print(f"\n📁 本地 MANAGED_STRATEGY 持仓：{len(local_positions)} 个")

        # 3. 识别鬼持仓
        ghost_positions = []
        for pos in local_positions:
            symbol = pos.symbol
            side = pos.position_side.lower()

            if symbol not in exchange_positions:
                ghost_positions.append((pos, "symbol_not_exist"))
            elif exchange_positions[symbol]["side"] != side:
                ghost_positions.append((pos, "side_mismatch"))
            else:
                # 数量匹配检查（允许 0.1% 误差）
                local_qty = pos.quantity
                exchange_qty = exchange_positions[symbol]["quantity"]
                if abs(local_qty - exchange_qty) / max(local_qty, exchange_qty) > 0.001:
                    ghost_positions.append((pos, "quantity_mismatch"))

        print(f"\n👻 发现鬼持仓：{len(ghost_positions)} 个")
        for pos, reason in ghost_positions:
            print(f"  {pos.symbol:12} {pos.position_side:5} qty={pos.quantity:.3f} [{reason}]")

        if not ghost_positions:
            print("\n✅ 没有鬼持仓，数据库与交易所同步")
            return {"ghost_positions": 0, "cancelled_protections": 0}

        if dry_run:
            print("\n⚠️  DRY RUN 模式：不执行实际清理")
            print("   如需执行清理，运行：python scripts/emergency_reconcile_positions.py --execute")
            return {"ghost_positions": len(ghost_positions), "cancelled_protections": 0}

        # 4. 执行清理
        print("\n🔧 开始清理...")
        cancelled_protections = 0

        for pos, reason in ghost_positions:
            # 标记持仓为 RECONCILED_GHOST
            pos.management_status = PositionManagementStatus.RECONCILED_GHOST
            pos.updated_at = datetime.now(UTC)

            # 取消对应的保护单
            protections = (
                session.query(ProtectionRecord)
                .filter(
                    ProtectionRecord.position_record_id == pos.position_record_id, ProtectionRecord.status == "ACTIVE"
                )
                .all()
            )

            for prot in protections:
                prot.status = "CANCELLED_GHOST_POSITION"
                prot.updated_at = datetime.now(UTC)
                cancelled_protections += 1

            print(f"  ✓ {pos.symbol} {pos.position_side} → RECONCILED_GHOST (取消 {len(protections)} 个保护单)")

        session.commit()

        print("\n✅ 清理完成：")
        print(f"   - 清理鬼持仓：{len(ghost_positions)} 个")
        print(f"   - 取消保护单：{cancelled_protections} 个")

        return {"ghost_positions": len(ghost_positions), "cancelled_protections": cancelled_protections}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="清理鬼持仓")
    parser.add_argument("--execute", action="store_true", help="执行实际清理（默认为 dry-run）")
    args = parser.parse_args()

    result = reconcile_ghost_positions(dry_run=not args.execute)

    if result["ghost_positions"] > 0 and not args.execute:
        print("\n" + "=" * 60)
        print(f"⚠️  检测到 {result['ghost_positions']} 个鬼持仓")
        print("⚠️  请运行以下命令执行清理：")
        print("   python scripts/emergency_reconcile_positions.py --execute")
        sys.exit(1)
