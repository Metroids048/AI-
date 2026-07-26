#!/usr/bin/env python3
"""
Phase 5: 1d/4h Swing strategy OOS validation.

Uses TechnicalStrategyValidationService.replay() against AUTO_PAPER_SWING_RULES
(1d direction + 4h entry) and compares metrics against AGENTS.md validation gates:
  - Sharpe Ratio > 1.0
  - Profit Factor > 1.3
  - Max Drawdown < 25%
  - Expectancy > 0

If all gates pass, the strategy is approved for转正 (enable auto_schedule_enabled=True
and wire into bootstrap_local_paper_runtime()).
"""

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from services.data import DataRepository
from services.data.binance import BinanceCcxtClient
from services.database import get_session_factory
from services.execution.bootstrap import AUTO_PAPER_SWING_KEY, AUTO_PAPER_SWING_RULES
from services.validation.technical_replay import MarketData, TechnicalStrategyValidationService
from shared.models import OHLCVBar, StrategyContract, StrategyRules, Timeframe


def _load_market_data(*, days: int, end_at: datetime, symbols: tuple[str, ...]) -> MarketData:
    """Load or backfill OHLCV data for swing strategy (1d/4h timeframes)."""
    start_at = end_at - timedelta(days=days)
    client = BinanceCcxtClient()
    market_data: MarketData = {}

    with get_session_factory()() as session:
        repository = DataRepository(session)
        for symbol in symbols:
            market_data[symbol] = {}
            # Swing strategy needs 1d for direction, 4h for entry
            for timeframe in ("1d", "4h"):
                # Try loading from database first
                existing_bars = repository.list_ohlcv_bars(
                    symbol=symbol,
                    timeframe=timeframe,
                    start_at=start_at,
                    end_at=end_at,
                )

                if len(existing_bars) < 30:  # Need at least 30 bars for meaningful replay
                    print(f"Backfilling {symbol} {timeframe} data...")
                    bars = client.fetch_ohlcv_history(
                        symbol=f"{symbol}:USDT",
                        timeframe=timeframe,
                        start_at=start_at,
                        end_at=end_at,
                    )
                    platform_bars: list[OHLCVBar | dict[str, Any]] = [
                        bar.model_copy(update={"symbol": symbol}) for bar in bars
                    ]
                    repository.store_ohlcv_bars(platform_bars)
                    session.commit()
                    market_data[symbol][timeframe] = platform_bars
                else:
                    market_data[symbol][timeframe] = cast(list, existing_bars)

    return market_data


