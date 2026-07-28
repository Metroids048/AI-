"""Wait for the next 15m close and print Sampling projection + scheduler evidence."""

from __future__ import annotations

import json
import sqlite3
import time
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "logs" / "scheduler-state.json"
DB = ROOT / ".local_paper_console.db"


def _next_close(now: datetime) -> datetime:
    floored = now.replace(second=0, microsecond=0) - timedelta(minutes=now.minute % 15)
    return floored + timedelta(minutes=15)


def _fetch_metrics(symbol: str) -> dict:
    url = f"https://testnet.binancefuture.com/fapi/v1/klines?symbol={symbol}&interval=15m&limit=80"
    with urllib.request.urlopen(url, timeout=20) as response:
        raw = json.loads(response.read().decode())
    rows = [
        {
            "ts": datetime.fromtimestamp(item[0] / 1000, tz=UTC),
            "close": float(item[4]),
            "closed": item[6] < datetime.now(UTC).timestamp() * 1000,
        }
        for item in raw
    ]
    closed = [row for row in rows if row["closed"]]
    close = pd.Series([row["close"] for row in closed])
    ema50 = float(close.ewm(span=50, adjust=False).mean().iloc[-1])
    macd_line = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    macd = float((macd_line - macd_line.ewm(span=9, adjust=False).mean()).iloc[-1])
    delta = close.diff()
    average_gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    average_loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    rsi = float((100 - (100 / (1 + average_gain / average_loss.replace(0, float("nan"))))).iloc[-1])
    latest = float(close.iloc[-1])
    return {
        "bar": closed[-1]["ts"].isoformat(),
        "close": round(latest, 4),
        "ema50": round(ema50, 4),
        "macd": round(macd, 4),
        "rsi": round(rsi, 4),
        "SHORT": latest < ema50 and macd < 0 and 28 <= rsi <= 50,
        "LONG": latest > ema50 and macd > 0 and 50 <= rsi <= 72,
    }


def _observe_db() -> None:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    print("exchange_orders", conn.execute("SELECT COUNT(*) FROM exchange_orders").fetchone()[0])
    print("fill_receipts", conn.execute("SELECT COUNT(*) FROM exchange_fill_receipts").fetchone()[0])
    rows = conn.execute(
        """
        SELECT symbol, reason_code, status, bar_time, created_at
        FROM decision_funnel_terminals
        WHERE paper_run_id='78ba69a7-2bfb-457e-9a97-934aaf418e00'
        ORDER BY created_at DESC LIMIT 6
        """
    ).fetchall()
    for row in rows:
        print("funnel", dict(row))
    conn.close()


def main() -> None:
    now = datetime.now(UTC)
    target = _next_close(now) + timedelta(seconds=75)
    print(f"utc_now={now.isoformat()} wait_until={target.isoformat()}")
    while datetime.now(UTC) < target:
        time.sleep(5)
    print("=== post-close probe ===")
    print("BTC", _fetch_metrics("BTCUSDT"))
    print("ETH", _fetch_metrics("ETHUSDT"))
    # give scheduler time to finish cycle
    time.sleep(90)
    if STATE.exists():
        state = json.loads(STATE.read_text(encoding="utf-8"))
        paper = (state.get("task_last_results") or {}).get("paper_runtime_cycle") or {}
        print("scheduler_last_auto", state.get("last_auto_cycle_at"))
        print("paper_runtime", json.dumps(paper, ensure_ascii=False)[:2000])
    _observe_db()


if __name__ == "__main__":
    main()
