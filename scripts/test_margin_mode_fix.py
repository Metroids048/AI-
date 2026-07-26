"""测试 order_context._margin_mode 的修复是否工作

模拟 Binance gateway client 调用 _margin_mode，验证：
1. 当 isolated 字段不存在时，是否正确返回 "CROSS"
2. 而不是抛出 MarketRulesUnavailable
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.execution.order_context import OrderExecutionContextBuilder


class MockClient:
    """模拟 Binance client，position 数据没有 isolated 字段"""

    options = {}  # 没有 defaultMarginMode

    def fetch_positions(self, symbols):
        # 返回外部仓位，但 isolated 字段缺失（Testnet 数据格式问题）
        return [
            {
                "symbol": "BTC/USDT",
                "contracts": 0.4044,
                "side": "long",
                "info": {},  # info 里也没有 isolated
                # isolated 字段不存在
            }
        ]


print("=== 测试 _margin_mode 修复 ===")
client = MockClient()

try:
    result = OrderExecutionContextBuilder._margin_mode(client, "BTC/USDT")
    print(f"✅ 返回: {result}")
    if result == "CROSS":
        print("✅ 修复生效：默认返回 CROSS")
    else:
        print(f"⚠️  返回值不是预期的 CROSS: {result}")
except Exception as e:
    print(f"❌ 仍然抛出异常: {type(e).__name__}: {e}")
    print("修复未生效或有其他问题")
