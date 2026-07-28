"""Scan recent closed 15m bars for Sampling-lane alignment."""

from __future__ import annotations

import pandas as pd

from services.data import DataRepository
from services.database import get_session_factory
from services.execution.decision_pipeline import closed_bars_for_decision


def _direction(frame: pd.DataFrame) -> tuple[str | None, dict[str, float]]:
    close = frame["close"]
    ema50 = float(close.ewm(span=50, adjust=False).mean().iloc[-1])
    macd_line = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    macd_hist = float((macd_line - macd_line.ewm(span=9, adjust=False).mean()).iloc[-1])
    delta = close.diff()
    average_gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    average_loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    relative_strength = average_gain / average_loss.replace(0, float("nan"))
    rsi = float((100 - (100 / (1 + relative_strength))).iloc[-1])
    latest = float(close.iloc[-1])
    metrics = {"close": latest, "ema50": ema50, "macd": macd_hist, "rsi": rsi}
    if latest > ema50 and macd_hist > 0 and 50 <= rsi <= 72:
        return "LONG", metrics
    if latest < ema50 and macd_hist < 0 and 28 <= rsi <= 50:
        return "SHORT", metrics
    return None, metrics


def main() -> None:
    session = get_session_factory()()
    try:
        data = DataRepository(session)
        for symbol in ("BTC/USDT", "ETH/USDT"):
            from datetime import UTC, datetime

            bars = closed_bars_for_decision(
                data.list_ohlcv_bars(symbol=symbol, timeframe="15m", limit=160),
                timeframe="15m",
                decision_time=datetime.now(UTC),
            )
            print(symbol, "bars", len(bars), "last", bars[-1].timestamp if bars else None)
            hits = 0
            for end in range(52, len(bars) + 1):
                window = bars[:end]
                frame = pd.DataFrame(
                    [
                        {
                            "open": float(bar.open),
                            "high": float(bar.high),
                            "low": float(bar.low),
                            "close": float(bar.close),
                            "volume": float(bar.volume),
                        }
                        for bar in window
                    ],
                    index=pd.DatetimeIndex([bar.timestamp for bar in window]),
                )
                direction, metrics = _direction(frame)
                if direction is None:
                    continue
                hits += 1
                if end > len(bars) - 16:
                    print(
                        " ALIGN",
                        symbol,
                        window[-1].timestamp,
                        direction,
                        {k: round(v, 4) if isinstance(v, float) else v for k, v in metrics.items()},
                    )
            print(" total_align_hits_in_window", hits)
            # show latest metrics even if not aligned
            frame = pd.DataFrame(
                [
                    {
                        "open": float(bar.open),
                        "high": float(bar.high),
                        "low": float(bar.low),
                        "close": float(bar.close),
                        "volume": float(bar.volume),
                    }
                    for bar in bars
                ],
                index=pd.DatetimeIndex([bar.timestamp for bar in bars]),
            )
            direction, metrics = _direction(frame)
            print(" latest", direction, {k: round(v, 4) for k, v in metrics.items()})
    finally:
        session.close()


if __name__ == "__main__":
    main()
