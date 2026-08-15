"""Read-only Layer-1 R2 shadow calibration for testnet_sampling_v2.

This replays the frozen sampling signal on persisted 15m OHLCV and varies only
the minimum theoretical net-payoff threshold. It never writes runtime state,
never submits orders, and never changes production configuration.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from services.automated_trading.application.decision_service import (
    BarView,
    TimeframeView,
    atr,
    evaluate_sampling_signal,
    sampling_stop_distance,
)
from services.automated_trading.application.risk_controls import calculate_cost_gate

DB = Path(".local_paper_console.db").resolve()
THRESHOLDS = tuple(Decimal(value) for value in ("1.15", "1.05", "0.95", "0.85"))
COMMISSION_BPS = Decimal("10")
SLIPPAGE_BPS = Decimal("6")
TARGET_R = Decimal(os.environ.get("R2_TARGET_R", "1.5"))
MAX_BARS = 9_000
SIGNAL_WINDOW = 120
MAX_HOLD_BARS = 96


def _dt(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _bar(row: sqlite3.Row) -> BarView:
    return BarView(
        timestamp=_dt(row["time"]),
        open=Decimal(str(row["open"])),
        high=Decimal(str(row["high"])),
        low=Decimal(str(row["low"])),
        close=Decimal(str(row["close"])),
        volume=Decimal(str(row["volume"])),
    )


def _trade_result(
    *, side: str, entry: Decimal, stop_distance: Decimal, bars: list[BarView], start: int
) -> tuple[Decimal, int]:
    stop = entry - stop_distance if side == "LONG" else entry + stop_distance
    target = entry + TARGET_R * stop_distance if side == "LONG" else entry - TARGET_R * stop_distance
    for offset, bar in enumerate(bars[start : start + MAX_HOLD_BARS], start=start):
        stop_hit = bar.low <= stop if side == "LONG" else bar.high >= stop
        target_hit = bar.high >= target if side == "LONG" else bar.low <= target
        if stop_hit and target_hit:
            return Decimal("-1"), offset
        if stop_hit:
            return Decimal("-1"), offset
        if target_hit:
            return TARGET_R, offset
    return Decimal("0"), min(start + MAX_HOLD_BARS - 1, len(bars) - 1)


def _metrics(trades: list[dict]) -> dict:
    if not trades:
        return {"trades": 0, "wins": 0, "win_rate": None, "net_expectancy_r": None, "profit_factor": None, "max_drawdown_r": None}
    net = [Decimal(str(item["net_r"])) for item in trades]
    wins = sum(value > 0 for value in net)
    equity = Decimal("0")
    peak = Decimal("0")
    max_dd = Decimal("0")
    gross_win = Decimal("0")
    gross_loss = Decimal("0")
    for value in net:
        equity += value
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
        if value > 0:
            gross_win += value
        else:
            gross_loss += -value
    return {
        "trades": len(net),
        "wins": wins,
        "win_rate": float(Decimal(wins) / Decimal(len(net))),
        "net_expectancy_r": float(sum(net, Decimal("0")) / Decimal(len(net))),
        "profit_factor": float(gross_win / gross_loss) if gross_loss else None,
        "max_drawdown_r": float(max_dd),
    }


def main() -> int:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    symbols = ["BTC/USDT", "ETH/USDT"]
    all_results: dict[str, dict[str, object]] = {}
    for symbol in symbols:
        rows = con.execute(
            "SELECT time, open, high, low, close, volume FROM ohlcv_bars "
            "WHERE symbol = ? AND timeframe = '15m' ORDER BY time DESC LIMIT ?",
            (symbol, MAX_BARS),
        ).fetchall()
        rows = list(reversed(rows))
        bars = [_bar(row) for row in rows]
        if len(bars) < 200:
            all_results[symbol] = {"status": "INSUFFICIENT_DATA", "bars": len(bars)}
            continue
        candidates: list[dict] = []
        for index in range(100, len(bars) - 1):
            start = max(0, index - SIGNAL_WINDOW + 1)
            view = TimeframeView(timeframe="15m", bars=tuple(bars[start : index + 1]))
            signal = evaluate_sampling_signal(view)
            if signal.side is None:
                continue
            atr14 = atr(view.bars, 14)
            if atr14 is None or atr14 <= 0:
                continue
            reference = bars[index].close
            stop_distance = sampling_stop_distance(atr14=atr14, reference_price=reference)
            entry = bars[index + 1].open
            if entry <= 0:
                continue
            cost_by_threshold = {
                str(threshold): calculate_cost_gate(
                    entry_price=entry,
                    stop_distance=stop_distance,
                    take_profit_distance=TARGET_R * stop_distance,
                    commission_bps=COMMISSION_BPS,
                    slippage_bps=SLIPPAGE_BPS,
                    minimum_net_payoff=threshold,
                )
                for threshold in THRESHOLDS
            }
            gross_r, exit_index = _trade_result(
                side=str(signal.side),
                entry=entry,
                stop_distance=stop_distance,
                bars=bars,
                start=index + 1,
            )
            candidates.append(
                {
                    "index": index,
                    "time": bars[index].timestamp.isoformat(),
                    "side": str(signal.side),
                    "entry": str(entry),
                    "stop_distance": str(stop_distance),
                    "gross_r": str(gross_r),
                    "exit_index": exit_index,
                    "cost": {key: {"cost_r": str(value.cost_r), "payoff": str(value.theoretical_net_payoff), "passed": value.passed} for key, value in cost_by_threshold.items()},
                }
            )
        threshold_results: dict[str, object] = {}
        for threshold in THRESHOLDS:
            key = str(threshold)
            accepted = [item for item in candidates if item["cost"][key]["passed"]]
            trades = []
            next_available = -1
            for item in accepted:
                if int(item["index"]) <= next_available:
                    continue
                cost_r = Decimal(item["cost"][key]["cost_r"])
                gross_r = Decimal(item["gross_r"])
                next_available = int(item["exit_index"])
                trades.append({"time": item["time"], "gross_r": str(gross_r), "net_r": str(gross_r - cost_r)})
            windows: dict[str, list[dict]] = defaultdict(list)
            for trade in trades:
                stamp = _dt(trade["time"])
                windows[stamp.strftime("%Y-%m")].append(trade)
            window_metrics = {window: _metrics(items) for window, items in sorted(windows.items())}
            positive_windows = sum(
                metric["trades"] > 0 and (metric["net_expectancy_r"] or 0) > 0
                for metric in window_metrics.values()
            )
            overall = _metrics(trades)
            threshold_results[key] = {
                "candidate_count": len(candidates),
                "accepted_count": len(accepted),
                "coverage": float(Decimal(len(accepted)) / Decimal(len(candidates))) if candidates else None,
                "overall": overall,
                "oos_windows": window_metrics,
                "positive_windows": positive_windows,
                "window_count": len(window_metrics),
                "stable_positive_oos": bool(
                    len(trades) >= 30
                    and window_metrics
                    and positive_windows >= (len(window_metrics) + 1) // 2
                    and (overall.get("net_expectancy_r") or 0) > 0
                ),
            }
        all_results[symbol] = {
            "status": "OK",
            "bars": len(bars),
            "range": {"first": bars[0].timestamp.isoformat(), "last": bars[-1].timestamp.isoformat()},
            "candidate_count": len(candidates),
            "thresholds": threshold_results,
        }
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "scope": f"testnet_sampling_v2; R2 thresholds with TP={TARGET_R}R",
        "target_r": str(TARGET_R),
        "cost_model": {"commission_bps_round_trip": str(COMMISSION_BPS), "slippage_bps_round_trip": str(SLIPPAGE_BPS)},
        "symbols": all_results,
        "selection": "choose the lowest threshold with stable_positive_oos=true; otherwise NO_STABLE_POSITIVE_OOS and do not modify production",
    }
    print(json.dumps(report, indent=2, default=str))
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
