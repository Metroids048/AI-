"""Read-only 1m fidelity replay for the accepted strict two-confirmation candidate."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from scripts.analyze_active_sampling_trade_context import (
    _as_view,
    _fast_sampling_side,
    _trade_metrics,
    _trend_aligned,
)
from services.automated_trading.application.decision_service import TimeframeView, atr, sampling_stop_distance
from services.research.exit_policy_shadow.loader import load_bars

DB_PATH = Path(".strategy_refactor_history.db").resolve()
START = datetime(2023, 1, 1, tzinfo=UTC)
END = datetime(2026, 7, 29, 23, 59, tzinfo=UTC)


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
                cost_r = entry * Decimal("0.0011") / risk
                return gross_r - cost_r, timestamp, mfe >= Decimal("0.5") * risk
        return Decimal("0"), None, mfe >= Decimal("0.5") * risk

    def close(self) -> None:
        self._connection.close()


def _floor_15m(timestamp: datetime) -> datetime:
    return timestamp.replace(minute=(timestamp.minute // 15) * 15, second=0, microsecond=0)


def _replay_symbol(
    *,
    symbol: str,
    bars_15m: list[Any],
    bars_4h: list[Any],
    bars_1h: list[Any],
    strict_alignment: bool,
    two_confirmations: bool,
) -> dict[str, dict[str, Any]]:
    outcomes: dict[str, list[dict[str, Any]]] = {"development": [], "oos": []}
    candidates = {"development": 0, "oos": 0}
    split_at = bars_15m[int(len(bars_15m) * Decimal("0.7"))].time
    trend_index = 0
    secondary_trend_index = 0
    active_until: datetime | None = None
    confirmation_bars = 2 if two_confirmations else 0
    final_signal_index = len(bars_15m) - 1 - confirmation_bars
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
            if side is None:
                continue
            trend = bars_4h[: trend_index + 1]
            secondary_trend = bars_1h[: secondary_trend_index + 1]
            if strict_alignment and (not _trend_aligned(side, trend) or not _trend_aligned(side, secondary_trend)):
                continue
            signal_index = index
            entry_index = index + 1
            if confirmation_bars:
                confirmation_view = None
                for offset in range(1, confirmation_bars + 1):
                    confirmation_end = index + offset + 1
                    confirmation_view = TimeframeView(
                        "15m",
                        tuple(_as_view(bar) for bar in bars_15m[confirmation_end - 101 : confirmation_end]),
                    )
                    if _fast_sampling_side(confirmation_view) != side:
                        break
                else:
                    assert confirmation_view is not None
                    signal_index = index + confirmation_bars
                    entry_index = signal_index + 1
                    decision_bar = bars_15m[signal_index]
                    view = confirmation_view
                    confirmation_view = None
                if confirmation_view is not None:
                    continue
            cohort = "development" if decision_bar.time < split_at else "oos"
            candidates[cohort] += 1
            entry_bar = bars_15m[entry_index]
            atr14 = atr(view.bars, 14)
            if atr14 is None or atr14 <= 0:
                continue
            entry = entry_bar.open
            risk = sampling_stop_distance(atr14=atr14, reference_price=entry)
            if risk <= 0:
                continue
            net_r, exit_time, direction_hit = stream.replay(
                start=entry_bar.time,
                side=side,
                entry=entry,
                risk=risk,
            )
            if exit_time is None:
                active_until = END
                continue
            outcomes[cohort].append({"net_r": net_r, "direction_hit": direction_hit})
            active_until = _floor_15m(exit_time)
    finally:
        stream.close()
    return {cohort: _trade_metrics(outcomes[cohort], candidates[cohort]) for cohort in outcomes}


def main() -> int:
    result: dict[str, Any] = {
        "scope": "testnet_sampling_v2 strict dual 1h/4h EMA50 alignment with two closed 15m confirmations; 1m fidelity exits",
        "split": "70/30 chronological development/OOS over .strategy_refactor_history.db; Final Holdout not accessed",
        "r2_threshold": "1.15",
        "geometry": "unchanged max(1.2 * ATR14, reference_price * 0.0035) stop and 1.5R target",
        "policies": {"baseline": {}, "strict_dual_timeframe_alignment_two_bar": {}},
    }
    for symbol in ("BTC/USDT", "ETH/USDT"):
        bars_15m = load_bars(DB_PATH, symbol=symbol, timeframe="15m", start=START, end=END)
        bars_4h = load_bars(DB_PATH, symbol=symbol, timeframe="4h", start=START, end=END)
        bars_1h = load_bars(DB_PATH, symbol=symbol, timeframe="1h", start=START, end=END)
        if not bars_15m or not bars_4h or not bars_1h:
            raise RuntimeError(f"missing historical bars for {symbol}")
        result["policies"]["baseline"][symbol] = _replay_symbol(
            symbol=symbol,
            bars_15m=bars_15m,
            bars_4h=bars_4h,
            bars_1h=bars_1h,
            strict_alignment=False,
            two_confirmations=False,
        )
        result["policies"]["strict_dual_timeframe_alignment_two_bar"][symbol] = _replay_symbol(
            symbol=symbol,
            bars_15m=bars_15m,
            bars_4h=bars_4h,
            bars_1h=bars_1h,
            strict_alignment=True,
            two_confirmations=True,
        )
    baseline = result["policies"]["baseline"]
    challenger = result["policies"]["strict_dual_timeframe_alignment_two_bar"]
    comparisons: dict[str, Any] = {}
    passes = True
    for symbol in ("BTC/USDT", "ETH/USDT"):
        base = baseline[symbol]["oos"]
        cand = challenger[symbol]["oos"]
        expectancy_improved = Decimal(cand["net_expectancy_r"]) > Decimal(base["net_expectancy_r"])
        pf_not_lower = Decimal(cand["profit_factor"]) >= Decimal(base["profit_factor"])
        dd_not_higher = Decimal(cand["max_drawdown_r"]) <= Decimal(base["max_drawdown_r"])
        passes = passes and expectancy_improved and pf_not_lower and dd_not_higher
        comparisons[symbol] = {
            "expectancy_improved": expectancy_improved,
            "pf_not_lower": pf_not_lower,
            "drawdown_not_higher": dd_not_higher,
            "baseline_oos": base,
            "challenger_oos": cand,
        }
    result["promotion_gate"] = {
        "rule": "challenger must improve OOS expectancy, not lower PF, and not increase drawdown for both symbols",
        "result": "PASS" if passes else "REJECTED",
        "comparisons": comparisons,
    }
    output = Path(
        "artifacts/active_strategy_optimization/strict_dual_timeframe_alignment_two_bar_1m_fidelity_20260815.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(result["promotion_gate"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
