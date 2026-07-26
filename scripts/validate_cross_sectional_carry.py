"""Run OOS validation for the cross-sectional funding-rate carry strategy.

This is the gate required before AUTO_PAPER_CROSS_SECTIONAL_CARRY_RULES can be
enabled for auto-trading. Must meet AGENTS.md validation gate:
- Sharpe > 1.0
- Profit Factor > 1.3
- Max Drawdown < 25%
- Expectancy > 0
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

from services.data import DataRepository
from services.data.service import DEFAULT_BINANCE_TOP20
from services.database import get_session_factory
from services.validation.cross_sectional_replay import run_cross_sectional_replay


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate cross-sectional carry strategy")
    parser.add_argument(
        "--database-url",
        default="sqlite:///.local_paper_console.db",
        help="Database URL (default: local SQLite)",
    )
    parser.add_argument(
        "--basket-size",
        type=int,
        default=3,
        help="Basket size per side (default: 3)",
    )
    parser.add_argument(
        "--rebalance-hours",
        type=int,
        default=8,
        help="Hours between rebalances (default: 8)",
    )
    parser.add_argument(
        "--min-net-edge-bps",
        type=float,
        default=5.0,
        help="Minimum net edge after costs (default: 5.0 bps)",
    )
    parser.add_argument(
        "--fee-bps",
        type=float,
        default=5.0,
        help="Trading fee per side (default: 5.0 bps)",
    )
    parser.add_argument(
        "--slippage-bps",
        type=float,
        default=3.0,
        help="Slippage per side (default: 3.0 bps)",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=90,
        help="Lookback period in days (default: 90)",
    )
    parser.add_argument(
        "--output",
        default="docs/audit/cross-sectional-carry-oos-report.md",
        help="Output report path",
    )
    args = parser.parse_args()

    # Connect to database
    with get_session_factory(args.database_url)() as session:
        data_repo = DataRepository(session)

        # Determine time range
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(days=args.lookback_days)

        print("[Carry OOS Validation]")
        print(f"Database: {args.database_url}")
        print(f"Time range: {start_time.date()} to {end_time.date()} ({args.lookback_days} days)")
        print(f"Universe: Top20 ({len(DEFAULT_BINANCE_TOP20)} symbols)")
        print(f"Basket size: {args.basket_size} per side")
        print(f"Rebalance: every {args.rebalance_hours}h")
        print(f"Min net edge: {args.min_net_edge_bps} bps")
        print(f"Fee: {args.fee_bps} bps/side, Slippage: {args.slippage_bps} bps/side")
        print()

        # Run replay
        result = run_cross_sectional_replay(
            data_repo=data_repo,
            symbols=list(DEFAULT_BINANCE_TOP20),
            basket_size=args.basket_size,
            rebalance_hours=args.rebalance_hours,
            min_estimated_net_edge_bps=args.min_net_edge_bps,
            fee_bps=args.fee_bps,
            slippage_bps=args.slippage_bps,
            start_time=start_time,
            end_time=end_time,
        )

        # Print results
        print(f"Total rebalance cycles: {result.total_rebalance_cycles}")
        print(f"Legs opened: {result.total_legs_opened}")
        print(f"Legs closed: {result.total_legs_closed}")
        print()
        print("Performance Metrics:")
        print(f"  Sharpe Ratio: {result.sharpe:.4f}")
        print(f"  Deflated Sharpe: {result.deflated_sharpe:.4f}")
        print(f"  Profit Factor: {result.profit_factor:.4f}")
        print(f"  Max Drawdown: {result.max_drawdown:.2%}")
        print(f"  Expectancy: {result.expectancy:.4f}")
        print(f"  Win Rate: {result.win_rate:.2%}")
        print(f"  Avg Fee: {result.average_fee_bps:.2f} bps")
        print(f"  Avg Slippage: {result.average_slippage_bps:.2f} bps")
        print()

        # Check against AGENTS.md validation gate
        gate_passed = (
            result.sharpe > 1.0 and result.profit_factor > 1.3 and result.max_drawdown < 0.25 and result.expectancy > 0
        )

        if gate_passed:
            print("✅ VALIDATION GATE PASSED")
            print("Strategy meets all AGENTS.md criteria and can be enabled for auto-trading.")
        else:
            print("❌ VALIDATION GATE FAILED")
            failures = []
            if result.sharpe <= 1.0:
                failures.append(f"Sharpe {result.sharpe:.4f} <= 1.0")
            if result.profit_factor <= 1.3:
                failures.append(f"Profit Factor {result.profit_factor:.4f} <= 1.3")
            if result.max_drawdown >= 0.25:
                failures.append(f"Max Drawdown {result.max_drawdown:.2%} >= 25%")
            if result.expectancy <= 0:
                failures.append(f"Expectancy {result.expectancy:.4f} <= 0")
            print("Failed criteria:")
            for failure in failures:
                print(f"  - {failure}")
            print()
            print("Strategy does NOT meet validation gate and must remain disabled.")

        # Write report
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with output_path.open("w", encoding="utf-8") as f:
            f.write("# Cross-Sectional Funding Carry OOS Validation Report\n\n")
            f.write(f"**Generated**: {datetime.utcnow().isoformat()}Z\n\n")
            f.write("## Strategy Configuration\n\n")
            f.write(f"- **Universe**: Top20 ({len(DEFAULT_BINANCE_TOP20)} symbols)\n")
            f.write(
                f"- **Basket Size**: {args.basket_size} per side (short top {args.basket_size}, long bottom {args.basket_size})\n"
            )
            f.write(f"- **Rebalance Frequency**: Every {args.rebalance_hours} hours\n")
            f.write(f"- **Min Net Edge**: {args.min_net_edge_bps} bps after costs\n")
            f.write(f"- **Transaction Costs**: {args.fee_bps} bps fee + {args.slippage_bps} bps slippage per side\n")
            f.write(f"- **Round-trip Cost**: {(args.fee_bps + args.slippage_bps) * 2:.1f} bps\n\n")
            f.write("## Backtest Period\n\n")
            f.write(f"- **Start**: {result.start_time.date()}\n")
            f.write(f"- **End**: {result.end_time.date()}\n")
            f.write(f"- **Duration**: {(result.end_time - result.start_time).days} days\n")
            f.write(f"- **Rebalance Cycles**: {result.total_rebalance_cycles}\n\n")
            f.write("## Execution Summary\n\n")
            f.write(f"- **Legs Opened**: {result.total_legs_opened}\n")
            f.write(f"- **Legs Closed**: {result.total_legs_closed}\n")
            f.write(f"- **Avg Legs per Cycle**: {result.total_legs_opened / result.total_rebalance_cycles:.2f}\n\n")
            f.write("## Performance Metrics\n\n")
            f.write("| Metric | Value | AGENTS.md Gate | Status |\n")
            f.write("|--------|-------|----------------|--------|\n")
            f.write(
                f"| Sharpe Ratio | {result.sharpe:.4f} | > 1.0 | {'✅ PASS' if result.sharpe > 1.0 else '❌ FAIL'} |\n"
            )
            f.write(f"| Deflated Sharpe | {result.deflated_sharpe:.4f} | - | - |\n")
            f.write(
                f"| Profit Factor | {result.profit_factor:.4f} | > 1.3 | {'✅ PASS' if result.profit_factor > 1.3 else '❌ FAIL'} |\n"
            )
            f.write(
                f"| Max Drawdown | {result.max_drawdown:.2%} | < 25% | {'✅ PASS' if result.max_drawdown < 0.25 else '❌ FAIL'} |\n"
            )
            f.write(
                f"| Expectancy | {result.expectancy:.4f} | > 0 | {'✅ PASS' if result.expectancy > 0 else '❌ FAIL'} |\n"
            )
            f.write(f"| Win Rate | {result.win_rate:.2%} | - | - |\n\n")
            f.write("## Cost Analysis\n\n")
            f.write(f"- **Average Fee**: {result.average_fee_bps:.2f} bps (round-trip)\n")
            f.write(f"- **Average Slippage**: {result.average_slippage_bps:.2f} bps (round-trip)\n")
            f.write(
                f"- **Total Cost**: {result.average_fee_bps + result.average_slippage_bps:.2f} bps per round-trip\n\n"
            )
            f.write("## Decision\n\n")
            if gate_passed:
                f.write("**✅ VALIDATION GATE PASSED**\n\n")
                f.write("This strategy meets all AGENTS.md validation criteria:\n")
                f.write("- Sharpe > 1.0 ✅\n")
                f.write("- Profit Factor > 1.3 ✅\n")
                f.write("- Max Drawdown < 25% ✅\n")
                f.write("- Expectancy > 0 ✅\n\n")
                f.write(
                    "**Action Required**: Set `AUTO_PAPER_CROSS_SECTIONAL_CARRY_RULES['entry_rules']['default_enabled_for_auto_trading'] = True` in `services/execution/bootstrap.py` and ensure `bootstrap_cross_sectional_carry_strategy()` is called in `bootstrap_local_paper_runtime()`.\n"
                )
            else:
                f.write("**❌ VALIDATION GATE FAILED**\n\n")
                f.write("This strategy does NOT meet AGENTS.md validation criteria:\n\n")
                for failure in failures:
                    f.write(f"- {failure}\n")
                f.write(
                    "\n**Action Required**: Strategy must remain disabled (`default_enabled_for_auto_trading=False`). Consider:\n"
                )
                f.write("1. Adjusting basket_size or rebalance_hours\n")
                f.write("2. Tightening min_estimated_net_edge_bps threshold\n")
                f.write("3. Waiting for more favorable funding-rate market conditions\n")
                f.write("4. Validating that sufficient funding-rate history exists in the database\n")

        print(f"\nReport written to: {output_path}")
        return 0 if gate_passed else 1


if __name__ == "__main__":
    sys.exit(main())
