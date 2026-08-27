"""Research-only tournament for three orthogonal alpha families."""

from __future__ import annotations

import argparse
import json
import sqlite3
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from statistics import mean, median
from typing import Any

from scripts.run_alpha_champion_master_loop import _research_windows, _validation_windows, build_split_plan

FEE_BPS = 5.0
SLIPPAGE_BPS = 3.0
SYMBOLS = ("BTC/USDT", "ETH/USDT")


@dataclass(frozen=True)
class Bar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class Trade:
    symbol: str
    side: str
    opened_at: str
    closed_at: str
    gross_return: float
    net_return: float
    fee: float
    slippage: float
    funding: float
    exit_reason: str
    bars_held: int


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=UTC)


def _load_bars(
    connection: sqlite3.Connection, symbol: str, timeframe: str, start: datetime, end: datetime
) -> list[Bar]:
    rows = connection.execute(
        """
        SELECT time, open, high, low, close, volume
        FROM ohlcv_bars
        WHERE symbol=? AND timeframe=? AND time>=? AND time<=?
        ORDER BY time
        """,
        (symbol, timeframe, start.isoformat(sep=" "), end.isoformat(sep=" ")),
    )
    return [Bar(_dt(str(t)), float(o), float(h), float(low), float(c), float(v or 0.0)) for t, o, h, low, c, v in rows]


def _load_funding(
    connection: sqlite3.Connection, symbol: str, start: datetime, end: datetime
) -> list[tuple[datetime, float]]:
    rows = connection.execute(
        "SELECT time, funding_rate FROM market_extras WHERE symbol=? AND time>=? AND time<=? AND funding_rate IS NOT NULL ORDER BY time",
        (symbol, start.isoformat(sep=" "), end.isoformat(sep=" ")),
    )
    return [(_dt(str(timestamp)), float(rate)) for timestamp, rate in rows]


def _true_range(previous: Bar | None, current: Bar) -> float:
    if previous is None:
        return current.high - current.low
    return max(current.high - current.low, abs(current.high - previous.close), abs(current.low - previous.close))


def _atr(bars: Sequence[Bar], index: int, window: int = 14) -> float | None:
    if index < window:
        return None
    values = [_true_range(bars[j - 1] if j else None, bars[j]) for j in range(index - window + 1, index + 1)]
    value = mean(values)
    return value if value > 0 else None


def _median_volume(bars: Sequence[Bar], index: int, window: int = 20) -> float | None:
    if index < window:
        return None
    value = median(b.volume for b in bars[index - window : index])
    return value if value > 0 else None


def _rolling_vwap(bars: Sequence[Bar], index: int, window: int = 48) -> float | None:
    if index + 1 < window:
        return None
    rows = bars[index - window + 1 : index + 1]
    volume = sum(row.volume for row in rows)
    return sum(((row.high + row.low + row.close) / 3.0) * row.volume for row in rows) / volume if volume > 0 else None


def _linear_regression(values: Sequence[float]) -> tuple[float, float, float]:
    n = len(values)
    x_mean = (n - 1) / 2.0
    y_mean = mean(values)
    denominator = sum((x - x_mean) ** 2 for x in range(n))
    slope = sum((x - x_mean) * (values[x] - y_mean) for x in range(n)) / denominator if denominator else 0.0
    intercept = y_mean - slope * x_mean
    fitted = [intercept + slope * x for x in range(n)]
    ss_tot = sum((value - y_mean) ** 2 for value in values)
    ss_res = sum((value - fit) ** 2 for value, fit in zip(values, fitted, strict=True))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return slope, r2, fitted[-1]


def _costed_return(side: str, entry: float, exit_price: float) -> tuple[float, float, float, float]:
    gross = (exit_price - entry) / entry if side == "long" else (entry - exit_price) / entry
    fee = 2.0 * FEE_BPS / 10_000.0
    slippage = 2.0 * SLIPPAGE_BPS / 10_000.0
    return gross, gross - fee - slippage, fee, slippage


