"""I-2 RT-04 probe (READ-ONLY).

RT-04: ``list_ohlcv_bars(symbol="BTC/USDT", timeframe="5m")`` must be non-empty.

Also reports per-timeframe coverage and, when 5m bars exist, checks that 5m shares
the same close semantics / timezone / timestamp identity as the other timeframes:
timestamps land exactly on 5-minute boundaries, are strictly increasing, and a
5m->15m aggregation matches the stored 15m bars bar-for-bar.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / ".local_paper_console.db"
ARTIFACT_DIR = ROOT / "artifacts" / "t0-i2-5m-registration"

SYMBOLS = ("BTC/USDT", "ETH/USDT")
TIMEFRAMES = ("1m", "5m", "15m", "1h", "4h", "1d")


def _aggregation_agreement(conn: sqlite3.Connection, symbol: str) -> dict[str, int]:
    """Fold 5m bars into 15m buckets in Python and compare against stored 15m bars.

    Done in Python rather than SQL on purpose: the only index is
    ``(symbol, exchange, timeframe, time)``, and a join that supplies symbol /
    timeframe / time but no ``exchange`` cannot seek on it, so a SQL formulation
    degrades to a scan of all ~37k 15m rows per bucket. The 5m table is small and
    bounded, so we read it once and only fetch the 15m rows inside that window.
    """
    five = conn.execute(
        "SELECT time, open, high, low, close FROM ohlcv_bars WHERE symbol = ? AND timeframe = '5m' ORDER BY time",
        (symbol,),
    ).fetchall()
    result = {
        "compared": 0,
        "open_mismatch": 0,
        "high_mismatch": 0,
        "low_mismatch": 0,
        "close_mismatch": 0,
        "incomplete_buckets": 0,
    }
    if not five:
        return result

    buckets: dict[int, list[tuple[str, float, float, float, float]]] = {}
    for row in five:
        epoch = int(datetime.fromisoformat(row[0]).replace(tzinfo=UTC).timestamp())
        buckets.setdefault((epoch // 900) * 900, []).append(row)

    # Key on the parsed epoch, not the raw string: stored times carry a
    # ".000000" microsecond suffix that a strftime-built key would not reproduce.
    fifteen = {
        int(datetime.fromisoformat(r[0]).replace(tzinfo=UTC).timestamp()): r
        for r in conn.execute(
            "SELECT time, open, high, low, close FROM ohlcv_bars "
            "WHERE symbol = ? AND timeframe = '15m' AND time >= ? AND time <= ? ",
            (symbol, five[0][0], five[-1][0]),
        )
    }

    for bucket_epoch, members in sorted(buckets.items()):
        if len(members) != 3:
            # partial trailing/leading bucket -- not evidence of disagreement
            result["incomplete_buckets"] += 1
            continue
        stored = fifteen.get(bucket_epoch)
        if stored is None:
            result["incomplete_buckets"] += 1
            continue
        members.sort(key=lambda r: r[0])
        result["compared"] += 1
        if abs(members[0][1] - stored[1]) > 1e-6:
            result["open_mismatch"] += 1
        if abs(max(m[2] for m in members) - stored[2]) > 1e-6:
            result["high_mismatch"] += 1
        if abs(min(m[3] for m in members) - stored[3]) > 1e-6:
            result["low_mismatch"] += 1
        if abs(members[-1][4] - stored[4]) > 1e-6:
            result["close_mismatch"] += 1
    return result


def main() -> int:
    conn = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True, timeout=30.0)
    out: dict[str, object] = {"db": str(DB)}

    print("=" * 78)
    print("I-2 RT-04 PROBE  (read-only)")
    print("=" * 78)

    coverage: list[dict[str, object]] = []
    for symbol in SYMBOLS:
        for timeframe in TIMEFRAMES:
            row = conn.execute(
                "SELECT COUNT(*), MIN(time), MAX(time) FROM ohlcv_bars WHERE symbol = ? AND timeframe = ?",
                (symbol, timeframe),
            ).fetchone()
            entry = {
                "symbol": symbol,
                "timeframe": timeframe,
                "bar_count": row[0],
                "first": row[1],
                "last": row[2],
            }
            coverage.append(entry)
            print(f"  {symbol:9s} {timeframe:4s} bars={row[0]:>8}  {row[1]} .. {row[2]}")
    out["coverage"] = coverage

    print()
    btc_5m = next(c for c in coverage if c["symbol"] == "BTC/USDT" and c["timeframe"] == "5m")
    rt04_green = int(btc_5m["bar_count"]) > 0
    print(f"RT-04 (BTC/USDT 5m non-empty) = {'GREEN' if rt04_green else 'RED'}  bar_count={btc_5m['bar_count']}")
    out["rt04_green"] = rt04_green

    identity: dict[str, object] = {}
    if rt04_green:
        print()
        print("--- 5m timestamp identity / close semantics ---")
        for symbol in SYMBOLS:
            rows = conn.execute(
                "SELECT time FROM ohlcv_bars WHERE symbol = ? AND timeframe = '5m' ORDER BY time",
                (symbol,),
            ).fetchall()
            stamps = [r[0] for r in rows]
            if not stamps:
                identity[symbol] = {"bar_count": 0}
                print(f"  {symbol:9s} no 5m bars")
                continue
            off_grid = conn.execute(
                "SELECT COUNT(*) FROM ohlcv_bars WHERE symbol = ? AND timeframe = '5m' "
                "AND (CAST(strftime('%M', time) AS INTEGER) % 5) != 0",
                (symbol,),
            ).fetchone()[0]
            non_zero_seconds = conn.execute(
                "SELECT COUNT(*) FROM ohlcv_bars WHERE symbol = ? AND timeframe = '5m' "
                "AND strftime('%S', time) != '00'",
                (symbol,),
            ).fetchone()[0]
            duplicates = len(stamps) - len(set(stamps))
            monotonic = all(a < b for a, b in zip(stamps, stamps[1:], strict=False))
            identity[symbol] = {
                "bar_count": len(stamps),
                "off_5min_grid": off_grid,
                "non_zero_seconds": non_zero_seconds,
                "duplicate_timestamps": duplicates,
                "strictly_increasing": monotonic,
            }
            print(
                f"  {symbol:9s} bars={len(stamps)} off_grid={off_grid} "
                f"non_zero_seconds={non_zero_seconds} duplicates={duplicates} increasing={monotonic}"
            )

        print()
        print("--- 5m -> 15m aggregation agreement (RT-06 preview) ---")
        for symbol in SYMBOLS:
            identity[f"{symbol}:agg_15m"] = _aggregation_agreement(conn, symbol)
            agg = identity[f"{symbol}:agg_15m"]
            print(
                f"  {symbol:9s} compared={agg['compared']} mismatches o/h/l/c="
                f"{agg['open_mismatch']}/{agg['high_mismatch']}/"
                f"{agg['low_mismatch']}/{agg['close_mismatch']}"
            )
    out["identity"] = identity

    conn.close()
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    target = ARTIFACT_DIR / "I2_RT04_PROBE.json"
    target.write_text(json.dumps(out, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print()
    print(f"wrote {target.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
