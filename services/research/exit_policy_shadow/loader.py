"""Read-only loader for real testnet_sampling_v2 fills and bars.

Opens SQLite in read-only mode. No INSERT/UPDATE/DELETE authority.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from services.research.exit_policy_shadow.contracts import Bar, RealEntry, Regime


def load_real_entries(db_path: Path) -> list[RealEntry]:
    """Load all testnet_sampling_v2 CLOSED positions from the local runtime database.

    The database is opened in read-only mode. Returns entries sorted by fill time.
    """
    uri = f"file:{db_path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute(
        """
        SELECT
            mp.position_id,
            mp.symbol,
            mp.direction,
            mp.quantity,
            mp.entry_price,
            mp.entry_fee,
            mp.projected_at,
            i.candidate_key,
            i.decision_bar_timestamp,
            o.average_fill_price,
            o.fill_timestamp,
            o.exchange_order_id
        FROM v2_managed_positions mp
        JOIN v2_execution_intents i ON mp.intent_id = i.intent_id
        JOIN v2_exchange_orders o ON mp.order_record_id = o.order_record_id
        WHERE i.candidate_key = 'testnet_sampling_v2'
          AND mp.state = 'CLOSED'
        ORDER BY o.fill_timestamp
        """
    )
    rows = cur.fetchall()
    conn.close()

    entries: list[RealEntry] = []
    for row in rows:
        fill_ts_str = row["fill_timestamp"]
        decision_ts_str = row["decision_bar_timestamp"]

        # Parse ISO timestamps.
        fill_ts = datetime.fromisoformat(fill_ts_str.replace(" ", "T")).replace(tzinfo=UTC)
        decision_ts = datetime.fromisoformat(decision_ts_str.replace(" ", "T")).replace(tzinfo=UTC)

        entries.append(
            RealEntry(
                position_id=row["position_id"],
                symbol=row["symbol"],
                side=row["direction"],
                average_fill_price=Decimal(str(row["average_fill_price"])),
                fill_timestamp=fill_ts,
                filled_quantity=Decimal(str(row["quantity"])),
                entry_fee_usdt=Decimal(str(row["entry_fee"])),
                candidate_key=row["candidate_key"],
                decision_bar_timestamp=decision_ts,
                exchange_order_id=str(row["exchange_order_id"]),
            )
        )

    return entries


def load_bars(db_path: Path, *, symbol: str, timeframe: str, start: datetime, end: datetime) -> list[Bar]:
    """Load OHLCV bars for replay.

    Returns bars in [start, end], sorted chronologically.
    """
    uri = f"file:{db_path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute(
        """
        SELECT time, open, high, low, close, volume
        FROM ohlcv_bars
        WHERE symbol = ? AND timeframe = ?
          AND time >= ? AND time <= ?
        ORDER BY time
        """,
        (symbol, timeframe, _to_db_timestamp(start), _to_db_timestamp(end)),
    )
    rows = cur.fetchall()
    conn.close()

    bars: list[Bar] = []
    for row in rows:
        time_str = row["time"]
        bar_time = datetime.fromisoformat(time_str.replace(" ", "T")).replace(tzinfo=UTC)
        bars.append(
            Bar(
                time=bar_time,
                open=Decimal(str(row["open"])),
                high=Decimal(str(row["high"])),
                low=Decimal(str(row["low"])),
                close=Decimal(str(row["close"])),
                volume=Decimal(str(row["volume"])),
            )
        )
    return bars


DB_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S.%f"


def _to_db_timestamp(value: datetime) -> str:
    """Render a datetime the way this database stores it.

    `ohlcv_bars.time` holds space-separated, timezone-naive strings such as
    ``2026-08-07 10:00:00.000000``, and the range filters are string comparisons.
    Passing ``datetime.isoformat()`` here silently loses data: ``'T'`` (0x54) sorts
    after ``' '`` (0x20), so every bar on the start date fails ``time >= start`` and
    the replay begins hours late without raising anything.
    """
    if value.tzinfo is not None:
        value = value.astimezone(UTC).replace(tzinfo=None)
    return value.strftime(DB_TIMESTAMP_FORMAT)


ATR_PERIOD = 14
ENTRY_TIMEFRAME = "15m"


def build_entry_context(db_path: Path, *, symbol: str, decision_bar: datetime) -> dict[str, Decimal] | None:
    """Compute entry-time ATR14 strictly from bars closed at or before the decision bar.

    Point-in-time contract: only 15m bars whose open time is <= ``decision_bar`` are
    read, so no post-entry information can leak into the initial geometry. This
    mirrors the production sampling rule's own ATR14 input, which is what lets
    policy A reproduce the real stop/target instead of a fabricated fallback.

    Returns None when insufficient history exists, so the caller can skip the entry
    rather than silently substitute a placeholder ATR.
    """
    lookback_start = decision_bar - timedelta(minutes=15 * (ATR_PERIOD * 6))
    bars = load_bars(
        db_path,
        symbol=symbol,
        timeframe=ENTRY_TIMEFRAME,
        start=lookback_start,
        end=decision_bar,
    )
    atr = compute_atr(bars, period=ATR_PERIOD)
    if atr is None or atr <= 0:
        return None
    return {"atr14": atr}


def compute_atr(bars: list[Bar], *, period: int = ATR_PERIOD) -> Decimal | None:
    """Wilder-style ATR over ``bars``, or None when history is insufficient.

    True range uses the previous bar's close, so the first bar cannot contribute and
    ``period + 1`` bars are required.
    """
    if len(bars) < period + 1:
        return None

    true_ranges: list[Decimal] = []
    # strict=False is deliberate: bars[1:] is one shorter by construction, which is
    # exactly the consecutive-pair pattern needed for true range.
    for previous, current in zip(bars, bars[1:], strict=False):
        high_low = current.high - current.low
        high_close = abs(current.high - previous.close)
        low_close = abs(current.low - previous.close)
        true_ranges.append(max(high_low, high_close, low_close))

    if len(true_ranges) < period:
        return None

    atr = sum(true_ranges[:period], Decimal("0")) / Decimal(period)
    for true_range in true_ranges[period:]:
        atr = (atr * Decimal(period - 1) + true_range) / Decimal(period)
    return atr


def classify_regime(*, bars: list[Bar]) -> Regime:
    """Classify the entry-time regime from point-in-time bars only.

    Deliberately conservative: P2-A has no validated regime classifier, and the
    frozen scope forbids inventing one to make a slice look better. Callers pass
    bars that closed at or before the decision bar, and this returns UNKNOWN so the
    regime-aware policy fails closed onto the baseline rather than fabricating a
    label. Real classification is a separate, reviewable piece of work.
    """
    return Regime.UNKNOWN
