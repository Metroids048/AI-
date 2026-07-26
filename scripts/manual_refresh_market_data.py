"""手动触发市场数据刷新

模拟 market_data_heartbeat 任务的逻辑：
1. 从 Binance 获取最新K线
2. 写入 ohlcv_bars 表
3. 验证写入成功
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.data.binance import BinanceCcxtClient
from services.data.repository import DataRepository
from services.database import get_session_factory

print("=== 手动刷新市场数据 ===\n")

# 创建 Binance client
client = BinanceCcxtClient()

# 创建 DB session
SessionLocal = get_session_factory()
session = SessionLocal()
repo = DataRepository(session)

try:
    for symbol in ["BTC/USDT", "ETH/USDT"]:
        print(f"\n{symbol}:")

        # 1. 从 Binance 获取
        print("  1. 从 Binance 获取最新60根15m K线...")
        bars = client.fetch_recent_usdm_ohlcv(symbol=symbol, timeframe="15m", limit=60)
        if bars:
            latest = bars[-1]
            print(f"     获取成功: {len(bars)}根, 最新={latest.timestamp.isoformat()}, close={latest.close}")
        else:
            print("     ❌ 未获取到数据")
            continue

        # 2. 写入 DB
        print("  2. 写入数据库...")
        count = repo.store_ohlcv_bars(bars)
        print(f"     写入 {count} 条记录")

        # 3. 验证
        print("  3. 验证写入...")
        latest_in_db = repo.get_latest_ohlcv_bar(symbol=symbol, timeframe="15m")
        if latest_in_db:
            print(f"     ✅ DB最新: {latest_in_db.timestamp.isoformat()}, close={latest_in_db.close}")
        else:
            print("     ❌ DB中无数据")

    session.commit()
    print("\n✅ 刷新完成")

except Exception as e:
    print(f"\n❌ 错误: {type(e).__name__}: {e}")
    import traceback

    traceback.print_exc()
    session.rollback()
finally:
    session.close()
