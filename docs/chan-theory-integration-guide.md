# Chan Theory (缠论) Integration Guide

**Status**: Ready for implementation after open-source asset ingestion  
**License**: MIT (Vespa314/chan.py verified)  
**Priority**: Medium (parallel with Module 11 swing trading validation)

---

## Why Use Open-Source Implementation?

Chan Theory has **objective, programmable rules** (K-line containment → fractals → strokes → segments → hubs → divergence → buy/sell points), and re-implementing from scratch risks edge-case bugs that have already been debugged in the community. The `Vespa314/chan.py` library (1.5k+ stars, MIT license, pure Python) is a battle-tested implementation with configurable algorithms for segment calculation and buy/sell point detection.

---

## Implementation Steps

### 1. Ingest Open-Source Asset

Follow the existing pattern in `research_source/open_source_strategy_library/`:

```bash
# Clone Vespa314/chan.py as a vendored asset
cd research_source/open_source_strategy_library/assets/
git clone https://github.com/Vespa314/chan.py chan_py
cd chan_py
git rev-parse HEAD  # Record commit hash for asset_manifest.json
```

Create `asset_manifest.json` entry:
```json
{
  "asset_id": "chan_py:vespa314",
  "source_repo": "https://github.com/Vespa314/chan.py",
  "license": "MIT",
  "license_policy": "vendored_dependency",
  "updated_at": "2026-07-14T...",
  "commit_hash": "<git-rev-parse-HEAD>",
  "summary": "Chan Theory technical analysis: fractals, strokes, segments, hubs, divergence, buy/sell points"
}
```

### 2. Create Adapter Layer

**File**: `services/strategy_library/technical/chan_theory.py`

```python
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
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

# After ingestion, chan.py modules will be importable from vendored location
# or via `pip install chan-py` if you prefer managed dependency
try:
    from chan import CChan, KLine_Unit
    from chan.Common.CEnum import BSP_TYPE
except ImportError:
    raise ImportError(
        "chan.py not found. Install via 'pip install chan-py' or "
        "vendor Vespa314/chan.py into research_source/open_source_strategy_library/assets/"
    )

from shared.models import OHLCVBar, TradeSignal, TradeSide


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
```

### 3. Backtest Validation Script

**File**: `scripts/backtest_chan_signal_replay.py`

