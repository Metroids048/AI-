"""Watch until Sampling aligns or a natural exchange order appears."""

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
MAX_HOURS = 3


def _metrics(symbol: str) -> dict:
    url = f"https://testnet.binancefuture.com/fapi/v1/klines?symbol={symbol}&interval=15m&limit=80"
    with urllib.request.urlopen(url, timeout=20) as response:
        raw = json.loads(response.read().decode())
    closed = [item for item in raw if item[6] < datetime.now(UTC).timestamp() * 1000]
    close = pd.Series([float(item[4]) for item in closed])
    ema50 = float(close.ewm(span=50, adjust=False).mean().iloc[-1])
    macd_line = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    macd = float((macd_line - macd_line.ewm(span=9, adjust=False).mean()).iloc[-1])
    delta = close.diff()
    average_gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    average_loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    rsi = float((100 - (100 / (1 + average_gain / average_loss.replace(0, float("nan"))))).iloc[-1])
    latest = float(close.iloc[-1])
    return {
        "bar": datetime.fromtimestamp(closed[-1][0] / 1000, tz=UTC).isoformat(),
        "close": round(latest, 4),
        "macd": round(macd, 4),
        "rsi": round(rsi, 4),
        "SHORT": latest < ema50 and macd < 0 and 28 <= rsi <= 50,
        "LONG": latest > ema50 and macd > 0 and 50 <= rsi <= 72,
    }


def _db_snapshot() -> dict:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    orders = conn.execute("SELECT COUNT(*) AS n FROM exchange_orders").fetchone()["n"]
    fills = conn.execute("SELECT COUNT(*) AS n FROM exchange_fill_receipts").fetchone()["n"]
    funnel = conn.execute(
        """
        SELECT symbol, reason_code, bar_time, created_at
        FROM decision_funnel_terminals
        WHERE paper_run_id='78ba69a7-2bfb-457e-9a97-934aaf418e00'
        ORDER BY created_at DESC LIMIT 4
        """
    ).fetchall()
    conn.close()
    return {"orders": orders, "fills": fills, "funnel": [dict(row) for row in funnel]}


def main() -> None:
    deadline = datetime.now(UTC) + timedelta(hours=MAX_HOURS)
    last_bar = None
    while datetime.now(UTC) < deadline:
        now = datetime.now(UTC)
        btc = _metrics("BTCUSDT")
        eth = _metrics("ETHUSDT")
        snap = _db_snapshot()
        state = {}
        if STATE.exists():
            state = json.loads(STATE.read_text(encoding="utf-8-sig"))
        paper = (state.get("task_last_results") or {}).get("paper_runtime_cycle") or {}
        print(
            json.dumps(
                {
                    "utc": now.isoformat(),
                    "btc": btc,
                    "eth": eth,
                    "orders": snap["orders"],
                    "fills": snap["fills"],
                    "funnel": snap["funnel"][:2],
                    "last_auto": state.get("last_auto_cycle_at"),
                    "paper_runs": paper.get("paper_runs"),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        if snap["orders"] > 0 or snap["fills"] > 0:
            print("NATURAL_EXCHANGE_EVIDENCE", flush=True)
            return
        if (btc["SHORT"] or btc["LONG"] or eth["SHORT"] or eth["LONG"]) and last_bar != btc["bar"]:
            print("SAMPLING_ALIGN_CANDIDATE", flush=True)
            last_bar = btc["bar"]
            # give scheduler ~3 minutes to submit
            time.sleep(180)
            snap = _db_snapshot()
            if snap["orders"] > 0:
                print("NATURAL_EXCHANGE_EVIDENCE", flush=True)
                return
        # wake near next 15m close + 90s
        minutes = 15 - (now.minute % 15)
        seconds = minutes * 60 - now.second + 90
        time.sleep(max(30, min(seconds, 300)))
    print("WATCH_TIMEOUT", flush=True)


if __name__ == "__main__":
    main()
