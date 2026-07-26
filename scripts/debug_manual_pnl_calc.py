#!/usr/bin/env python3
"""调试manual_pnl的实际计算过程，逐步打印中间结果"""

import sys

sys.path.insert(0, ".")

from services.data.universe import exchange_to_platform_symbol
from services.database import get_session_factory
from services.execution.gateway import configured_gateways
from services.strategy_library import ExecutionRepository
from shared.models import TradeSide

session = get_session_factory()()
execution_repo = ExecutionRepository(session)

gateways = configured_gateways()
gateway = gateways[0] if gateways else None

run_id = "35298c65-cdbe-4bee-bee3-b7ded07c3204"

print(f"\ngateway: {gateway}")
print(f"gateway type: {type(gateway).__name__ if gateway else 'None'}")

if gateway is not None:
    snapshot = gateway.reconcile(live_run_id=f"paper-testnet:{run_id}")
    open_positions = snapshot.get("open_positions", [])
    print(f"\n交易所返回的持仓数量: {len(open_positions)}")

    capability = getattr(gateway, "capability", None)
    exchange_name = str(getattr(capability, "exchange", None) or "paper")
    market_type = str(getattr(capability, "market_type", None) or "paper")
    backend = str(getattr(gateway, "api_backend", None) or "local")
    exchange_account = f"{exchange_name}:{market_type}:{backend}"
    print(f"计算出的exchange_account: {exchange_account!r}")

    manual_pnl = 0.0

    for pos in open_positions:
        raw_symbol = str(pos.get("symbol") or "")
        symbol = exchange_to_platform_symbol(raw_symbol) or raw_symbol
        side_str = str(pos.get("side") or "").lower()
        side = TradeSide.SHORT if side_str == "short" else TradeSide.LONG
        unrealized = float(pos.get("unrealized_pnl") or 0.0)

        print(f"\n持仓: raw_symbol={raw_symbol!r} -> platform_symbol={symbol!r}")
        print(f"  side_str={side_str!r} -> side={side}")
        print(f"  unrealized_pnl={unrealized}")

        record = execution_repo.find_unmanaged_position_record(
            exchange_account=exchange_account, symbol=symbol, position_side=side, run_id=run_id
        )

        if record is not None:
            print(f"  ✅ 匹配到未托管持仓记录: {record.position_record_id[:12]}...")
            manual_pnl += unrealized
        else:
            print("  ❌ 未匹配到记录（这是策略自己的持仓，或匹配条件不对）")

    print(f"\n最终计算的manual_pnl总和: {manual_pnl}")
else:
    print("❌ 没有配置的gateway")

session.close()