```python
"""Standalone backtest for Chan Theory signals (Module 6).

Validates whether chan.py buy/sell points have positive net expectancy on
Top20 USD-M perpetuals over 90 days, BEFORE integrating into live SignalEnsemble.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta

from services.strategy_library.technical.chan_theory import extract_chan_signals
from services.validation.technical_replay import TechnicalStrategyValidationService
from scripts.run_top20_technical_validation import _load_or_backfill
from shared.models import StrategyContract, StrategyRules, Timeframe


def main() -> int:
    parser = argparse.ArgumentParser(description="Backtest Chan Theory signals")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--enable-buy-3", action="store_true", help="Enable aggressive third-class buy points")
    parser.add_argument("--enable-sell-3", action="store_true", help="Enable aggressive third-class sell points")
    args = parser.parse_args()

    print(f"Loading Top20 market data ({args.days} days)...")
    end_at = datetime.now(UTC)
    market_data = _load_or_backfill(days=args.days, end_at=end_at)

    # Define a minimal strategy that ONLY uses Chan Theory signals
    # (no other technical signals mixed in, to isolate Chan's edge)
    strategy = StrategyContract(
        strategy_id="chan_theory_backtest",
        strategy_key="chan_theory_backtest",
        source="backtest",
        core_thesis="Chan Theory buy/sell points (缠论买卖点) standalone validation",
        rules=StrategyRules(
            entry_rules={
                "technical_pipeline": True,
                "timeframe_model": "custom",
                "direction_timeframe": "4h",
                "entry_timeframe": "1h",
                "enabled_signals": ["chan_t1", "chan_t2"],  # Placeholder, actual signals injected via pipeline_factory
                "enable_buy_3": args.enable_buy_3,
                "enable_sell_3": args.enable_sell_3,
            },
            stoploss_rules={"atr_multiple": 2.0},
            takeprofit_rules={"risk_reward": 2.0},
            position_rules={"risk_per_trade": 0.02, "max_leverage": 20, "max_position_fraction": 0.15},
        ),
    )

    # Custom pipeline factory that injects chan.py signals
    def chan_pipeline_factory(view):
        from services.execution.decision_pipeline import DecisionPipelineResult

        class ChanPipeline:
            def __init__(self, view):
                self.view = view

            def evaluate(self, *, symbol: str, timeframe: str, **_):
                bars = self.view.list_ohlcv_bars(symbol=symbol, timeframe=timeframe, limit=200)
                signals = extract_chan_signals(
                    bars=bars,
                    symbol=symbol,
                    timeframe=timeframe,
                    enable_buy_3=args.enable_buy_3,
                    enable_sell_3=args.enable_sell_3,
                )
                if not signals:
                    return DecisionPipelineResult(
                        direction=None, should_trade=False, reason="no_chan_signal", reference_price=bars[-1].close
                    )
                # Take the first signal (if multiple at same bar, chan.py prioritizes by type)
                signal = signals[0]
                return DecisionPipelineResult(
                    direction=signal.side,
                    should_trade=True,
                    reason=f"chan_{signal.source}",
                    reference_price=signal.reference_price,
                )

        return ChanPipeline(view)

    print("Running historical replay with Chan Theory signals...")
    service = TechnicalStrategyValidationService(warmup_bars=80, max_workers=8, pipeline_factory=chan_pipeline_factory)
    metrics = service.replay(strategy=strategy, market_data=market_data)

    print("\n" + "=" * 80)
    print("CHAN THEORY BACKTEST RESULTS")
    print("=" * 80)
    print(f"Signals:         {metrics.signal_count}")
    print(f"Trades:          {metrics.total_trades}")
    print(f"Win rate:        {metrics.win_rate:.4f}")
    print(f"Net expectancy:  {metrics.net_expectancy:.6f}")
    print(f"Profit factor:   {metrics.profit_factor:.4f}")
    print(f"Max drawdown:    {metrics.max_drawdown:.4f}")
    print("=" * 80)

    if metrics.net_expectancy > 0:
        print("✅ POSITIVE NET EXPECTANCY")
        print("   → Chan Theory signals have independent edge on Top20.")
        print("   → Safe to integrate into SignalEnsemble voting pool.")
    else:
        print("❌ NEGATIVE NET EXPECTANCY")
        print("   → Chan Theory signals (with default chan.py params) do NOT have")
        print("      independent positive edge on Top20 crypto perpetuals.")
        print("   → Do NOT integrate into live trading without further tuning.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

### 4. Optional Visual Validation

If backtest shows positive net expectancy and you want extra confidence:

```python
# Pick 10-20 historical buy/sell points from the backtest results
# Use chan.py's built-in plotting (if available) or custom Plotly to overlay
# detected points on K-line charts, compare against your manual judgment
```

But this is now **optional** (not a blocker), since the acceptance criterion is "does it make money in backtest?"

---

## Integration into SignalEnsemble (After Validation)

If backtest net expectancy > 0:

1. Add `"chan_t1"`, `"chan_t2"` to `enabled_signals` in `AUTO_PAPER_TECHNICAL_RULES` (or `AUTO_PAPER_SWING_RULES` if using daily timeframe)
2. Register signal extractor in `services/execution/decision_pipeline.py::_extract_technical_signals()`
3. Assign voting weight in `_signal_weight()` (start conservatively, e.g., base 0.6, scaled by confidence)

---

## Why This Approach Works

1. **No manual labeling bottleneck**: Starts with code from day one
2. **Objective validation**: Backtest net expectancy is the acceptance criterion, not subjective "does this match my eye?"
3. **Lower risk**: Uses a 1.5k-star, multi-year maintained implementation instead of re-inventing fractal/segment logic
4. **Configurable**: chan.py supports multiple segment algorithms and buy/sell point tuning params

---

## Next Steps

- [ ] Complete open-source asset ingestion (add chan.py to `asset_manifest.json`)
- [ ] Implement `services/strategy_library/technical/chan_theory.py` adapter
- [ ] Run `scripts/backtest_chan_signal_replay.py` on Top20 90-day window
- [ ] If net expectancy > 0, integrate into SignalEnsemble; else, document as "validated negative edge"
