"""Chan Theory (缠论) buy/sell point signal adapter.

Wraps Vespa314/chan.py CChan implementation to emit TradeSignal compatible
with the project's SignalEnsemble voting system. Chan Theory provides objective
rules for identifying structural buy/sell points based on:
- K-line containment processing
- Top/bottom fractals (分型)
- Strokes (笔)
- Segments (线段)
- Hubs (中枢)
- Divergence (背驰)
- Three classes of buy/sell points (一二三类买卖点)

STATUS: Adapter skeleton only. Actual integration requires:
1. Vendor Vespa314/chan.py into research_source/open_source_strategy_library/assets/
2. Run backtest validation via scripts/backtest_chan_signal_replay.py
3. Only integrate into SignalEnsemble if backtest net expectancy > 0

See docs/chan-theory-integration-guide.md for full implementation steps.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from shared.models import OHLCVBar, TradeSignal, TradeSide

# Placeholder: actual implementation requires chan.py vendored or pip-installed
try:
    from chan import CChan, KLine_Unit
    from chan.Common.CEnum import BSP_TYPE

    CHAN_AVAILABLE = True
except ImportError:
    CHAN_AVAILABLE = False


def extract_chan_signals(
    *,
    bars: list[OHLCVBar],
    symbol: str,
    timeframe: str,
    enable_buy_1: bool = True,
    enable_buy_2: bool = True,
    enable_buy_3: bool = False,  # More aggressive, default off until validated
    enable_sell_1: bool = True,
    enable_sell_2: bool = True,
    enable_sell_3: bool = False,
) -> list[TradeSignal]:
    """Extract Chan Theory buy/sell points from OHLCV bars.

    Args:
        bars: OHLCV bars (oldest first), must cover enough history for hub formation
        symbol: Trading pair
        timeframe: Chart timeframe
        enable_buy_1/2/3: Enable first/second/third-class buy points
        enable_sell_1/2/3: Enable first/second/third-class sell points

    Returns:
        List of TradeSignal (one per detected buy/sell point in the latest bar)
    """
    if not CHAN_AVAILABLE:
        # Return empty list when chan.py not installed rather than raising
        # ImportError, so strategies that include "chan_t1" in enabled_signals
        # can gracefully skip it if the asset ingestion step hasn't run yet.
        return []

    if len(bars) < 50:  # Chan Theory needs sufficient history for hub detection
        return []

    # Convert OHLCVBar to chan.py KLine_Unit format
    kline_list = [
        KLine_Unit(
            {
                "time": bar.timestamp.isoformat(),
                "open": float(bar.open),
                "high": float(bar.high),
                "low": float(bar.low),
                "close": float(bar.close),
                "volume": float(bar.volume),
            }
        )
        for bar in bars
    ]

    # Initialize CChan with default config (can be customized via CChan.conf)
    cchan = CChan(kline_list=kline_list, begin_time=bars[0].timestamp, end_time=bars[-1].timestamp)

    # Extract buy/sell points
    bsp_list = cchan.get_bsp()
    signals: list[TradeSignal] = []

    latest_bar_time = bars[-1].timestamp

    for bsp in bsp_list:
        # Only emit signals for the latest bar (avoid look-ahead bias)
        bsp_time = datetime.fromisoformat(bsp.klu.time)
        if abs((bsp_time - latest_bar_time).total_seconds()) > 3600:  # 1h tolerance
            continue

        # Map chan.py BSP_TYPE to project TradeSide
        if bsp.type in {BSP_TYPE.T1, BSP_TYPE.T2, BSP_TYPE.T3}:
            side = TradeSide.LONG
            enabled = (
                (enable_buy_1 and bsp.type == BSP_TYPE.T1)
                or (enable_buy_2 and bsp.type == BSP_TYPE.T2)
                or (enable_buy_3 and bsp.type == BSP_TYPE.T3)
            )
        elif bsp.type in {BSP_TYPE.S1, BSP_TYPE.S2, BSP_TYPE.S3}:
            side = TradeSide.SHORT
            enabled = (
                (enable_sell_1 and bsp.type == BSP_TYPE.S1)
                or (enable_sell_2 and bsp.type == BSP_TYPE.S2)
                or (enable_sell_3 and bsp.type == BSP_TYPE.S3)
            )
        else:
            continue  # Unknown type, skip

        if not enabled:
            continue

        signals.append(
            TradeSignal(
                symbol=symbol,
                side=side,
                source=f"technical_chan_{bsp.type.value.lower()}",  # e.g., "technical_chan_t1"
                confidence=Decimal("0.75"),  # Default confidence, tune based on backtest
                timestamp=latest_bar_time,
                reference_price=Decimal(str(bars[-1].close)),
                reason=f"Chan Theory {bsp.type.value} buy/sell point detected",
            )
        )

    return signals