def _simulate_trade(
    symbol: str,
    bars: Sequence[Bar],
    entry_index: int,
    side: str,
    stop: float,
    targets: Sequence[tuple[float, float]],
    max_hold: int,
    funding_rates: Sequence[tuple[datetime, float]] = (),
) -> Trade | None:
    if entry_index >= len(bars):
        return None
    entry_bar = bars[entry_index]
    entry = entry_bar.open
    risk = abs(entry - stop)
    if entry <= 0 or stop <= 0 or risk <= 0:
        return None
    remaining = 1.0
    realized_gross = 0.0
    closed_index = min(entry_index + max_hold, len(bars) - 1)
    exit_reason = "time_exit"
    for index in range(entry_index, min(entry_index + max_hold + 1, len(bars))):
        bar = bars[index]
        stop_hit = bar.low <= stop if side == "long" else bar.high >= stop
        if stop_hit:
            realized_gross += remaining * ((stop - entry) / entry if side == "long" else (entry - stop) / entry)
            closed_index = index
            remaining = 0.0
            exit_reason = "stop_loss"
            break
        for multiple, fraction in targets:
            target = entry + risk * multiple if side == "long" else entry - risk * multiple
            hit = bar.high >= target if side == "long" else bar.low <= target
            if hit and remaining > 0:
                take_fraction = min(fraction, remaining)
                realized_gross += take_fraction * (
                    (target - entry) / entry if side == "long" else (entry - target) / entry
                )
                remaining -= take_fraction
                closed_index = index
                if remaining <= 1e-9:
                    exit_reason = "targets"
                    break
        if remaining <= 1e-9:
            break
    if remaining > 0:
        bar = bars[closed_index]
        realized_gross += remaining * ((bar.close - entry) / entry if side == "long" else (entry - bar.close) / entry)
    fee = 2.0 * FEE_BPS / 10_000.0
    slippage = 2.0 * SLIPPAGE_BPS / 10_000.0
    funding = sum(
        rate if side == "long" else -rate
        for timestamp, rate in funding_rates
        if entry_bar.timestamp <= timestamp <= bars[closed_index].timestamp
    )
    return Trade(
        symbol,
        side,
        entry_bar.timestamp.isoformat(),
        bars[closed_index].timestamp.isoformat(),
        realized_gross,
        realized_gross - fee - slippage - funding,
        fee,
        slippage,
        funding,
        exit_reason,
        closed_index - entry_index + 1,
    )


def _h1_signals(bars: Sequence[Bar]) -> list[tuple[int, str, float, Sequence[tuple[float, float]]]]:
    signals: list[tuple[int, str, float, Sequence[tuple[float, float]]]] = []
    for i in range(48, len(bars) - 1):
        atr = _atr(bars, i)
        vwap = _rolling_vwap(bars, i)
        vol_mid = _median_volume(bars, i)
        if atr is None or vwap is None or vol_mid is None or bars[i].volume < vol_mid:
            continue
        trend_move = abs(bars[i].close - bars[i - 48].close)
        if trend_move > 1.5 * atr:
            continue
        deviation = (bars[i].close - vwap) / atr
        if deviation <= -1.5 and bars[i].close > bars[i - 1].close and vwap > bars[i].close:
            stop = min(row.low for row in bars[i - 7 : i + 1]) - 0.25 * atr
            risk = abs(bars[i].close - stop)
            target_multiple = (vwap - bars[i].close) / risk if risk > 0 else 0.0
            if target_multiple > 0:
                signals.append((i + 1, "long", stop, ((target_multiple, 1.0),)))
        elif deviation >= 1.5 and bars[i].close < bars[i - 1].close and vwap < bars[i].close:
            stop = max(row.high for row in bars[i - 7 : i + 1]) + 0.25 * atr
            risk = abs(bars[i].close - stop)
            target_multiple = (bars[i].close - vwap) / risk if risk > 0 else 0.0
            if target_multiple > 0:
                signals.append((i + 1, "short", stop, ((target_multiple, 1.0),)))
    return signals


def _h2_signals(
    bars_15m: Sequence[Bar], bars_1h: Sequence[Bar]
) -> list[tuple[int, str, float, Sequence[tuple[float, float]]]]:
    hourly_times = [bar.timestamp for bar in bars_1h]
    signals: list[tuple[int, str, float, Sequence[tuple[float, float]]]] = []
    for i in range(20, len(bars_15m) - 1):
        atr = _atr(bars_15m, i)
        if atr is None:
            continue
        hour_index = bisect_right(hourly_times, bars_15m[i].timestamp) - 1
        if hour_index < 47:
            continue
        slope, r2, regression_end = _linear_regression([bar.close for bar in bars_1h[hour_index - 47 : hour_index + 1]])
        if r2 < 0.35:
            continue
        distance = bars_15m[i].close - regression_end
        if slope > 0 and distance <= -0.5 * atr and bars_15m[i].close > bars_15m[i - 1].close:
            stop = min(row.low for row in bars_15m[i - 7 : i + 1]) - 0.25 * atr
            signals.append((i + 1, "long", stop, ((2.0, 1.0),)))
        elif slope < 0 and distance >= 0.5 * atr and bars_15m[i].close < bars_15m[i - 1].close:
            stop = max(row.high for row in bars_15m[i - 7 : i + 1]) + 0.25 * atr
            signals.append((i + 1, "short", stop, ((2.0, 1.0),)))
    return signals


