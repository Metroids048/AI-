"""Research-only cost challenger for the existing strict D16 sampling control."""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from scripts.analyze_active_sampling_trade_context import _as_view, _fast_sampling_side, _trend_aligned
from services.automated_trading.application.decision_service import TimeframeView, atr, sampling_stop_distance
from services.research.exit_policy_shadow.loader import load_bars

DB_PATH = Path(".strategy_refactor_history.db").resolve()
START = datetime(2023, 1, 1, tzinfo=UTC)
END = datetime(2026, 7, 29, 23, 59, tzinfo=UTC)
ROUND_TRIP_COMMISSION_RATE = Decimal("0.0008")
OBSERVED_TRIGGER_TO_FILL_R = Decimal("-0.174646953215032985787687271")
FLOOR_RISK_PCT = Decimal("0.0035")


class _OneMinuteStream:
    def __init__(self, db_path: Path, *, symbol: str) -> None:
        self._connection = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
        self._cursor = self._connection.execute(
            "SELECT time, high, low FROM ohlcv_bars WHERE symbol = ? AND timeframe = '1m' ORDER BY time",
            (symbol,),
        )
        self._current: tuple[datetime, Decimal, Decimal] | None = None
        self._advance()

    def _advance(self) -> None:
        row = self._cursor.fetchone()
        if row is None:
            self._current = None
            return
        timestamp = datetime.fromisoformat(str(row[0]).replace(" ", "T")).replace(tzinfo=UTC)
        self._current = (timestamp, Decimal(str(row[1])), Decimal(str(row[2])))

    def replay(
        self, *, start: datetime, side: str, entry: Decimal, risk: Decimal
    ) -> tuple[Decimal, datetime | None, bool]:
        while self._current is not None and self._current[0] < start:
            self._advance()
        stop = entry - risk if side == "long" else entry + risk
        target = entry + Decimal("1.5") * risk if side == "long" else entry - Decimal("1.5") * risk
        mfe = Decimal("0")
        while self._current is not None:
            timestamp, high, low = self._current
            favorable = high - entry if side == "long" else entry - low
            mfe = max(mfe, favorable)
            stop_hit = low <= stop if side == "long" else high >= stop
            target_hit = high >= target if side == "long" else low <= target
            self._advance()
            if stop_hit or target_hit:
                exit_price = stop if stop_hit else target
                gross_r = (exit_price - entry) / risk if side == "long" else (entry - exit_price) / risk
                return gross_r, timestamp, mfe >= Decimal("0.5") * risk
        return Decimal("0"), None, mfe >= Decimal("0.5") * risk

    def close(self) -> None:
        self._connection.close()


