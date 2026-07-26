"""Cross-sectional funding-rate carry OOS replay.

Validates the cross-sectional basket strategy against historical funding rate
data: rank Top20 by funding rate every rebalance cycle, go short the highest
payers and long the lowest/most negative, hold until next rebalance or rank
dropout, net the funding收益 after transaction costs.

This is the OOS validation gate required before
AUTO_PAPER_CROSS_SECTIONAL_CARRY_RULES.entry_rules.default_enabled_for_auto_trading
can be set to True.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from services.data import DataRepository
from services.data.service import DEFAULT_BINANCE_TOP20

from .metrics import (
    annualized_sharpe,
    deflated_sharpe,
    expectancy,
    max_drawdown_from_pnls,
    profit_factor,
    win_rate,
)


@dataclass(frozen=True)
class CrossSectionalReplayResult:
    """OOS replay summary for the cross-sectional carry strategy."""

    total_rebalance_cycles: int
    total_legs_opened: int
    total_legs_closed: int
    net_pnls: list[float]  # per-leg realized PnL in USDT
    gross_returns: list[float]  # per-leg gross return (before costs)
    net_returns: list[float]  # per-leg net return (after costs)
    sharpe: float
    deflated_sharpe: float
    profit_factor: float
    max_drawdown: float
    expectancy: float
    win_rate: float
    average_fee_bps: float
    average_slippage_bps: float
    start_time: datetime
    end_time: datetime


def run_cross_sectional_replay(
    *,
    data_repo: DataRepository,
    symbols: list[str] | None = None,
    basket_size: int = 3,
    rebalance_hours: int = 8,
    min_estimated_net_edge_bps: float = 5.0,
    fee_bps: float = 5.0,
    slippage_bps: float = 3.0,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> CrossSectionalReplayResult:
    """Run OOS replay of the cross-sectional funding carry strategy.

    Args:
        data_repo: Data repository with OHLCV and funding rate history
        symbols: Symbol universe (defaults to DEFAULT_BINANCE_TOP20)
        basket_size: Number of symbols on each side (short top N, long bottom N)
        rebalance_hours: Hours between rebalance cycles (default 8 = funding settlement)
        min_estimated_net_edge_bps: Minimum net edge after costs to open a leg
        fee_bps: Trading fee per side (default 5.0 = 0.05% taker)
        slippage_bps: Slippage per side (default 3.0 = 0.03%)
        start_time: Replay start (default: earliest available funding data)
        end_time: Replay end (default: latest available funding data)

    Returns:
        CrossSectionalReplayResult with performance metrics
    """
    if symbols is None:
        symbols = list(DEFAULT_BINANCE_TOP20)

    # Fetch funding rate history for all symbols in the universe
    funding_history: dict[str, list[tuple[datetime, float]]] = {}
    for symbol in symbols:
        extras = data_repo.list_market_extras(symbol=symbol, limit=10000)
        if not extras:
            continue
        funding_history[symbol] = [
            (e.timestamp, float(e.funding_rate) * 10_000.0)  # Convert to bps
            for e in extras
            if e.funding_rate is not None
        ]
        funding_history[symbol].sort(key=lambda x: x[0])

    if not funding_history:
        raise ValueError("No funding rate history found for any symbol in universe")

    # Determine replay time range
    all_timestamps = [ts for pairs in funding_history.values() for ts, _ in pairs]
    if not all_timestamps:
        raise ValueError("Empty funding history")

    actual_start = start_time or min(all_timestamps)
    actual_end = end_time or max(all_timestamps)

    # Generate rebalance cycles
    current_cycle = actual_start
    cycles: list[datetime] = []
    while current_cycle <= actual_end:
        cycles.append(current_cycle)
        current_cycle += timedelta(hours=rebalance_hours)

    # Track open positions: {symbol: (side, entry_price, entry_time, entry_funding_bps)}
    open_positions: dict[str, tuple[str, float, datetime, float]] = {}
    net_pnls: list[float] = []
    gross_returns: list[float] = []
    net_returns: list[float] = []
    total_fee_cost = 0.0
    total_slippage_cost = 0.0
    legs_opened = 0
    legs_closed = 0

    for cycle_time in cycles:
        # Rank symbols by funding rate at this cycle
        cycle_funding: list[tuple[str, float]] = []
        for symbol, history in funding_history.items():
            # Find latest funding rate before or at this cycle time
            valid_entries = [(ts, rate) for ts, rate in history if ts <= cycle_time]
            if not valid_entries:
                continue
            _latest_ts, latest_rate = max(valid_entries, key=lambda x: x[0])
            cycle_funding.append((symbol, latest_rate))

        if len(cycle_funding) < 2 * basket_size:
            continue  # Not enough symbols with funding data

        cycle_funding.sort(key=lambda x: x[1], reverse=True)  # Highest funding first

        # Determine target basket: short top basket_size, long bottom basket_size
        target_short = {sym for sym, _ in cycle_funding[:basket_size]}
        target_long = {sym for sym, _ in cycle_funding[-basket_size:]}

        # Close positions that dropped out of basket or reversed side
        symbols_to_close = []
        for symbol, (side, _entry_price, _entry_time, _entry_funding) in list(open_positions.items()):
            side_mismatch = (side == "short" and symbol in target_long) or (side == "long" and symbol in target_short)
            dropped_out = (side == "short" and symbol not in target_short) or (
                side == "long" and symbol not in target_long
            )
            if dropped_out or side_mismatch:
                symbols_to_close.append(symbol)

        for symbol in symbols_to_close:
            side, entry_price, entry_time, entry_funding = open_positions.pop(symbol)
            # Get exit price at this cycle
            bars = data_repo.list_ohlcv_bars(symbol=symbol, timeframe="1h", limit=10000)
            exit_bar = next((b for b in bars if b.timestamp == cycle_time), None)
            if exit_bar is None:
                continue  # Cannot close without exit price

            exit_price = float(exit_bar.close)
            hold_hours = (cycle_time - entry_time).total_seconds() / 3600.0
            funding_cycles = hold_hours / rebalance_hours
            realized_funding_bps = entry_funding * funding_cycles

            if side == "short":
                price_pnl_pct = (entry_price - exit_price) / entry_price
            else:  # long
                price_pnl_pct = (exit_price - entry_price) / entry_price

            gross_return_pct = price_pnl_pct + (realized_funding_bps / 10_000.0)
            round_trip_cost_bps = fee_bps * 2 + slippage_bps * 2  # Open + close
            net_return_pct = gross_return_pct - (round_trip_cost_bps / 10_000.0)

            # Assume unit notional for simplicity (performance is scale-invariant)
            net_pnl = net_return_pct * 1000.0  # 1000 USDT notional per leg
            net_pnls.append(net_pnl)
            gross_returns.append(gross_return_pct)
            net_returns.append(net_return_pct)
            total_fee_cost += fee_bps * 2
            total_slippage_cost += slippage_bps * 2
            legs_closed += 1

        # Open new positions for symbols in target basket but not yet open
        for symbol, funding_bps in cycle_funding:
            if symbol in target_short and symbol not in open_positions:
                # Estimate net edge: funding收益 - costs
                estimated_funding_收益 = abs(funding_bps) * (rebalance_hours / 8.0)  # Per cycle
                estimated_cost = fee_bps * 2 + slippage_bps * 2
                estimated_net_edge = estimated_funding_收益 - estimated_cost
                if estimated_net_edge < min_estimated_net_edge_bps:
                    continue  # Insufficient edge

                bars = data_repo.list_ohlcv_bars(symbol=symbol, timeframe="1h", limit=10000)
                entry_bar = next((b for b in bars if b.timestamp == cycle_time), None)
                if entry_bar is None:
                    continue
                entry_price = float(entry_bar.close)
                open_positions[symbol] = ("short", entry_price, cycle_time, funding_bps)
                legs_opened += 1

            elif symbol in target_long and symbol not in open_positions:
                estimated_funding_收益 = abs(funding_bps) * (rebalance_hours / 8.0)
                estimated_cost = fee_bps * 2 + slippage_bps * 2
                estimated_net_edge = estimated_funding_收益 - estimated_cost
                if estimated_net_edge < min_estimated_net_edge_bps:
                    continue

                bars = data_repo.list_ohlcv_bars(symbol=symbol, timeframe="1h", limit=10000)
                entry_bar = next((b for b in bars if b.timestamp == cycle_time), None)
                if entry_bar is None:
                    continue
                entry_price = float(entry_bar.close)
                open_positions[symbol] = ("long", entry_price, cycle_time, funding_bps)
                legs_opened += 1

    # Close any remaining open positions at end of replay
    final_cycle = cycles[-1] if cycles else actual_end
    for symbol, (side, entry_price, entry_time, entry_funding) in open_positions.items():
        bars = data_repo.list_ohlcv_bars(symbol=symbol, timeframe="1h", limit=10000)
        exit_bar = next((b for b in bars if b.timestamp == final_cycle), None)
        if exit_bar is None:
            continue
        exit_price = float(exit_bar.close)
        hold_hours = (final_cycle - entry_time).total_seconds() / 3600.0
        funding_cycles = hold_hours / rebalance_hours
        realized_funding_bps = entry_funding * funding_cycles

        if side == "short":
            price_pnl_pct = (entry_price - exit_price) / entry_price
        else:
            price_pnl_pct = (exit_price - entry_price) / entry_price

        gross_return_pct = price_pnl_pct + (realized_funding_bps / 10_000.0)
        round_trip_cost_bps = fee_bps * 2 + slippage_bps * 2
        net_return_pct = gross_return_pct - (round_trip_cost_bps / 10_000.0)
        net_pnl = net_return_pct * 1000.0
        net_pnls.append(net_pnl)
        gross_returns.append(gross_return_pct)
        net_returns.append(net_return_pct)
        total_fee_cost += fee_bps * 2
        total_slippage_cost += slippage_bps * 2
        legs_closed += 1

    # Calculate performance metrics
    if not net_returns:
        # No trades executed
        return CrossSectionalReplayResult(
            total_rebalance_cycles=len(cycles),
            total_legs_opened=0,
            total_legs_closed=0,
            net_pnls=[],
            gross_returns=[],
            net_returns=[],
            sharpe=0.0,
            deflated_sharpe=0.0,
            profit_factor=0.0,
            max_drawdown=0.0,
            expectancy=0.0,
            win_rate=0.0,
            average_fee_bps=0.0,
            average_slippage_bps=0.0,
            start_time=actual_start,
            end_time=actual_end,
        )

    periods_per_year = (365.25 * 24) / rebalance_hours  # Rebalance cycles per year
    sharpe_ratio = annualized_sharpe(net_returns, periods_per_year=periods_per_year)
    dsr = deflated_sharpe(sharpe_ratio, net_returns, trials_count=1)
    decimal_returns = [Decimal(str(value)) for value in net_returns]
    pf = profit_factor(decimal_returns)
    max_dd = max_drawdown_from_pnls(decimal_returns, initial_equity=Decimal("1"))
    exp = expectancy(decimal_returns)
    wr = win_rate(decimal_returns)

    avg_fee = total_fee_cost / legs_closed if legs_closed > 0 else 0.0
    avg_slip = total_slippage_cost / legs_closed if legs_closed > 0 else 0.0

    return CrossSectionalReplayResult(
        total_rebalance_cycles=len(cycles),
        total_legs_opened=legs_opened,
        total_legs_closed=legs_closed,
        net_pnls=net_pnls,
        gross_returns=gross_returns,
        net_returns=net_returns,
        sharpe=sharpe_ratio,
        deflated_sharpe=dsr,
        profit_factor=pf,
        max_drawdown=max_dd,
        expectancy=exp,
        win_rate=wr,
        average_fee_bps=avg_fee,
        average_slippage_bps=avg_slip,
        start_time=actual_start,
        end_time=actual_end,
    )
