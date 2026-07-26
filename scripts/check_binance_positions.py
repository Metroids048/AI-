#!/usr/bin/env python3
"""直接查询Binance testnet获取实际持仓"""

import os
import sys

sys.path.insert(0, os.path.abspath("."))

import ccxt

from shared.binance_network import binance_ccxt_config


def main():
    print("\n=== 查询Binance Testnet实际持仓 ===\n")

    try:
        # 直接使用ccxt
        config = binance_ccxt_config()
        client = ccxt.binance(config)
        client.load_markets()

        # 获取账户信息
        balance = client.fetch_balance()

        print(f"总钱包余额: {balance.get('USDT', {}).get('total', 'N/A')}")
        print(f"可用余额: {balance.get('USDT', {}).get('free', 'N/A')}")

        # 获取持仓 - 使用fapiPrivateV2GetAccount
        account_info = client.fapiPrivateV2GetAccount()

        total_wallet = account_info.get("totalWalletBalance", "N/A")
        total_unrealized = account_info.get("totalUnrealizedProfit", "N/A")

        print(f"总钱包余额(合约): {total_wallet}")
        print(f"未实现盈亏: {total_unrealized}")

        # 获取持仓
        positions = account_info.get("positions", [])
        active_positions = [p for p in positions if float(p.get("positionAmt", 0)) != 0]

        print(f"\n活跃持仓数量: {len(active_positions)}")

        if active_positions:
            print("\n持仓详情:")
            for pos in active_positions:
                symbol = pos.get("symbol")
                amt = float(pos.get("positionAmt", 0))
                entry_price = float(pos.get("entryPrice", 0))
                mark_price = float(pos.get("markPrice", 0))
                unrealized = float(pos.get("unRealizedProfit", 0))
                side = pos.get("positionSide", "BOTH")
                leverage = pos.get("leverage", "N/A")

                direction = "多单" if amt > 0 else "空单"
                print(f"\n  {symbol} | {side} | {direction} | 杠杆:{leverage}x")
                print(f"    数量: {abs(amt)}")
                print(f"    入场价: {entry_price}")
                print(f"    标记价: {mark_price}")
                print(f"    未实现盈亏: {unrealized:.4f} USDT")
        else:
            print("\n✅ Binance上没有活跃持仓")

        print("\n" + "=" * 80)
        print("💡 诊断：")
        print("  如果Binance上有持仓但本地数据库中找不到对应的position_record，")
        print("  系统会将其标记为UNMANAGED_EXTERNAL_POSITION，阻塞正常的入场决策。")
        print("  解决方案：手动平掉Binance上的未托管持仓，或者标记为已托管。")
        print("=" * 80)

    except Exception as e:
        print(f"\n❌ 查询失败: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    main()