def _floor_15m(timestamp: datetime) -> datetime:
    return timestamp.replace(minute=(timestamp.minute // 15) * 15, second=0, microsecond=0)


def _quantile(values: list[Decimal], percentile: Decimal) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[int((Decimal(len(ordered) - 1) * percentile).to_integral_value())]


def _bootstrap_lcb95(values: list[Decimal], *, seed: int = 7, samples: int = 1000) -> Decimal | None:
    if not values:
        return None
    rng = random.Random(seed)
    count = len(values)
    means = [
        sum((values[rng.randrange(count)] for _ in range(count)), Decimal("0")) / Decimal(count) for _ in range(samples)
    ]
    return _quantile(means, Decimal("0.05"))


def _metrics(
    trades: list[dict[str, Any]], *, candidates: int, signals_seen: int, stress_multiplier: Decimal
) -> dict[str, Any]:
    stressed = [
        trade["gross_r"] - stress_multiplier * trade["commission_r"] + stress_multiplier * trade["trigger_to_fill_r"]
        for trade in trades
    ]
    wins = [value for value in stressed if value > 0]
    losses = [value for value in stressed if value < 0]
    equity = Decimal("0")
    peak = Decimal("0")
    max_drawdown = Decimal("0")
    for value in stressed:
        equity += value
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
    expectancy = sum(stressed, Decimal("0")) / Decimal(len(stressed)) if stressed else None
    variance = None
    if expectancy is not None and len(stressed) > 1:
        variance = sum((value - expectancy) ** 2 for value in stressed) / Decimal(len(stressed) - 1)
    trade_sharpe = (
        None if variance is None or variance <= 0 else expectancy / variance.sqrt() * Decimal(len(stressed)).sqrt()
    )
    profit_factor = None if not losses else sum(wins, Decimal("0")) / abs(sum(losses, Decimal("0")))
    lcb95 = _bootstrap_lcb95(stressed)
    return {
        "signals_seen": signals_seen,
        "candidates": candidates,
        "trades": len(stressed),
        "net_r": str(sum(stressed, Decimal("0"))),
        "expectancy_r": None if expectancy is None else str(expectancy),
        "profit_factor": None if profit_factor is None else str(profit_factor),
        "max_drawdown_r": str(max_drawdown),
        "trade_sharpe": None if trade_sharpe is None else str(trade_sharpe),
        "lcb95_expectancy_r": None if lcb95 is None else str(lcb95),
        "direction_hit_rate": None
        if not trades
        else str(Decimal(sum(trade["direction_hit"] for trade in trades)) / Decimal(len(trades))),
        "cost_multiplier": str(stress_multiplier),
    }


def _replay_symbol(
    *, symbol: str, bars_15m: list[Any], bars_4h: list[Any], bars_1h: list[Any], atr_native_only: bool
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    outcomes: dict[str, list[dict[str, Any]]] = {"development": [], "oos": []}
    candidates = {"development": 0, "oos": 0}
    signals_seen = {"development": 0, "oos": 0}
    split_at = bars_15m[int(len(bars_15m) * Decimal("0.7"))].time
    trend_index = 0
    secondary_trend_index = 0
    active_until: datetime | None = None
    final_signal_index = len(bars_15m) - 3
    stream = _OneMinuteStream(DB_PATH, symbol=symbol)
    try:
        for index in range(100, final_signal_index):
            decision_bar = bars_15m[index]
            if active_until is not None and decision_bar.time <= active_until:
                continue
            while trend_index + 1 < len(bars_4h) and bars_4h[trend_index + 1].time <= decision_bar.time - timedelta(
                hours=4
            ):
                trend_index += 1
            while secondary_trend_index + 1 < len(bars_1h) and bars_1h[
                secondary_trend_index + 1
            ].time <= decision_bar.time - timedelta(hours=1):
                secondary_trend_index += 1
            view = TimeframeView("15m", tuple(_as_view(bar) for bar in bars_15m[index - 100 : index + 1]))
            side = _fast_sampling_side(view)
            if (
                side is None
                or not _trend_aligned(side, bars_4h[: trend_index + 1])
                or not _trend_aligned(side, bars_1h[: secondary_trend_index + 1])
            ):
                continue
            confirmation_view: TimeframeView | None = None
            for offset in (1, 2):
                confirmation_end = index + offset + 1
                confirmation_view = TimeframeView(
                    "15m", tuple(_as_view(bar) for bar in bars_15m[confirmation_end - 101 : confirmation_end])
                )
                if _fast_sampling_side(confirmation_view) != side:
                    break
            else:
                assert confirmation_view is not None
                decision_bar = bars_15m[index + 2]
                entry_bar = bars_15m[index + 3]
                view = confirmation_view
                cohort = "development" if decision_bar.time < split_at else "oos"
                signals_seen[cohort] += 1
                atr14 = atr(view.bars, 14)
                if atr14 is None or atr14 <= 0:
                    continue
                entry = entry_bar.open
                risk = sampling_stop_distance(atr14=atr14, reference_price=entry)
                atr_native = Decimal("1.2") * atr14 > entry * FLOOR_RISK_PCT
                if risk <= 0 or (atr_native_only and not atr_native):
                    continue
                candidates[cohort] += 1
                gross_r, exit_time, direction_hit = stream.replay(
                    start=entry_bar.time, side=side, entry=entry, risk=risk
                )
                if exit_time is None:
                    active_until = END
                    continue
                commission_r = entry * ROUND_TRIP_COMMISSION_RATE / risk
                trade = {
                    "net_return": str(gross_r - commission_r + OBSERVED_TRIGGER_TO_FILL_R),
                    "gross_r": gross_r,
                    "commission_r": commission_r,
                    "trigger_to_fill_r": OBSERVED_TRIGGER_TO_FILL_R,
                    "direction_hit": direction_hit,
                    "symbol": symbol,
                }
                outcomes[cohort].append(trade)
                active_until = _floor_15m(exit_time)
    finally:
        stream.close()
    result = {
        cohort: _metrics(
            outcomes[cohort],
            candidates=candidates[cohort],
            signals_seen=signals_seen[cohort],
            stress_multiplier=Decimal("1"),
        )
        for cohort in outcomes
    }
    return result, outcomes["oos"]


def _hard_gate(metrics: dict[str, Any]) -> bool:
    return (
        metrics["trades"] > 0
        and Decimal(str(metrics["expectancy_r"])) >= Decimal("0.10")
        and Decimal(str(metrics["profit_factor"])) >= Decimal("1.40")
        and Decimal(str(metrics["lcb95_expectancy_r"])) > 0
    )


def evaluate(*, db_path: Path = DB_PATH) -> dict[str, Any]:
    global DB_PATH
    DB_PATH = db_path.resolve()
    policies = {"D16_STRICT_CONTROL": False, "ATR_NATIVE_ONLY_FILTER": True}
    result: dict[str, Any] = {
        "scope": "research-only strict D16 control versus ATR-native selection; Final Holdout not accessed",
        "split": "70/30 chronological development/OOS independently per BTC/USDT and ETH/USDT",
        "fidelity": "1m stop/target replay; conservative same-minute stop precedence",
        "frozen": [
            "signal",
            "side filter",
            "P1",
            "TP=1.5R",
            "R2=1.15",
            "position risk semantics",
            "execution implementation",
        ],
        "changed_variable": "ATR_NATIVE_ONLY_FILTER: accept only entries where existing 1.2*ATR14 branch exceeds the existing 0.35% floor",
        "cost_model": {
            "observed_commission_rate_per_side": "0.0004",
            "round_trip_commission_rate": str(ROUND_TRIP_COMMISSION_RATE),
            "observed_mean_trigger_to_fill_r": str(OBSERVED_TRIGGER_TO_FILL_R),
            "unexplained_residual_excluded": "-1.291989096038500326295248051R / 30 episodes; not safely attributable to a challenger",
        },
        "results": {},
        "stress_1_5x": {},
        "promotion": "REJECTED_RESEARCH_ONLY",
    }
    oos_by_policy: dict[str, list[dict[str, Any]]] = {key: [] for key in policies}
    for symbol in ("BTC/USDT", "ETH/USDT"):
        bars_15m = load_bars(DB_PATH, symbol=symbol, timeframe="15m", start=START, end=END)
        bars_4h = load_bars(DB_PATH, symbol=symbol, timeframe="4h", start=START, end=END)
        bars_1h = load_bars(DB_PATH, symbol=symbol, timeframe="1h", start=START, end=END)
        if not bars_15m or not bars_4h or not bars_1h:
            raise RuntimeError(f"missing historical bars for {symbol}")
        for policy, atr_native_only in policies.items():
            details, oos_trades = _replay_symbol(
                symbol=symbol, bars_15m=bars_15m, bars_4h=bars_4h, bars_1h=bars_1h, atr_native_only=atr_native_only
            )
            result["results"].setdefault(policy, {})[symbol] = details
            oos_by_policy[policy].extend(oos_trades)
    for policy, trades in oos_by_policy.items():
        combined = _metrics(trades, candidates=len(trades), signals_seen=len(trades), stress_multiplier=Decimal("1"))
        stress = _metrics(trades, candidates=len(trades), signals_seen=len(trades), stress_multiplier=Decimal("1.5"))
        result["results"][policy]["COMBINED_OOS"] = combined
        result["stress_1_5x"][policy] = stress
    result["promotion"] = (
        "PROMOTION_GATE_PASSED"
        if all(
            _hard_gate(result["results"]["ATR_NATIVE_ONLY_FILTER"][symbol]["oos"])
            for symbol in ("BTC/USDT", "ETH/USDT")
        )
        else "REJECTED_RESEARCH_ONLY"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DB_PATH)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/active_strategy_optimization/atr_native_only_cost_calibrated_20260816.json"),
    )
    args = parser.parse_args()
    report = evaluate(db_path=args.database)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    print(report["promotion"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
