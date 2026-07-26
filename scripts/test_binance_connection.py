"""手动测试市场数据获取（无依赖版本）

直接调用 Binance API 获取最新K线，验证：
1. Binance Testnet 是否在线
2. 数据是否能正常获取
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import UTC, datetime

import ccxt

print("=== 测试 Binance Testnet 连接 ===\n")

# 从环境变量或直接使用空凭证（公开数据不需要凭证）
import os

api_key = os.getenv("BINANCE_API_KEY", "")
api_secret = os.getenv("BINANCE_API_SECRET", "")

# 创建testnet client（获取市场数据不需要凭证）
exchange = ccxt.binance(
    {
        "apiKey": api_key,
        "secret": api_secret,
        "options": {"defaultType": "future"},
        "enableRateLimit": True,
    }
)
exchange.set_sandbox_mode(True)

try:
    print("1. 获取最新1分钟K线...")
    for symbol in ["BTC/USDT", "ETH/USDT"]:
        try:
            candles = exchange.fetch_ohlcv(symbol, "1m", limit=1)
            if candles:
                latest = candles[-1]
                ts = datetime.fromtimestamp(latest[0] / 1000, tz=UTC)
                now = datetime.now(UTC)
                delay = (now - ts).total_seconds()
                print(f"   {symbol:12} 最新K线: {ts.isoformat()} (延迟 {delay:.0f}秒)")
            else:
                print(f"   {symbol:12} ❌ 无数据")
        except Exception as e:
            print(f"   {symbol:12} ❌ 错误: {e}")

    print("\n2. 获取最新15分钟K线...")
    for symbol in ["BTC/USDT", "ETH/USDT"]:
        try:
            candles = exchange.fetch_ohlcv(symbol, "15m", limit=2)
            if candles:
                for c in candles:
                    ts = datetime.fromtimestamp(c[0] / 1000, tz=UTC)
                    close = c[4]
                    closed = c[0] + 15 * 60 * 1000 <= datetime.now(UTC).timestamp() * 1000
                    print(f"   {symbol:12} {ts.isoformat()}, close={close:.2f}, closed={closed}")
            else:
                print(f"   {symbol:12} ❌ 无数据")
        except Exception as e:
            print(f"   {symbol:12} ❌ 错误: {e}")

    print(f"\n当前UTC时间: {datetime.now(UTC).isoformat()}")

except Exception as e:
    print(f"\n❌ 错误: {type(e).__name__}: {e}")
    import traceback

    traceback.print_exc()
