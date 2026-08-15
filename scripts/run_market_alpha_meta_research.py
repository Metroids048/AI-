"""Build new-market-alpha data and run nested Meta-Label OOS research.

This is read-only.  It downloads public Binance spot/perpetual 1h klines up to the
sealed boundary, joins point-in-time funding from the historical database, and
compares price-only against genuinely new alpha feature sets.  It never reads the
sealed holdout and never creates a TradeCandidate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import time as time_module
import urllib.parse
import urllib.request
from bisect import bisect_left
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from math import sqrt
from pathlib import Path
from statistics import stdev
from typing import Any

from sklearn.ensemble import GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from services.strategy_library.market_alpha import AlphaSnapshot, MarketBar, build_snapshot, feature_vector
from services.validation.proposal_walk_forward import build_proposal_walk_forward_windows

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / ".strategy_refactor_history.db"
RAW_DIR = ROOT / "artifacts/market_alpha/raw"
REPORT_PATH = ROOT / "artifacts/market_alpha/reports/market_alpha_meta_research.json"
DATASET_PATH = ROOT / "artifacts/market_alpha/canonical/market_alpha_events.jsonl"
DEV_START = datetime(2023, 1, 29, tzinfo=UTC)
HOLDOUT_START = datetime(2026, 1, 29, tzinfo=UTC)
SYMBOLS = ("BTC/USDT", "ETH/USDT")
FEATURE_SETS = ("PRICE_ONLY", "DERIVATIVES_PRESSURE", "SPOT_FUTURES_DISLOCATION", "BTC_ETH_LEAD_LAG", "ALL_ALPHA")
MODELS = ("LOGISTIC", "GBM")
TARGET_RRS = (1.5, 1.75, 2.0, 2.25)
THRESHOLDS = (0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80)
MIN_VALIDATION_TRADES = 15
STRESS_COST_MULTIPLIER = 1.5


@dataclass(frozen=True)
class ResearchRow:
    symbol: str
    side: str
    event_time: str
    entry_time: str
    outcome_time: str
    target_rr: float
    entry: float
    stop: float
    atr: float
    breakout_distance_atr: float
    snapshot: dict[str, float | int | str]
    outcome: str
    outcome_r: float
    cost_r: float

    @property
    def net_r(self) -> float:
        return self.outcome_r - self.cost_r


def _dt(value: str | int | float) -> datetime:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, tz=UTC)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fetch_klines(*, market: str, symbol: str, start: datetime, end: datetime) -> tuple[MarketBar, ...]:
    cache = RAW_DIR / f"{market}_{symbol.replace('/', '')}_1h.jsonl"
    if cache.exists() and cache.stat().st_size > 100:
        rows = [json.loads(line) for line in cache.read_text(encoding="utf-8").splitlines() if line.strip()]
        return tuple(MarketBar(int(row[0]), float(row[4]), float(row[7]), float(row[10])) for row in rows)
    base = "https://fapi.binance.com/fapi/v1/klines" if market == "perp" else "https://api.binance.com/api/v3/klines"
    symbol_raw = symbol.replace("/", "")
    cursor = _ms(start)
    end_ms = _ms(end)
    raw: list[list[Any]] = []
    while cursor < end_ms:
        query = urllib.parse.urlencode(
            {"symbol": symbol_raw, "interval": "1h", "startTime": cursor, "endTime": end_ms, "limit": 1000}
        )
        request = urllib.request.Request(f"{base}?{query}", headers={"User-Agent": "ai-quant-research/1.0"})
        for attempt in range(4):
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    batch = json.loads(response.read())
                break
            except Exception:
                if attempt == 3:
                    raise
                time_module.sleep(1.5 * (attempt + 1))
        if not batch:
            break
        raw.extend(batch)
        next_cursor = int(batch[-1][0]) + 3_600_000
        if next_cursor <= cursor:
            break
        cursor = next_cursor
        time_module.sleep(0.05)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_text("\n".join(json.dumps(row, separators=(",", ":")) for row in raw) + "\n", encoding="utf-8")
    return tuple(MarketBar(int(row[0]), float(row[4]), float(row[7]), float(row[10])) for row in raw)


def _load_15m(connection: sqlite3.Connection, symbol: str) -> tuple[tuple[datetime, float, float, float, float], ...]:
    rows = connection.execute(
        "SELECT time, open, high, low, close FROM ohlcv_bars WHERE symbol=? AND timeframe='15m' AND time < ? ORDER BY time",
        (symbol, HOLDOUT_START.replace(tzinfo=None).isoformat(sep=" ")),
    ).fetchall()
    return tuple((_dt(str(row[0])), float(row[1]), float(row[2]), float(row[3]), float(row[4])) for row in rows)


def _atr(bars: tuple[MarketBar, ...], index: int, period: int = 14) -> float:
    sample = bars[max(1, index - period) : index + 1]
    ranges = [
        max(current.close - previous.close, 0.0) for previous, current in zip(sample[:-1], sample[1:], strict=True)
    ]
    return sum(ranges) / len(ranges) if ranges else 0.0


def _funding(connection: sqlite3.Connection, symbol: str) -> tuple[tuple[int, float], ...]:
    rows = connection.execute(
        "SELECT time, funding_rate FROM market_extras WHERE symbol=? AND time < ? ORDER BY time",
        (symbol, HOLDOUT_START.replace(tzinfo=None).isoformat(sep=" ")),
    ).fetchall()
    return tuple((_ms(_dt(str(row[0]))), float(row[1] or 0.0)) for row in rows)


def _latest(points: tuple[tuple[int, float], ...], timestamp: int) -> float:
    index = bisect_left([point[0] for point in points], timestamp + 1) - 1
    return points[index][1] if index >= 0 else 0.0


def _outcome(
    *,
    side: str,
    entry_index: int,
    entry: float,
    stop: float,
    target: float,
    bars: tuple[tuple[datetime, float, float, float, float], ...],
) -> tuple[str, datetime, float] | None:
    for row in bars[entry_index : entry_index + 96]:
        bar_time, _, high, low, close = row
        hit_stop = low <= stop if side == "long" else high >= stop
        hit_target = high >= target if side == "long" else low <= target
        if hit_stop:
            return "STOP_FIRST", bar_time, -1.0
        if hit_target:
            return "TP_FIRST", bar_time, abs(target - entry) / abs(entry - stop)
    if not bars:
        return None
    last = bars[min(len(bars) - 1, entry_index + 95)]
    signed = last[4] - entry if side == "long" else entry - last[4]
    risk = abs(entry - stop)
    return "TIMEOUT", last[0], max(-1.0, min(abs(target - entry) / risk, signed / risk))


def _build_rows() -> tuple[ResearchRow, ...]:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as connection:
        spot = {
            symbol: _fetch_klines(market="spot", symbol=symbol, start=DEV_START, end=HOLDOUT_START)
            for symbol in SYMBOLS
        }
        perp = {
            symbol: _fetch_klines(market="perp", symbol=symbol, start=DEV_START, end=HOLDOUT_START)
            for symbol in SYMBOLS
        }
        funding = {symbol: _funding(connection, symbol) for symbol in SYMBOLS}
        bars15 = {symbol: _load_15m(connection, symbol) for symbol in SYMBOLS}
    perp_by_ts = {symbol: {bar.timestamp: bar for bar in bars} for symbol, bars in perp.items()}
    spot_by_ts = {symbol: {bar.timestamp: bar for bar in bars} for symbol, bars in spot.items()}
    btc_returns: dict[int, float] = {}
    btc_bars = perp["BTC/USDT"]
    for index in range(1, len(btc_bars)):
        previous, current = btc_bars[index - 1], btc_bars[index]
        btc_returns[current.timestamp] = current.close / previous.close - 1.0 if previous.close else 0.0
    rows: list[ResearchRow] = []
    for symbol in SYMBOLS:
        perp_bars = perp[symbol]
        spot_map = spot_by_ts[symbol]
        perp_map = perp_by_ts[symbol]
        funding_points = funding[symbol]
        local_bars = bars15[symbol]
        local_times = [item[0] for item in local_bars]
        basis_history: list[float] = []
        funding_history: list[float] = []
        for index in range(25, len(perp_bars)):
            current, previous = perp_bars[index], perp_bars[index - 1]
            event_time = _dt(current.timestamp + 3_600_000)
            if event_time >= HOLDOUT_START:
                break
            spot_current = spot_map.get(current.timestamp)
            spot_previous = spot_map.get(previous.timestamp)
            if not spot_current or not spot_previous:
                continue
            atr = _atr(perp_bars, index)
            funding_rate = _latest(funding_points, current.timestamp)
            basis = current.close / spot_current.close - 1.0
            basis_history.append(basis)
            funding_history.append(funding_rate)
            prior = perp_bars[index - 20 : index]
            high_level, low_level = max(bar.close for bar in prior), min(bar.close for bar in prior)
            side: str | None = None
            distance = 0.0
            if current.close > high_level + 0.10 * atr:
                side, distance = "long", (current.close - high_level) / max(atr, 1e-12)
            elif current.close < low_level - 0.10 * atr:
                side, distance = "short", (low_level - current.close) / max(atr, 1e-12)
            if not side or atr <= 0 or abs(current.close - previous.close) / atr < 0.35:
                continue
            btc_return = btc_returns.get(current.timestamp, 0.0)
            relative = (current.close / previous.close - 1.0) - btc_return if symbol != "BTC/USDT" else 0.0
            snapshot = build_snapshot(
                symbol=symbol,
                timestamp=current.timestamp,
                perp=current,
                perp_previous=previous,
                spot=spot_current,
                spot_previous=spot_previous,
                perp_history=perp_bars[: index + 1],
                basis_history=basis_history,
                funding_rate=funding_rate,
                funding_history=funding_history,
                btc_return_1h=btc_return,
                relative_return=relative,
            )
            entry_index = bisect_left(local_times, event_time)
            if entry_index >= len(local_bars):
                continue
            entry = local_bars[entry_index][1]
            stop = entry - atr if side == "long" else entry + atr
            for target_rr in TARGET_RRS:
                target = entry + atr * target_rr if side == "long" else entry - atr * target_rr
                result = _outcome(
                    side=side, entry_index=entry_index, entry=entry, stop=stop, target=target, bars=local_bars
                )
                if not result:
                    continue
                outcome, outcome_time, outcome_r = result
                rows.append(
                    ResearchRow(
                        symbol=symbol,
                        side=side,
                        event_time=event_time.isoformat(),
                        entry_time=local_bars[entry_index][0].isoformat(),
                        outcome_time=outcome_time.isoformat(),
                        target_rr=target_rr,
                        entry=entry,
                        stop=stop,
                        atr=atr,
                        breakout_distance_atr=distance,
                        snapshot={**asdict(snapshot), "basis_history_last": basis_history[-1]},
                        outcome=outcome,
                        outcome_r=outcome_r,
                        cost_r=(entry * 12.0 / 10000.0) / atr,
                    )
                )
    return tuple(rows)


def _metrics(
    rows: Iterable[ResearchRow], probabilities: Iterable[float] | None = None, threshold: float = 0.0
) -> dict[str, float | int]:
    materialized = tuple(rows)
    probability_values = tuple(probabilities) if probabilities is not None else None
    selected = [
        row
        for index, row in enumerate(materialized)
        if probability_values is None or probability_values[index] >= threshold
    ]
    selected.sort(key=lambda row: (row.event_time, row.symbol, row.side))
    values = [row.net_r for row in selected]
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value < 0]
    equity = peak = drawdown = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return {
        "trades": len(values),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(values) if values else 0.0,
        "avg_win_r": sum(wins) / len(wins) if wins else 0.0,
        "avg_loss_r": sum(losses) / len(losses) if losses else 0.0,
        "payoff": (sum(wins) / len(wins)) / abs(sum(losses) / len(losses)) if wins and losses else 0.0,
        "profit_factor": sum(wins) / abs(sum(losses)) if losses else 0.0,
        "expectancy": sum(values) / len(values) if values else 0.0,
        "max_drawdown_r": drawdown,
        "expectancy_lcb95": _expectancy_lcb95(values),
        "stress_expectancy": _expectancy_from_values(selected, STRESS_COST_MULTIPLIER),
        "stress_profit_factor": _profit_factor_from_values(selected, STRESS_COST_MULTIPLIER),
    }


def _expectancy_lcb95(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    return sum(values) / len(values) - 1.96 * stdev(values) / sqrt(len(values))


def _stressed_values(rows: Iterable[ResearchRow], cost_multiplier: float) -> list[float]:
    return [row.outcome_r - row.cost_r * cost_multiplier for row in rows]


def _expectancy_from_values(rows: Iterable[ResearchRow], cost_multiplier: float) -> float:
    values = _stressed_values(rows, cost_multiplier)
    return sum(values) / len(values) if values else 0.0


def _profit_factor_from_values(rows: Iterable[ResearchRow], cost_multiplier: float) -> float:
    values = _stressed_values(rows, cost_multiplier)
    wins = sum(value for value in values if value > 0)
    losses = sum(value for value in values if value < 0)
    return wins / abs(losses) if losses else 0.0


def _ordered(rows: Iterable[ResearchRow]) -> tuple[ResearchRow, ...]:
    return tuple(sorted(rows, key=lambda row: (row.event_time, row.symbol, row.side)))


def _model(name: str):
    if name == "LOGISTIC":
        return make_pipeline(StandardScaler(), LogisticRegression(C=0.5, max_iter=500, random_state=7))
    return GradientBoostingClassifier(random_state=7, n_estimators=80, max_depth=2, learning_rate=0.05)


def _snapshot_from_row(row: ResearchRow) -> AlphaSnapshot:
    values = row.snapshot
    return AlphaSnapshot(
        symbol=str(values["symbol"]),
        timestamp=int(values["timestamp"]),
        perp_return_1h=float(values["perp_return_1h"]),
        spot_return_1h=float(values["spot_return_1h"]),
        basis=float(values["basis"]),
        basis_change=float(values["basis_change"]),
        taker_imbalance=float(values["taker_imbalance"]),
        taker_imbalance_3h=float(values["taker_imbalance_3h"]),
        funding_rate=float(values["funding_rate"]),
        funding_zscore=float(values["funding_zscore"]),
        btc_return_1h=float(values["btc_return_1h"]),
        relative_return=float(values["relative_return"]),
    )


def _run_arm(
    rows: tuple[ResearchRow, ...], feature_set: str, model_name: str, target_rr: float, windows
) -> dict[str, Any]:
    window_reports: list[dict[str, Any]] = []
    all_selected: list[ResearchRow] = []
    for window in windows:
        train = _ordered(
            row
            for row in rows
            if row.target_rr == target_rr and window.train_start <= _dt(row.event_time) < window.train_end
        )
        oos = _ordered(
            row
            for row in rows
            if row.target_rr == target_rr
            and window.oos_start <= _dt(row.event_time) < window.oos_end
            and _dt(row.outcome_time) < window.oos_end
        )
        split = int(len(train) * 0.67)
        fit, validation = train[:split], train[split:]
        best: tuple[float, float] | None = None
        if len(fit) >= 40 and len({row.outcome_r > 0 for row in fit}) == 2:
            X_fit = [feature_vector(_snapshot_from_row(row), feature_set) for row in fit]
            y_fit = [int(row.outcome_r > 0) for row in fit]
            classifier = _model(model_name).fit(X_fit, y_fit)
            X_validation = [feature_vector(_snapshot_from_row(row), feature_set) for row in validation]
            probabilities = classifier.predict_proba(X_validation)[:, 1] if validation else []
            for threshold in THRESHOLDS:
                selected = [
                    row
                    for row, probability in zip(validation, probabilities, strict=True)
                    if probability >= threshold and probability * target_rr - (1 - probability) - row.cost_r >= 0.10
                ]
                metrics = _metrics(selected)
                score = (float(metrics["expectancy"]), threshold)
                if (
                    len(selected) >= MIN_VALIDATION_TRADES
                    and (best is None or score > best)
                ):
                    best = score
        if best is None or not oos:
            window_reports.append({"window_id": window.window_id, "config": None, "metrics": _metrics(())})
            continue
        threshold = best[1]
        validation_selected = [
            row
            for row, probability in zip(validation, probabilities, strict=True)
            if probability >= threshold and probability * target_rr - (1 - probability) - row.cost_r >= 0.10
        ]
        X_fit = [feature_vector(_snapshot_from_row(row), feature_set) for row in train]
        y_fit = [int(row.outcome_r > 0) for row in train]
        classifier = _model(model_name).fit(X_fit, y_fit)
        X_oos = [feature_vector(_snapshot_from_row(row), feature_set) for row in oos]
        probabilities = classifier.predict_proba(X_oos)[:, 1]
        selected = [
            row
            for row, probability in zip(oos, probabilities, strict=True)
            if probability >= threshold and probability * target_rr - (1 - probability) - row.cost_r >= 0.10
        ]
        all_selected.extend(selected)
        window_reports.append(
            {
                "window_id": window.window_id,
                "config": {
                    "feature_set": feature_set,
                    "model": model_name,
                    "target_rr": target_rr,
                    "threshold": threshold,
                    "validation_metrics": _metrics(validation_selected),
                },
                "metrics": _metrics(selected),
            }
        )
    aggregate = _metrics(all_selected)
    aggregate["positive_windows"] = sum(
        item["metrics"]["expectancy"] > 0 for item in window_reports if item["metrics"]["trades"]
    )
    return {
        "feature_set": feature_set,
        "model": model_name,
        "target_rr": target_rr,
        "aggregate": aggregate,
        "windows": window_reports,
    }


def run() -> dict[str, Any]:
    rows = _build_rows()
    windows = build_proposal_walk_forward_windows(
        development_start=DEV_START,
        development_end=HOLDOUT_START,
        window_count=8,
        train_months=12,
        oos_months=3,
        max_lookback_bars=72,
        max_holding_bars=96,
        bar_interval=timedelta(minutes=15),
        embargo=timedelta(hours=24),
    )
    arms = [
        _run_arm(rows, feature_set, model_name, target_rr, windows)
        for feature_set in FEATURE_SETS
        for model_name in MODELS
        for target_rr in TARGET_RRS
    ]
    accepted = [
        arm
        for arm in arms
        if arm["aggregate"]["trades"] >= 100
        and arm["aggregate"]["win_rate"] >= 0.55
        and arm["aggregate"]["payoff"] >= 1.15
        and arm["aggregate"]["profit_factor"] >= 1.40
        and arm["aggregate"]["expectancy"] >= 0.10
        and arm["aggregate"]["expectancy_lcb95"] > 0.0
        and arm["aggregate"]["positive_windows"] >= 6
        and arm["aggregate"]["max_drawdown_r"] <= 20.0
        and arm["aggregate"]["stress_expectancy"] > 0.0
        and arm["aggregate"]["stress_profit_factor"] > 1.10
    ]
    return {
        "status": "HISTORICAL_ALPHA_PASS" if accepted else "CURRENT_MARKET_ALPHA_SOURCE_EXHAUSTED",
        "development_start": DEV_START.isoformat(),
        "holdout_start": HOLDOUT_START.isoformat(),
        "holdout_accessed": False,
        "data_sources": ["binance_spot_1h_klines", "binance_perpetual_1h_klines", "market_extras_funding_rate"],
        "rows": len(rows),
        "feature_sets": FEATURE_SETS,
        "models": MODELS,
        "target_rrs": TARGET_RRS,
        "selection_policy": {
            "minimum_validation_trades": MIN_VALIDATION_TRADES,
            "expected_net_r_floor": 0.10,
            "cost_multiplier": 1.0,
            "validation_does_not_predeclare_final_acceptance": True,
        },
        "acceptance_thresholds": {
            "oos_trades": 100,
            "win_rate": 0.55,
            "payoff": 1.15,
            "profit_factor": 1.40,
            "expectancy": 0.10,
            "expectancy_lcb95": 0.0,
            "positive_windows": 6,
            "max_drawdown_r": 20.0,
            "stress_expectancy": 0.0,
            "stress_profit_factor": 1.10,
        },
        "arms_with_oos_trades": sum(arm["aggregate"]["trades"] > 0 for arm in arms),
        "arms_with_configured_windows": sum(
            any(window["config"] is not None for window in arm["windows"]) for arm in arms
        ),
        "arms": arms,
        "accepted_arms": len(accepted),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, default=REPORT_PATH)
    parser.add_argument("--dataset", type=Path, default=DATASET_PATH)
    args = parser.parse_args()
    report = run()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    args.dataset.parent.mkdir(parents=True, exist_ok=True)
    # Rebuild rows once more only for the canonical event export, never for scoring.
    rows = _build_rows()
    args.dataset.write_text("\n".join(json.dumps(asdict(row), default=str) for row in rows) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "arms"}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