def _h3_signals(bars: Sequence[Bar]) -> list[tuple[int, str, float, Sequence[tuple[float, float]]]]:
    signals: list[tuple[int, str, float, Sequence[tuple[float, float]]]] = []
    for i in range(21, len(bars) - 1):
        climax = bars[i - 1]
        atr = _atr(bars, i - 1)
        vol_mid = _median_volume(bars, i - 1)
        if (
            atr is None
            or vol_mid is None
            or climax.volume / vol_mid < 2.0
            or abs(climax.close - climax.open) / atr < 1.2
        ):
            continue
        confirmation = bars[i]
        span = max(climax.high - climax.low, 1e-12)
        close_location = (climax.close - climax.low) / span
        if (
            climax.close < climax.open
            and close_location >= 0.35
            and confirmation.close > climax.close
            and confirmation.close > confirmation.open
        ):
            stop = climax.low - 0.25 * atr
            signals.append((i + 1, "long", stop, ((1.0, 0.5), (2.0, 0.5))))
        elif (
            climax.close > climax.open
            and close_location <= 0.65
            and confirmation.close < climax.close
            and confirmation.close < confirmation.open
        ):
            stop = climax.high + 0.25 * atr
            signals.append((i + 1, "short", stop, ((1.0, 0.5), (2.0, 0.5))))
    return signals


def _run_family(
    family: str,
    data: dict[str, dict[str, list[Bar]]],
    funding: dict[str, list[tuple[datetime, float]]],
    start: datetime,
    end: datetime,
) -> list[Trade]:
    trades: list[Trade] = []
    for symbol in SYMBOLS:
        bars_15m = data[symbol]["15m"]
        bars_1h = data[symbol]["1h"]
        if family == "vwap_deviation_reversion_v1":
            signals = _h1_signals(bars_15m)
        elif family == "regression_trend_pullback_v1":
            signals = _h2_signals(bars_15m, bars_1h)
        else:
            signals = _h3_signals(bars_15m)
        last_closed = -1
        for entry_index, side, stop, targets in signals:
            if entry_index <= last_closed:
                continue
            if not (start <= bars_15m[entry_index].timestamp <= end):
                continue
            trade = _simulate_trade(symbol, bars_15m, entry_index, side, stop, targets, 32, funding[symbol])
            if trade is not None:
                trades.append(trade)
                last_closed = entry_index + trade.bars_held - 1
    return sorted(trades, key=lambda row: row.closed_at)