def main() -> int:
    """Run Swing strategy validation and generate approval report."""
    lookback_days = 90  # 3 months of historical data
    symbols = ("BTC/USDT", "ETH/USDT")

    end_time = datetime.now(UTC)
    start_time = end_time - timedelta(days=lookback_days)

    print("[Swing Strategy OOS Validation]")
    print(f"Time range: {start_time.date()} to {end_time.date()} ({lookback_days} days)")
    print(f"Strategy: {AUTO_PAPER_SWING_KEY}")
    print("Direction TF: 1d, Entry TF: 4h, State TF: 1d")
    print(f"Symbols: {', '.join(symbols)}")
    print()

    # Load market data
    print("[Loading Market Data]")
    market_data = _load_market_data(days=lookback_days, end_at=end_time, symbols=symbols)

    # Check data availability
    for symbol in symbols:
        for tf in ("1d", "4h"):
            bar_count = len(market_data.get(symbol, {}).get(tf, []))
            print(f"{symbol} {tf}: {bar_count} bars")
            if bar_count < 30:
                print(f"❌ Insufficient {tf} data for {symbol} (need at least 30 bars)")
                return 1
    print()

    # Create strategy contract
    strategy = StrategyContract(
        strategy_id=AUTO_PAPER_SWING_KEY,
        strategy_key=AUTO_PAPER_SWING_KEY,
        source="platform:swing_validation",
        core_thesis="Medium-term swing: 1d direction + 4h entry, offline validation only",
        symbol_scope=list(symbols),
        timeframe=Timeframe.H4,  # Entry timeframe
        rules=StrategyRules(**AUTO_PAPER_SWING_RULES),
    )

    # Run replay
    print("[Running OOS Replay]")
    print("This may take 2-5 minutes...")
    print()

    service = TechnicalStrategyValidationService(max_workers=4, warmup_bars=80)
    try:
        metrics = service.replay(
            strategy=strategy,
            market_data=market_data,
            start_at=start_time,
            end_at=end_time,
        )
    except Exception as e:
        print(f"❌ Replay failed: {e}")
        import traceback

        traceback.print_exc()
        return 1

    # Extract metrics
    sharpe = metrics.sharpe
    profit_factor = metrics.profit_factor
    max_dd = metrics.max_drawdown  # This is already a fraction (0.15 = 15%)
    max_dd_pct = max_dd * 100.0
    expectancy = metrics.net_expectancy
    total_trades = metrics.total_trades
    win_rate = metrics.win_rate

    print()
    print("[OOS Validation Results]")
    print(f"Total trades: {total_trades}")
    print(f"Win rate: {win_rate:.2%}")
    print(f"Sharpe Ratio: {sharpe:.3f} (gate: > 1.0)")
    print(f"Profit Factor: {profit_factor:.3f} (gate: > 1.3)")
    print(f"Max Drawdown: {max_dd_pct:.2f}% (gate: < 25%)")
    print(f"Expectancy: {expectancy:.4f} (gate: > 0)")
    print()

    # Check validation gates from AGENTS.md
    gates_passed = []
    gates_failed = []

    if sharpe > 1.0:
        gates_passed.append("✅ Sharpe Ratio")
    else:
        gates_failed.append(f"❌ Sharpe Ratio ({sharpe:.3f} <= 1.0)")

    if profit_factor > 1.3:
        gates_passed.append("✅ Profit Factor")
    else:
        gates_failed.append(f"❌ Profit Factor ({profit_factor:.3f} <= 1.3)")

    if max_dd_pct < 25.0:
        gates_passed.append("✅ Max Drawdown")
    else:
        gates_failed.append(f"❌ Max Drawdown ({max_dd_pct:.2f}% >= 25%)")

    if expectancy > 0:
        gates_passed.append("✅ Expectancy")
    else:
        gates_failed.append(f"❌ Expectancy ({expectancy:.4f} <= 0)")

    # Check minimum sample size
    if total_trades < 20:
        gates_failed.append(f"❌ Insufficient sample size ({total_trades} < 20 trades)")
    else:
        gates_passed.append(f"✅ Sample size ({total_trades} trades)")

    print("[Validation Gates]")
    for gate in gates_passed:
        print(gate)
    for gate in gates_failed:
        print(gate)
    print()

    # Generate report
    report_path = Path("docs/audit/swing-strategy-oos-report.md")
    report_path.parent.mkdir(parents=True, exist_ok=True)

    with open(report_path, "w", encoding="utf-8") as f:
        f.write("# 1d/4h Swing Strategy OOS Validation Report\n\n")
        f.write(f"**Generated**: {datetime.now(UTC).isoformat()}\n\n")
        f.write(f"**Strategy**: `{AUTO_PAPER_SWING_KEY}`\n\n")
        f.write("**Timeframes**: Direction=1d, Entry=4h, State=1d\n\n")
        f.write(f"**Validation Period**: {start_time.date()} to {end_time.date()} ({lookback_days} days)\n\n")
        f.write(f"**Symbols**: {', '.join(symbols)}\n\n")
        f.write("## Metrics\n\n")
        f.write("| Metric | Value | Gate | Status |\n")
        f.write("|--------|-------|------|--------|\n")
        f.write(f"| Total Trades | {total_trades} | ≥ 20 | {'✅' if total_trades >= 20 else '❌'} |\n")
        f.write(f"| Win Rate | {win_rate:.2%} | - | - |\n")
        f.write(f"| Sharpe Ratio | {sharpe:.3f} | > 1.0 | {'✅' if sharpe > 1.0 else '❌'} |\n")
        f.write(f"| Profit Factor | {profit_factor:.3f} | > 1.3 | {'✅' if profit_factor > 1.3 else '❌'} |\n")
        f.write(f"| Max Drawdown | {max_dd_pct:.2f}% | < 25% | {'✅' if max_dd_pct < 25.0 else '❌'} |\n")
        f.write(f"| Expectancy | {expectancy:.4f} | > 0 | {'✅' if expectancy > 0 else '❌'} |\n\n")

        if gates_failed:
            f.write("## ❌ Validation Result: REJECTED\n\n")
            f.write("The strategy did NOT pass all validation gates:\n\n")
            for gate in gates_failed:
                f.write(f"- {gate}\n")
            f.write("\n**Decision**: Keep `auto_schedule_enabled=False` and do NOT wire into ")
            f.write("`bootstrap_local_paper_runtime()`. Strategy remains disabled until ")
            f.write("further optimization or longer data accumulation.\n")
        else:
            f.write("## ✅ Validation Result: APPROVED\n\n")
            f.write("All validation gates passed. The strategy is approved for转正:\n\n")
            f.write("1. Set `auto_schedule_enabled=True` in `bootstrap_auto_trading_swing_paper_run()`\n")
            f.write("2. Add `bootstrap_auto_trading_swing_paper_run()` call to `bootstrap_local_paper_runtime()`\n")
            f.write("3. Monitor for 7-14 days to confirm live performance matches OOS backtest\n")

    print("[Report Generated]")
    print(f"Saved to: {report_path}")
    print()

    if gates_failed:
        print("❌ VALIDATION FAILED")
        print("Strategy remains disabled (auto_schedule_enabled=False)")
        return 1
    else:
        print("✅ VALIDATION PASSED")
        print("Strategy is approved for转正 (pending manual bootstrap.py edit)")
        return 0


if __name__ == "__main__":
    sys.exit(main())
