"""Fast, read-only OOS replay for the two Generation-Next strategy families."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sqlite3
from bisect import bisect_right
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from services.validation.proposal_walk_forward import build_proposal_walk_forward_windows

SYMBOLS = ("BTC/USDT", "ETH/USDT")
DEVELOPMENT_START = datetime(2023, 1, 29, tzinfo=UTC)
FINAL_HOLDOUT_START = datetime(2026, 1, 29, tzinfo=UTC)
TIMEFRAME_SECONDS = {"15m": 900, "1h": 3600, "4h": 14400}
ZERO = Decimal("0")


@dataclass(frozen=True)
class Bar:
    time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


@dataclass(frozen=True)
class Trade:
    family: str
    symbol: str
    side: str
    opened_at: datetime
    closed_at: datetime
    entry: Decimal
    exit: Decimal
    stop: Decimal
    target: Decimal
    net_return: Decimal
    gross_return: Decimal
    reason: str
    window_id: str


def _dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _load_bars(connection: sqlite3.Connection, *, symbol: str, timeframe: str) -> list[Bar]:
    rows = connection.execute(
        "SELECT time, open, high, low, close, volume FROM ohlcv_bars WHERE symbol = ? AND timeframe = ? ORDER BY time",
        (symbol, timeframe),
    ).fetchall()
    return [Bar(_dt(str(row[0])), *(Decimal(str(value)) for value in row[1:])) for row in rows]


def _eligible(bars: list[Bar], *, decision_time: datetime, timeframe: str) -> list[Bar]:
    delta = timedelta(seconds=TIMEFRAME_SECONDS[timeframe])
    return [bar for bar in bars if bar.time + delta <= decision_time]


def _ema(bars: list[Bar], period: int) -> Decimal:
    values = [bar.close for bar in bars[-period - 5 :]]
    multiplier = Decimal("2") / Decimal(period + 1)
    result = values[0]
    for value in values[1:]:
        result = result + (value - result) * multiplier
    return result


def _atr(bars: list[Bar]) -> Decimal:
    sample = bars[-15:]
    ranges = []
    for index, bar in enumerate(sample):
        previous = sample[index - 1].close if index else bar.open
        ranges.append(max(bar.high - bar.low, abs(bar.high - previous), abs(bar.low - previous)))
    return sum(ranges[-14:], ZERO) / Decimal(min(14, len(ranges)))


def _htf_direction(bars: list[Bar], lookback: int = 12) -> str:
    sample = bars[-lookback:]
    if len(sample) < lookback:
        return "none"
    half = lookback // 2
    first, second = sample[:half], sample[half:]
    if max(x.high for x in second) > max(x.high for x in first) and min(x.low for x in second) > min(
        x.low for x in first
    ):
        return "long"
    if min(x.low for x in second) < min(x.low for x in first) and max(x.high for x in second) < max(
        x.high for x in first
    ):
        return "short"
    return "none"


def _proposal(
    family: str, *, side: str, entry: Decimal, stop: Decimal, target_rr: Decimal, now: datetime, window_id: str
) -> tuple[str, Decimal, Decimal, Decimal, datetime, str] | None:
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    target = entry + risk * target_rr if side == "long" else entry - risk * target_rr
    return family, entry, stop, target, now, side


def _htf_candidate(
    *,
    bars15: list[Bar],
    bars1h: list[Bar],
    bars4h: list[Bar],
    decision_time: datetime,
    window_id: str,
    target_rr: Decimal = Decimal("2.0"),
    buffer: Decimal = Decimal("0.25"),
) -> tuple[str, Decimal, Decimal, Decimal, datetime, str] | None:
    if len(bars15) < 12 or len(bars1h) < 60 or len(bars4h) < 12:
        return None
    atr = _atr(bars15)
    if atr <= 0:
        return None
    direction = _htf_direction(bars4h)
    fast, slow = _ema(bars1h, 20), _ema(bars1h, 50)
    previous_fast = _ema(bars1h[:-1], 20)
    trend_long = direction == "long" and fast > slow and fast >= previous_fast
    trend_short = direction == "short" and fast < slow and fast <= previous_fast
    impulse, pullback, confirmation = bars15[-3:]
    ratio = abs(impulse.close - impulse.open) / atr
    volume_mean = sum((bar.volume for bar in bars15[-12:-4]), ZERO) / Decimal("8")
    if ratio < Decimal("0.45") or ratio > Decimal("2.2") or impulse.volume < volume_mean * Decimal("1.05"):
        return None
    long = (
        trend_long
        and impulse.close > impulse.open
        and pullback.close <= impulse.close
        and pullback.low >= impulse.low - atr
        and confirmation.close > pullback.high
        and confirmation.close > confirmation.open
    )
    short = (
        trend_short
        and impulse.close < impulse.open
        and pullback.close >= impulse.close
        and pullback.high <= impulse.high + atr
        and confirmation.close < pullback.low
        and confirmation.close < confirmation.open
    )
    if long == short:
        return None
    side = "long" if long else "short"
    stop = pullback.low - atr * buffer if long else pullback.high + atr * buffer
    return _proposal(
        "htf_trend_continuation_v1",
        side=side,
        entry=confirmation.close,
        stop=stop,
        target_rr=target_rr,
        now=decision_time,
        window_id=window_id,
    )


def _breakout_candidate(
    *,
    bars15: list[Bar],
    bars1h: list[Bar],
    bars4h: list[Bar],
    decision_time: datetime,
    window_id: str,
    target_rr: Decimal = Decimal("2.0"),
    buffer: Decimal = Decimal("0.25"),
) -> tuple[str, Decimal, Decimal, Decimal, datetime, str] | None:
    if len(bars15) < 24 or len(bars1h) < 20 or len(bars4h) < 10:
        return None
    atr = _atr(bars15)
    if atr <= 0:
        return None
    prior = bars15[-23:-3]
    high_level, low_level = max(x.high for x in prior), min(x.low for x in prior)
    breakout, retest, confirmation = bars15[-3:]
    mean_volume = sum((bar.volume for bar in prior), ZERO) / Decimal(len(prior))
    if breakout.volume < mean_volume * Decimal("1.15"):
        return None
    long = (
        breakout.close > high_level + atr * Decimal("0.15")
        and retest.low <= high_level + atr * Decimal("0.35")
        and retest.close > high_level
        and confirmation.close > retest.high
    )
    short = (
        breakout.close < low_level - atr * Decimal("0.15")
        and retest.high >= low_level - atr * Decimal("0.35")
        and retest.close < low_level
        and confirmation.close < retest.low
    )
    if long == short:
        return None
    side = "long" if long else "short"
    stop = retest.low - atr * buffer if long else retest.high + atr * buffer
    return _proposal(
        "breakout_retest_v1",
        side=side,
        entry=confirmation.close,
        stop=stop,
        target_rr=target_rr,
        now=decision_time,
        window_id=window_id,
    )


def _volatility_candidate(
    *,
    bars15: list[Bar],
    bars1h: list[Bar],
    bars4h: list[Bar],
    decision_time: datetime,
    window_id: str,
    target_rr: Decimal = Decimal("2.0"),
    buffer: Decimal = Decimal("0.25"),
) -> tuple[str, Decimal, Decimal, Decimal, datetime, str] | None:
    """Generation 3: compressed range followed by confirmed volatility expansion."""
    if len(bars15) < 13 or len(bars1h) < 20 or len(bars4h) < 10:
        return None
    atr = _atr(bars15)
    if atr <= 0:
        return None
    compression = bars15[-9:-3]
    breakout, confirmation = bars15[-2:]
    compressed_range = max(bar.high for bar in compression) - min(bar.low for bar in compression)
    if compressed_range > atr * Decimal("6") * Decimal("0.80"):
        return None
    level_high, level_low = max(bar.high for bar in compression), min(bar.low for bar in compression)
    body = abs(breakout.close - breakout.open)
    average_volume = sum((bar.volume for bar in compression), ZERO) / Decimal(len(compression))
    if body < atr * Decimal("0.80") or breakout.volume < average_volume * Decimal("1.20"):
        return None
    direction = _htf_direction(bars4h, lookback=10)
    fast, slow = _ema(bars1h, 20), _ema(bars1h, 50)
    long = (
        breakout.close > level_high + atr * Decimal("0.10")
        and confirmation.close > breakout.close
        and direction != "short"
        and fast >= slow
    )
    short = (
        breakout.close < level_low - atr * Decimal("0.10")
        and confirmation.close < breakout.close
        and direction != "long"
        and fast <= slow
    )
    if long == short:
        return None
    side = "long" if long else "short"
    stop = breakout.low - atr * buffer if long else breakout.high + atr * buffer
    return _proposal(
        "volatility_expansion_v1",
        side=side,
        entry=confirmation.close,
        stop=stop,
        target_rr=target_rr,
        now=decision_time,
        window_id=window_id,
    )


def _donchian_candidate(
    *,
    bars15: list[Bar],
    bars1h: list[Bar],
    bars4h: list[Bar],
    decision_time: datetime,
    window_id: str,
    target_rr: Decimal = Decimal("2.2"),
    buffer: Decimal = Decimal("0.25"),
) -> tuple[str, Decimal, Decimal, Decimal, datetime, str] | None:
    if len(bars15) < 36 or len(bars1h) < 20 or len(bars4h) < 10:
        return None
    atr = _atr(bars15)
    channel = bars15[-35:-3]
    breakout, retest, confirmation = bars15[-3:]
    high, low = max(bar.high for bar in channel), min(bar.low for bar in channel)
    average_volume = sum((bar.volume for bar in channel), ZERO) / Decimal(len(channel))
    direction = _htf_direction(bars4h, lookback=10)
    fast, slow = _ema(bars1h, 20), _ema(bars1h, 50)
    long = (
        breakout.close > high
        and breakout.volume >= average_volume * Decimal("1.10")
        and retest.low <= high + atr * Decimal("0.25")
        and retest.close > high
        and confirmation.close > retest.high
        and direction != "short"
        and fast >= slow
    )
    short = (
        breakout.close < low
        and breakout.volume >= average_volume * Decimal("1.10")
        and retest.high >= low - atr * Decimal("0.25")
        and retest.close < low
        and confirmation.close < retest.low
        and direction != "long"
        and fast <= slow
    )
    if long == short:
        return None
    side = "long" if long else "short"
    stop = retest.low - atr * buffer if long else retest.high + atr * buffer
    return _proposal(
        "donchian_breakout_retest_v1",
        side=side,
        entry=confirmation.close,
        stop=stop,
        target_rr=target_rr,
        now=decision_time,
        window_id=window_id,
    )


def _momentum_candidate(
    *,
    bars15: list[Bar],
    bars1h: list[Bar],
    bars4h: list[Bar],
    decision_time: datetime,
    window_id: str,
    target_rr: Decimal = Decimal("2.0"),
    buffer: Decimal = Decimal("0.25"),
) -> tuple[str, Decimal, Decimal, Decimal, datetime, str] | None:
    if len(bars15) < 12 or len(bars1h) < 55 or len(bars4h) < 10:
        return None
    atr = _atr(bars15)
    momentum = bars15[-4:-1]
    confirmation = bars15[-1]
    move = momentum[-1].close - momentum[0].open
    avg_volume = sum((bar.volume for bar in bars15[-12:-4]), ZERO) / Decimal("8")
    fast = sum((bar.close for bar in bars1h[-20:]), ZERO) / Decimal("20")
    slow = sum((bar.close for bar in bars1h[-50:]), ZERO) / Decimal("50")
    long = move >= atr * Decimal("0.90") and confirmation.close > momentum[-1].high and fast > slow
    short = move <= -atr * Decimal("0.90") and confirmation.close < momentum[-1].low and fast < slow
    if long == short or confirmation.volume < avg_volume:
        return None
    side = "long" if long else "short"
    stop = min(bar.low for bar in momentum) - atr * buffer if long else max(bar.high for bar in momentum) + atr * buffer
    return _proposal(
        "momentum_continuation_v1",
        side=side,
        entry=confirmation.close,
        stop=stop,
        target_rr=target_rr,
        now=decision_time,
        window_id=window_id,
    )


def _simulate(
    *,
    bars: list[Bar],
    proposal: tuple[str, Decimal, Decimal, Decimal, datetime, str],
    window_id: str,
    cost_bps_side: Decimal,
) -> Trade:
    family, reference, stop_ref, target_ref, signal_time, side = proposal
    index = next(index for index, bar in enumerate(bars) if bar.time + timedelta(minutes=15) == signal_time)
    fill_bar = bars[index + 1]
    impact = cost_bps_side / Decimal("10000")
    entry = fill_bar.open * (Decimal("1") + impact if side == "long" else Decimal("1") - impact)
    risk = abs(reference - stop_ref)
    stop = entry - risk if side == "long" else entry + risk
    target = (
        entry + risk * (target_ref - reference) / max(risk, Decimal("0.00000001"))
        if side == "long"
        else entry - risk * (reference - target_ref) / max(risk, Decimal("0.00000001"))
    )
    exit_price, closed_at, reason = None, None, "end_of_window"
    for bar in bars[index + 1 :]:
        stop_hit = bar.low <= stop if side == "long" else bar.high >= stop
        target_hit = bar.high >= target if side == "long" else bar.low <= target
        if stop_hit:
            exit_price, closed_at, reason = stop, bar.time, "stop_loss"
            break
        if target_hit:
            exit_price, closed_at, reason = target, bar.time, "take_profit"
            break
    if exit_price is None:
        last = bars[-1]
        exit_price, closed_at = last.close, last.time
    assert closed_at is not None
    gross = ((exit_price - entry) / entry if side == "long" else (entry - exit_price) / entry) * Decimal("0.85")
    total_cost = Decimal("0.85") * Decimal("2") * cost_bps_side / Decimal("10000")
    return Trade(
        family,
        "",
        side,
        fill_bar.time,
        closed_at,
        entry,
        exit_price,
        stop,
        target,
        gross - total_cost,
        gross,
        reason,
        window_id,
    )


def _bootstrap_lcb(returns: list[Decimal], *, seed: int = 17) -> float | None:
    if len(returns) < 40:
        return None
    rng = random.Random(seed)
    block = max(2, min(8, len(returns) // 10))
    means: list[float] = []
    for _ in range(1000):
        sample: list[Decimal] = []
        while len(sample) < len(returns):
            start = rng.randrange(0, len(returns))
            sample.extend(returns[start : start + block])
        means.append(float(sum(sample[: len(returns)], ZERO) / Decimal(len(returns))))
    means.sort()
    return means[49]


def _metrics(trades: list[Trade]) -> dict[str, Any]:
    returns = [trade.net_return for trade in trades]
    wins = [value for value in returns if value > 0]
    losses = [value for value in returns if value < 0]
    equity, peak, max_dd = Decimal("1"), Decimal("1"), ZERO
    for value in returns:
        equity += value
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak if peak > 0 else ZERO)
    return {
        "trades": len(trades),
        "return": str(sum(returns, ZERO)),
        "PF": str(sum(wins, ZERO) / abs(sum(losses, ZERO))) if losses else None,
        "expectancy": str(sum(returns, ZERO) / Decimal(len(returns))) if returns else None,
        "LCB": _bootstrap_lcb(returns),
        "max_DD": str(max_dd),
        "win_rate": str(Decimal(len(wins)) / Decimal(len(returns))) if returns else None,
    }


def run(*, database: Path, output: Path, generation: int, target_rr: Decimal, stop_buffer: Decimal) -> dict[str, Any]:
    windows = build_proposal_walk_forward_windows(
        development_start=DEVELOPMENT_START, development_end=FINAL_HOLDOUT_START
    )
    results: dict[str, Any] = {}
    with sqlite3.connect(f"file:{database.resolve().as_posix()}?mode=ro", uri=True) as connection:
        series = {
            symbol: {
                timeframe: _load_bars(connection, symbol=symbol, timeframe=timeframe) for timeframe in TIMEFRAME_SECONDS
            }
            for symbol in SYMBOLS
        }
        timestamps = {
            symbol: {timeframe: [bar.time for bar in series[symbol][timeframe]] for timeframe in TIMEFRAME_SECONDS}
            for symbol in SYMBOLS
        }
        for family, builder in (
            ("htf_trend_continuation_v1", _htf_candidate),
            ("breakout_retest_v1", _breakout_candidate),
            ("volatility_expansion_v1", _volatility_candidate),
            ("donchian_breakout_retest_v1", _donchian_candidate),
            ("momentum_continuation_v1", _momentum_candidate),
        ):
            all_trades: list[Trade] = []
            window_payload: dict[str, Any] = {}
            for window in windows:
                window_trades: list[Trade] = []
                for symbol in SYMBOLS:
                    bars15_all = series[symbol]["15m"]
                    bars1h_all = series[symbol]["1h"]
                    bars4h_all = series[symbol]["4h"]
                    bars15 = [bar for bar in bars15_all if bar.time + timedelta(minutes=15) < window.oos_end]
                    index = next(
                        (
                            offset
                            for offset, bar in enumerate(bars15)
                            if bar.time + timedelta(minutes=15) >= window.oos_start
                        ),
                        4,
                    )
                    while index < len(bars15) - 1:
                        signal_bar = bars15[index]
                        decision_time = signal_bar.time + timedelta(minutes=15)
                        context15 = bars15[: index + 1]
                        context1h = bars1h_all[
                            : bisect_right(timestamps[symbol]["1h"], decision_time - timedelta(hours=1))
                        ]
                        context4h = bars4h_all[
                            : bisect_right(timestamps[symbol]["4h"], decision_time - timedelta(hours=4))
                        ]
                        proposal = builder(
                            bars15=context15,
                            bars1h=context1h,
                            bars4h=context4h,
                            decision_time=decision_time,
                            window_id=window.window_id,
                            target_rr=target_rr,
                            buffer=stop_buffer,
                        )
                        if proposal is None:
                            index += 1
                            continue
                        trade = _simulate(
                            bars=bars15[index:],
                            proposal=proposal,
                            window_id=window.window_id,
                            cost_bps_side=Decimal("7"),
                        )
                        trade = Trade(
                            trade.family,
                            symbol,
                            trade.side,
                            trade.opened_at,
                            trade.closed_at,
                            trade.entry,
                            trade.exit,
                            trade.stop,
                            trade.target,
                            trade.net_return,
                            trade.gross_return,
                            trade.reason,
                            trade.window_id,
                        )
                        window_trades.append(trade)
                        close_index = next(
                            (
                                offset
                                for offset, bar in enumerate(bars15[index:], start=index)
                                if bar.time == trade.closed_at
                            ),
                            index,
                        )
                        index = max(index + 1, close_index + 1)
                    all_trades.extend(window_trades)
                    window_payload.setdefault(window.window_id, {})[symbol] = _metrics(window_trades)
            metrics = _metrics(all_trades)
            portfolio_windows = []
            for window in windows:
                returns = []
                for symbol in SYMBOLS:
                    returns.extend(
                        [
                            trade.net_return
                            for trade in all_trades
                            if trade.window_id == window.window_id and trade.symbol == symbol
                        ]
                    )
                portfolio_windows.append(sum(returns, ZERO) / Decimal(len(returns)) if returns else ZERO)
            metrics["positive_windows"] = sum(value > 0 for value in portfolio_windows)
            results[family] = {
                "generation": generation,
                "parameters": {"target_rr": str(target_rr), "stop_buffer_atr": str(stop_buffer)},
                "portfolio": metrics,
                "windows": window_payload,
                "trades": [trade.__dict__ for trade in all_trades],
            }
    output.mkdir(parents=True, exist_ok=True)
    source_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    payload: dict[str, Any] = {
        "status": "GENERATION_RESEARCH_COMPLETE",
        "generation": generation,
        "source_hash": source_hash,
        "results": results,
        "cost_scenarios": {},
    }
    for multiplier in (Decimal("1"), Decimal("1.5"), Decimal("2")):
        for family in results:
            stressed = []
            for trade in results[family]["trades"]:
                gross = Decimal(str(trade["gross_return"]))
                stressed.append(gross - Decimal("0.85") * Decimal("2") * Decimal("7") * multiplier / Decimal("10000"))
            payload["cost_scenarios"].setdefault(family, {})[str(multiplier)] = _metrics(
                [
                    Trade(
                        family,
                        "",
                        "long",
                        datetime.now(UTC),
                        datetime.now(UTC),
                        ZERO,
                        ZERO,
                        ZERO,
                        ZERO,
                        value,
                        value,
                        "",
                        "",
                    )
                    for value in stressed
                ]
            )
    (output / "generation-report.json").write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=Path(".strategy_refactor_history.db"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--generation", type=int, default=1)
    parser.add_argument("--target-rr", type=Decimal, default=Decimal("2.0"))
    parser.add_argument("--stop-buffer", type=Decimal, default=Decimal("0.25"))
    args = parser.parse_args()
    payload = run(
        database=args.database,
        output=args.output,
        generation=args.generation,
        target_rr=args.target_rr,
        stop_buffer=args.stop_buffer,
    )
    print(
        json.dumps(
            {family: result["portfolio"] for family, result in payload["results"].items()}, indent=2, default=str
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