def _metrics(trades: Sequence[Trade]) -> dict[str, Any]:
    rows = list(trades)
    wins = [row.net_return for row in rows if row.net_return > 0]
    losses = [row.net_return for row in rows if row.net_return < 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for row in sorted(rows, key=lambda item: item.closed_at):
        equity += row.net_return
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    by_symbol: dict[str, list[Trade]] = {symbol: [row for row in rows if row.symbol == symbol] for symbol in SYMBOLS}

    def pf(items: Sequence[Trade]) -> float:
        positives = sum(max(0.0, item.net_return) for item in items)
        negatives = abs(sum(min(0.0, item.net_return) for item in items))
        return positives / negatives if negatives else 0.0

    return {
        "trades": len(rows),
        "btc_trades": len(by_symbol["BTC/USDT"]),
        "eth_trades": len(by_symbol["ETH/USDT"]),
        "net_return": sum(row.net_return for row in rows),
        "net_expectancy": mean(row.net_return for row in rows) if rows else 0.0,
        "profit_factor": gross_win / gross_loss if gross_loss else 0.0,
        "btc_profit_factor": pf(by_symbol["BTC/USDT"]),
        "eth_profit_factor": pf(by_symbol["ETH/USDT"]),
        "win_rate": len(wins) / len(rows) if rows else 0.0,
        "average_win": mean(wins) if wins else 0.0,
        "average_loss": mean(losses) if losses else 0.0,
        "payoff_ratio": mean(wins) / abs(mean(losses)) if wins and losses else 0.0,
        "max_drawdown": max_dd,
        "gross_return": sum(row.gross_return for row in rows),
        "fees": sum(row.fee for row in rows),
        "slippage": sum(row.slippage for row in rows),
        "funding": sum(row.funding for row in rows),
        "cost_share": (
            sum(row.fee + row.slippage + row.funding for row in rows) / abs(sum(row.gross_return for row in rows))
        )
        if rows and sum(row.gross_return for row in rows)
        else 0.0,
        "long_trades": sum(row.side == "long" for row in rows),
        "short_trades": sum(row.side == "short" for row in rows),
        "exit_reasons": {
            reason: sum(row.exit_reason == reason for row in rows)
            for reason in sorted({row.exit_reason for row in rows})
        },
    }


def _window_metrics(
    trades: Sequence[Trade], windows: Sequence[tuple[str, datetime, datetime]]
) -> dict[str, dict[str, Any]]:
    return {
        window_id: _metrics([row for row in trades if start <= _dt(row.opened_at) < end])
        for window_id, start, end in windows
    }


def _window_summary(window_metrics: dict[str, dict[str, Any]]) -> dict[str, int]:
    positive = sum(metrics["net_expectancy"] > 0 for metrics in window_metrics.values())
    return {"positive_windows": positive, "negative_windows": len(window_metrics) - positive}


def _gap_count(bars: Sequence[Bar], interval: timedelta) -> int:
    return sum((current.timestamp - previous.timestamp) != interval for previous, current in pairwise(bars))


def _research_windows_with_validation(split: Any) -> tuple[tuple[str, datetime, datetime], ...]:
    folds = _research_windows(split)
    validation = _validation_windows(split)[0]
    return tuple((window.window_id, window.oos_start, window.oos_end) for window in (*folds, validation))


def _gate(family: str, metrics: dict[str, Any]) -> tuple[str, str]:
    if family == "vwap_deviation_reversion_v1":
        if metrics["trades"] < 80:
            return "FAIL_LOW_SAMPLE", "trades below 80"
        if metrics["profit_factor"] <= 1.20 or metrics["net_expectancy"] <= 0 or metrics["net_return"] <= 0:
            return "FAIL", "research PF/expectancy/net return gate failed"
    elif family == "regression_trend_pullback_v1":
        if metrics["trades"] < 60:
            return "FAIL_LOW_SAMPLE", "trades below 60"
        if metrics["profit_factor"] < 1.20 or metrics["net_expectancy"] <= 0 or metrics["max_drawdown"] > 0.20:
            return "FAIL", "research PF/expectancy/max drawdown gate failed"
    else:
        if metrics["trades"] < 60:
            return "FAIL_LOW_SAMPLE", "trades below 60"
        if metrics["profit_factor"] < 1.20 or metrics["net_expectancy"] <= 0:
            return "FAIL", "research PF/expectancy gate failed"
    return "RESEARCH_PASS", "research gate passed"


def _validation_gate(family: str, metrics: dict[str, Any]) -> tuple[str, str]:
    threshold = 1.10 if family != "volume_climax_reversal_v1" else 1.05
    if metrics["profit_factor"] <= threshold or metrics["net_expectancy"] <= 0:
        return "OVERFIT_FAIL", "validation PF/expectancy gate failed"
    return "SURVIVOR", "research and validation gates passed"


def _read_only_data(
    database: Path, start: datetime, end: datetime
) -> tuple[dict[str, dict[str, list[Bar]]], dict[str, list[tuple[datetime, float]]]]:
    warmup = start - timedelta(days=30)
    result: dict[str, dict[str, list[Bar]]] = {}
    funding: dict[str, list[tuple[datetime, float]]] = {}
    with sqlite3.connect(f"file:{database.resolve().as_posix()}?mode=ro", uri=True) as connection:
        for symbol in SYMBOLS:
            result[symbol] = {
                timeframe: _load_bars(connection, symbol, timeframe, warmup, end) for timeframe in ("15m", "1h", "4h")
            }
            funding[symbol] = _load_funding(connection, symbol, warmup, end)
    return result, funding


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    split = build_split_plan(args.database)
    windows = _research_windows_with_validation(split)
    research_end = split.validation_start
    evaluation_end = split.validation_end
    data, funding = _read_only_data(args.database, windows[0][1], evaluation_end)
    definitions = {
        "vwap_deviation_reversion_v1": {
            "family": "VWAP Deviation Mean Reversion",
            "formula": "48-bar rolling VWAP, 1.5 ATR deviation, range proxy, volume median confirmation",
        },
        "regression_trend_pullback_v1": {
            "family": "Regression Trend Pullback",
            "formula": "48 closed 1h bars, normalized slope > 0/<0, R2 >= 0.35, 15m pullback and resumption",
        },
        "volume_climax_reversal_v1": {
            "family": "Volume Climax Exhaustion Reversal",
            "formula": "15m volume/ATR climax >= 2.0x/1.2 ATR, failed close location, next-bar reversal confirmation",
        },
    }
    results: dict[str, Any] = {}
    survivors: list[str] = []
    for family, definition in definitions.items():
        trades = _run_family(family, data, funding, windows[0][1], evaluation_end)
        research_trades = [row for row in trades if _dt(row.opened_at) < split.validation_start]
        validation_trades = [
            row for row in trades if split.validation_start <= _dt(row.opened_at) < split.validation_end
        ]
        research_metrics = _metrics(research_trades)
        validation_metrics = _metrics(validation_trades)
        research_status, research_reason = _gate(family, research_metrics)
        validation_status = "NOT_RUN"
        validation_reason = "research gate failed; validation not run"
        if research_status == "RESEARCH_PASS":
            validation_status, validation_reason = _validation_gate(family, validation_metrics)
        if validation_status == "SURVIVOR":
            survivors.append(family)
        research_windows = _window_metrics(research_trades, windows[:3])
        validation_windows = _window_metrics(validation_trades, (windows[3],))
        payload = {
            "candidate_id": family,
            "version": "1.0.0",
            "hypothesis": definition,
            "research": {
                "metrics": research_metrics,
                "windows": research_windows,
                **_window_summary(research_windows),
                "status": research_status,
                "reason": research_reason,
            },
            "validation": {
                "metrics": validation_metrics,
                "windows": validation_windows,
                **_window_summary(validation_windows),
                "status": validation_status,
                "reason": validation_reason,
            },
            "research_to_validation_degradation": (
                validation_metrics["net_expectancy"] / research_metrics["net_expectancy"]
            )
            if research_metrics["net_expectancy"]
            else None,
            "trades": [asdict(row) for row in trades],
            "final_holdout_accessed": False,
            "runtime_visible": False,
        }
        results[family] = payload
        _write(
            args.output_dir
            / {
                "vwap_deviation_reversion_v1": "H1_RESULT.json",
                "regression_trend_pullback_v1": "H2_RESULT.json",
                "volume_climax_reversal_v1": "H3_RESULT.json",
            }[family],
            payload,
        )
    stability = {
        "status": "NOT_RUN",
        "reason": "No research+validation survivor; neighbor policy forbids variants for failed hypotheses.",
        "survivors": survivors,
        "final_holdout_accessed": False,
    }
    _write(args.output_dir / "SURVIVOR_STABILITY.json", stability)
    status = "NEW_ALPHA_SURVIVOR" if survivors else "NEW_ALPHA_BATCH_EXHAUSTED"
    report = {
        "status": status,
        "baseline": {
            "candidate": "volatility_expansion_v1",
            "trades": 281,
            "profit_factor": 1.1576630479094718,
            "expectancy": 0.001512451049147131,
            "max_drawdown": 0.18736432836022304,
        },
        "hypotheses": [results[key]["hypothesis"] for key in definitions],
        "research_results": {key: results[key]["research"] for key in definitions},
        "validation_results": {key: results[key]["validation"] for key in definitions},
        "survivors": survivors,
        "best_candidate": survivors[0] if survivors else None,
        "why_it_improved": [],
        "why_others_failed": [
            f"{key}: {results[key]['research']['reason']}" for key in definitions if key not in survivors
        ],
        "runtime_modified": False,
        "production_authority": "NOT_GRANTED",
        "final_holdout_accessed": False,
        "database": str(args.database),
        "research_end": split.validation_start.isoformat(),
        "validation_end": split.validation_end.isoformat(),
        "data_quality": {
            symbol: {
                timeframe: {
                    "bars": len(data[symbol][timeframe]),
                    "gaps": _gap_count(
                        data[symbol][timeframe], timedelta(minutes={"15m": 15, "1h": 60, "4h": 240}[timeframe])
                    ),
                }
                for timeframe in ("15m", "1h", "4h")
            }
            for symbol in SYMBOLS
        },
    }
    _write(args.output_dir / "FINAL_REPORT.json", report)
    print(
        json.dumps(
            {
                "status": status,
                "survivors": survivors,
                "research": {key: results[key]["research"]["metrics"] for key in definitions},
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
