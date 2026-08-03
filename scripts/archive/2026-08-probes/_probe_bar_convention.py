import sqlite3
from datetime import UTC, datetime

conn = sqlite3.connect(".local_paper_console.db")
conn.row_factory = sqlite3.Row
rows = conn.execute(
    """
  SELECT timestamp, open, high, low, close
  FROM ohlcv_bars WHERE symbol='BTC/USDT' AND timeframe='15m'
  ORDER BY timestamp DESC LIMIT 6
  """
).fetchall()
now = datetime.now(UTC)
print("now", now.isoformat())
for r in rows:
    ts = r["timestamp"]
    # sqlite may return string
    print(dict(r), "age_min_if_open", None)
# compare to exchange last closed
import json
import urllib.request

raw = json.loads(
    urllib.request.urlopen(
        "https://testnet.binancefuture.com/fapi/v1/klines?symbol=BTCUSDT&interval=15m&limit=5", timeout=20
    ).read()
)
for i in raw:
    open_ts = datetime.fromtimestamp(i[0] / 1000, tz=UTC)
    close_ts = datetime.fromtimestamp(i[6] / 1000, tz=UTC)
    print(
        "exch open",
        open_ts.isoformat(),
        "close",
        close_ts.isoformat(),
        "closed",
        i[6] < now.timestamp() * 1000,
        "close_px",
        i[4],
    )
conn.close()
