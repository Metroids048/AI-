"""Bounded, research-only replay for the three pre-registered market-neutral families."""

from __future__ import annotations

import csv
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "ai-quant/market-neutral-v1"
OUT = Path("artifacts/market_neutral_research_v1")
AUDIT_PATH = OUT / "DATA_AUDIT.json"
START = datetime(2023, 1, 29, tzinfo=UTC)
END = datetime(2026, 1, 29, tzinfo=UTC)
RESEARCH_END = START + (END - START) * 0.6
VALIDATION_END = START + (END - START) * 0.8
SYMBOLS = ("BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT")
BASE_NOTIONAL = 100_000.0
COMMISSION_PER_LEG = 0.001
SLIPPAGE_PER_LEG = 0.0006


@dataclass(frozen=True)
class Bar:
    ts: datetime
    close: float


def _ts(raw: str) -> datetime:
    value = int(float(raw))
    divisor = 1_000_000 if value >= 100_000_000_000_000 else 1_000
    return datetime.fromtimestamp(value / divisor, tz=UTC)


def _bars(dataset: str, symbol: str) -> dict[datetime, Bar]:
    result: dict[datetime, Bar] = {}
    for path in sorted((ROOT / dataset / symbol / "1h").glob("*.zip")):
        with ZipFile(path) as archive:
            member = next(item for item in archive.infolist() if not item.is_dir())
            with archive.open(member) as stream:
                for row in csv.reader(line.decode("utf-8") for line in stream):
                    if not row or row[0].lower().endswith("open_time"):
                        continue
                    try:
                        ts = _ts(row[0])
                        if START <= ts < END:
                            result[ts] = Bar(ts, float(row[4]))
                    except (TypeError, ValueError, OSError):
                        continue
    return result


def _funding(symbol: str) -> dict[datetime, float]:
    return {
        datetime.fromtimestamp(int(item["fundingTime"]) / 1000, tz=UTC): float(item["fundingRate"])
        for item in (
            json.loads(line) for line in (ROOT / "funding" / f"{symbol}.jsonl").read_text(encoding="utf-8").splitlines()
        )
        if START <= datetime.fromtimestamp(int(item["fundingTime"]) / 1000, tz=UTC) < END
    }


def _metrics(values: list[float], cost_stress: float) -> dict[str, float | int | bool | None]:
    if not values:
        return {
            "observations": 0,
            "profit_factor": 0.0,
            "expectancy": 0.0,
            "max_drawdown": 0.0,
            "cost_stress_expectancy": 0.0,
            "majority_windows_positive": False,
            "gate_pass": False,
        }
    gains = sum(value for value in values if value > 0)
    losses = -sum(value for value in values if value < 0)
    pf = gains / losses if losses else None
    equity = peak = 1.0
    max_dd = 0.0
    for value in values:
        equity *= 1.0 + value
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak if peak else 1.0)
    windows = [values[len(values) * index // 3 : len(values) * (index + 1) // 3] for index in range(3)]
    positive_windows = sum(sum(window) > 0 for window in windows if window)
    stressed_expectancy = sum(value - cost_stress for value in values) / len(values)
    majority = positive_windows > 3 / 2
    gate = (
        len(values) >= 80
        and pf is not None
        and pf >= 1.15
        and sum(values) / len(values) > 0
        and max_dd <= 0.15
        and majority
        and stressed_expectancy > 0
    )
    return {
        "observations": len(values),
        "profit_factor": pf,
        "expectancy": sum(values) / len(values),
        "max_drawdown": min(max_dd, 1.0),
        "cost_stress_expectancy": stressed_expectancy,
        "positive_windows": positive_windows,
        "window_count": 3,
        "majority_windows_positive": majority,
        "gate_pass": gate,
    }


def _write(name: str, payload: dict[str, object]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = OUT / f".{name}.tmp"
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=True, allow_nan=False) + "\n", encoding="utf-8")
    tmp.replace(OUT / name)


def _execution_reality() -> dict[str, object]:
    return {
        "status": "NOT_RUN_NO_SURVIVOR",
        "delay_seconds": [0.5, 2.0, 5.0],
        "legging_risk": None,
        "capital_efficiency": None,
    }


def _h1(
    spot: dict[str, dict[datetime, Bar]],
    perp: dict[str, dict[datetime, Bar]],
    funding: dict[str, dict[datetime, float]],
    mark: dict[str, dict[datetime, Bar]],
    index: dict[str, dict[datetime, Bar]],
    premium: dict[str, dict[datetime, Bar]],
) -> dict[str, object]:
    output: dict[str, object] = {
        "family": "H1_SPOT_PERP_CARRY",
        "rule": "8h observations; long spot and short perpetual; basis threshold >= 0 bps",
        "execution_reality": _execution_reality(),
        "symbols": {},
    }
    for symbol in ("BTCUSDT", "ETHUSDT"):
        values: list[float] = []
        samples: list[dict[str, object]] = []
        intervals: list[tuple[str, str]] = []
        times = sorted(
            set(spot[symbol]) & set(perp[symbol]) & set(mark[symbol]) & set(index[symbol]) & set(premium[symbol])
        )
        for ts in times:
            end_ts = ts + timedelta(hours=8)
            if ts.hour % 8 or end_ts >= RESEARCH_END or end_ts not in spot[symbol] or end_ts not in perp[symbol]:
                continue
            basis = mark[symbol][ts].close / spot[symbol][ts].close - 1.0
            if basis < 0:
                continue
            annualized_basis = (1.0 + basis) ** (365.0 * 3.0) - 1.0
            spot_leg = spot[symbol][end_ts].close / spot[symbol][ts].close - 1.0
            perp_leg = -(perp[symbol][end_ts].close / perp[symbol][ts].close - 1.0)
            funding_pnl = sum(rate for ft, rate in funding[symbol].items() if ts < ft <= end_ts)
            spot_commission = perp_commission = COMMISSION_PER_LEG
            spot_slippage = perp_slippage = SLIPPAGE_PER_LEG
            cost = spot_commission + perp_commission + spot_slippage + perp_slippage
            net = spot_leg + perp_leg + funding_pnl - cost
            values.append(net)
            intervals.append((ts.isoformat(), end_ts.isoformat()))
            samples.append(
                {
                    "timestamp": ts.isoformat(),
                    "end_timestamp": end_ts.isoformat(),
                    "basis": basis,
                    "index_basis": mark[symbol][ts].close / index[symbol][ts].close - 1.0,
                    "annualized_basis": annualized_basis,
                    "premium_index": premium[symbol][ts].close,
                    "spot_leg_pnl": spot_leg,
                    "perp_leg_pnl": perp_leg,
                    "funding_pnl": funding_pnl,
                    "spot_commission": spot_commission,
                    "perp_commission": perp_commission,
                    "spot_slippage": spot_slippage,
                    "perp_slippage": perp_slippage,
                    "cost": cost,
                    "net_pnl": net,
                }
            )
        output["symbols"][symbol] = {
            "metrics": _metrics(values, 0.0016),
            "trade_count": len(intervals),
            "sample_trades": samples[:20],
            "_intervals": intervals,
        }
    return output


def _h2(perp: dict[str, dict[datetime, Bar]], funding: dict[str, dict[datetime, float]]) -> dict[str, object]:
    values: list[float] = []
    samples: list[dict[str, object]] = []
    intervals: list[tuple[str, str]] = []
    common = sorted(set.intersection(*(set(funding[symbol]) for symbol in SYMBOLS)))
    for ts in common:
        ranked = sorted(SYMBOLS, key=lambda symbol: funding[symbol][ts])
        long_symbol, short_symbol = ranked[0], ranked[-1]
        end_ts = ts + timedelta(hours=8)
        if (
            ts >= RESEARCH_END
            or end_ts >= RESEARCH_END
            or ts not in perp[long_symbol]
            or ts not in perp[short_symbol]
            or end_ts not in perp[long_symbol]
            or end_ts not in perp[short_symbol]
        ):
            continue
        long_leg = perp[long_symbol][end_ts].close / perp[long_symbol][ts].close - 1.0
        short_leg = -(perp[short_symbol][end_ts].close / perp[short_symbol][ts].close - 1.0)
        funding_pnl = funding[short_symbol][ts] - funding[long_symbol][ts]
        long_commission = short_commission = COMMISSION_PER_LEG
        long_slippage = short_slippage = SLIPPAGE_PER_LEG
        cost = long_commission + short_commission + long_slippage + short_slippage
        net = long_leg + short_leg + funding_pnl - cost
        values.append(net)
        intervals.append((ts.isoformat(), end_ts.isoformat()))
        samples.append(
            {
                "timestamp": ts.isoformat(),
                "end_timestamp": end_ts.isoformat(),
                "long": long_symbol,
                "short": short_symbol,
                "long_leg_pnl": long_leg,
                "short_leg_pnl": short_leg,
                "funding_pnl": funding_pnl,
                "long_commission": long_commission,
                "short_commission": short_commission,
                "long_slippage": long_slippage,
                "short_slippage": short_slippage,
                "cost": cost,
                "net_pnl": net,
            }
        )
    return {
        "family": "H2_CROSS_SECTIONAL_FUNDING_SPREAD",
        "rule": "fixed five-symbol universe; each funding interval long minimum funding and short maximum funding; dollar neutral",
        "metrics": _metrics(values, 0.0016),
        "trade_count": len(intervals),
        "sample_trades": samples[:20],
        "_intervals": intervals,
        "execution_reality": _execution_reality(),
    }


def _beta(xs: list[float], ys: list[float]) -> float:
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    den = sum((x - mx) ** 2 for x in xs)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / den if den else 1.0


def _h3(perp: dict[str, dict[datetime, Bar]]) -> dict[str, object]:
    btc, eth = perp["BTCUSDT"], perp["ETHUSDT"]
    times = sorted(set(btc) & set(eth))
    values: list[float] = []
    samples: list[dict[str, object]] = []
    intervals: list[tuple[str, str]] = []
    for index in range(168, len(times) - 24, 24):
        ts, end_ts = times[index], times[index + 24]
        if end_ts >= RESEARCH_END:
            break
        window = times[index - 168 : index]
        btc_r = [btc[b].close / btc[a].close - 1.0 for a, b in zip(window[:-1], window[1:], strict=True)]
        eth_r = [eth[b].close / eth[a].close - 1.0 for a, b in zip(window[:-1], window[1:], strict=True)]
        beta = _beta(btc_r, eth_r)
        spread = (eth[ts].close / eth[times[index - 1]].close - 1.0) - beta * (
            btc[ts].close / btc[times[index - 1]].close - 1.0
        )
        direction = -1.0 if spread > 0 else 1.0
        eth_leg = direction * (eth[end_ts].close / eth[ts].close - 1.0)
        btc_leg = -direction * beta * (btc[end_ts].close / btc[ts].close - 1.0)
        btc_commission = eth_commission = COMMISSION_PER_LEG
        btc_slippage = eth_slippage = SLIPPAGE_PER_LEG
        cost = btc_commission + eth_commission + btc_slippage + eth_slippage
        net = btc_leg + eth_leg - cost
        btc_signed_notional = -direction * beta * BASE_NOTIONAL
        eth_signed_notional = direction * BASE_NOTIONAL
        btc_beta_exposure = btc_signed_notional
        eth_beta_exposure = eth_signed_notional * beta
        estimated_beta_exposure = btc_beta_exposure + eth_beta_exposure
        values.append(net)
        intervals.append((ts.isoformat(), end_ts.isoformat()))
        samples.append(
            {
                "timestamp": ts.isoformat(),
                "end_timestamp": end_ts.isoformat(),
                "beta": beta,
                "gross_exposure_usd": abs(btc_signed_notional) + abs(eth_signed_notional),
                "net_usd_exposure": abs(btc_signed_notional + eth_signed_notional),
                "estimated_beta_exposure_usd": estimated_beta_exposure,
                "btc_beta_exposure_usd": btc_beta_exposure,
                "eth_beta_exposure_usd": eth_beta_exposure,
                "btc_signed_notional_usd": btc_signed_notional,
                "eth_signed_notional_usd": eth_signed_notional,
                "btc_leg_pnl": btc_leg,
                "eth_leg_pnl": eth_leg,
                "spread_pnl": btc_leg + eth_leg,
                "btc_commission": btc_commission,
                "eth_commission": eth_commission,
                "btc_slippage": btc_slippage,
                "eth_slippage": eth_slippage,
                "cost": cost,
                "net_pnl": net,
            }
        )
    return {
        "family": "H3_BTC_ETH_RELATIVE_VALUE",
        "rule": "168h rolling beta from past returns; 24h non-overlapping beta-neutral residual trades",
        "metrics": _metrics(values, 0.0016),
        "trade_count": len(intervals),
        "sample_trades": samples[:20],
        "_intervals": intervals,
        "execution_reality": _execution_reality(),
    }


def _summary(result: dict[str, object]) -> dict[str, object]:
    if "symbols" in result:
        return {
            symbol: {"trade_count": item["trade_count"], "metrics": item["metrics"]}
            for symbol, item in result["symbols"].items()
        }
    return {"trade_count": result["trade_count"], "metrics": result["metrics"]}


def _leakage_audit(h1: dict[str, object], h2: dict[str, object], h3: dict[str, object]) -> dict[str, object]:
    intervals: list[tuple[datetime, datetime, str]] = []
    for family, result in (("H1", h1), ("H2", h2), ("H3", h3)):
        if "symbols" in result:
            groups = ((f"{family}:{symbol}", item["_intervals"]) for symbol, item in result["symbols"].items())
        else:
            groups = ((family, result["_intervals"]),)
        for group_key, group in groups:
            intervals.extend(
                (datetime.fromisoformat(start), datetime.fromisoformat(end), group_key) for start, end in group
            )
    by_family: dict[str, list[tuple[datetime, datetime]]] = {}
    for start, end, family in intervals:
        by_family.setdefault(family, []).append((start, end))
    overlap = any(
        any(left[1] > right[0] for left, right in zip(sorted(group), sorted(group)[1:], strict=False))
        for group in by_family.values()
    )
    intervals.sort()
    crosses_research = any(end > RESEARCH_END for _, end, _ in intervals)
    return {
        "schema": "MARKET_NEUTRAL_LEAKAGE_AUDIT_V1",
        "status": "PASS" if not overlap and not crosses_research else "FAIL",
        "chronological": True,
        "split_boundaries": {
            "research_end": RESEARCH_END.isoformat(),
            "validation_end": VALIDATION_END.isoformat(),
            "final_holdout_start": END.isoformat(),
        },
        "future_beta": False,
        "full_sample_normalization": False,
        "position_crosses_split": crosses_research,
        "overlapping_positions_within_family": overlap,
        "research_intervals_checked": len(intervals),
        "validation_accessed": False,
        "final_holdout_accessed": False,
    }


def main() -> int:
    audit = json.loads(AUDIT_PATH.read_text(encoding="utf-8-sig")) if AUDIT_PATH.exists() else {}
    if audit.get("status") != "DATA_READY":
        _write(
            "FINAL_REPORT.json",
            {
                "schema": "MARKET_NEUTRAL_RESEARCH_V1_FINAL_REPORT",
                "status": "BLOCKED_MARKET_NEUTRAL_DATA",
                "reason": "DATA_AUDIT_NOT_READY",
                "Final Holdout accessed": False,
                "Runtime modified": False,
                "Production": "NOT_GRANTED",
            },
        )
        return 2
    spot = {symbol: _bars("spot", symbol) for symbol in SYMBOLS}
    perp = {symbol: _bars("perp", symbol) for symbol in SYMBOLS}
    mark = {symbol: _bars("markPrice", symbol) for symbol in SYMBOLS}
    index = {symbol: _bars("indexPrice", symbol) for symbol in SYMBOLS}
    premium = {symbol: _bars("premiumIndex", symbol) for symbol in SYMBOLS}
    funding = {symbol: _funding(symbol) for symbol in SYMBOLS}
    h1, h2, h3 = _h1(spot, perp, funding, mark, index, premium), _h2(perp, funding), _h3(perp)
    _write("LEAKAGE_AUDIT.json", _leakage_audit(h1, h2, h3))
    for result in (h1, h2, h3):
        if "symbols" in result:
            for item in result["symbols"].values():
                item.pop("_intervals", None)
        else:
            result.pop("_intervals", None)
    for result, name in (
        (h1, "H1_SPOT_PERP_CARRY.json"),
        (h2, "H2_FUNDING_SPREAD.json"),
        (h3, "H3_BTC_ETH_RELATIVE_VALUE.json"),
    ):
        _write(name, result)
    statuses = {
        "H1": any(item["metrics"]["gate_pass"] for item in h1["symbols"].values()),
        "H2": h2["metrics"]["gate_pass"],
        "H3": h3["metrics"]["gate_pass"],
    }
    if any(statuses.values()):
        raise RuntimeError("Survivor requires explicit Validation, Stability and one-time Final Holdout phases")
    _write(
        "VALIDATION.json",
        {
            "schema": "MARKET_NEUTRAL_VALIDATION_V1",
            "status": "NOT_RUN",
            "reason": "all_research_gates_failed",
            "final_holdout_accessed": False,
            "runtime_modified": False,
        },
    )
    _write(
        "STABILITY.json",
        {
            "schema": "MARKET_NEUTRAL_STABILITY_V1",
            "status": "NOT_RUN",
            "reason": "no_research_survivor",
            "final_holdout_accessed": False,
            "runtime_modified": False,
        },
    )
    _write(
        "FINAL_REPORT.json",
        {
            "schema": "MARKET_NEUTRAL_RESEARCH_V1_FINAL_REPORT",
            "status": "MARKET_NEUTRAL_BATCH_EXHAUSTED",
            "data_audit_status": audit.get("status"),
            "data_quality": {
                "coverage_thresholds_passed": True,
                "source_gaps_not_filled": True,
                "impact": "Research uses only timestamp intersections; missing official bars reduce eligible observations but do not create synthetic fills.",
            },
            "H1": _summary(h1),
            "H2": _summary(h2),
            "H3": _summary(h3),
            "Best": None,
            "Final Holdout accessed": False,
            "Survivor": None,
            "Runtime modified": False,
            "Production": "NOT_GRANTED",
            "Terminal": "MARKET_NEUTRAL_BATCH_EXHAUSTED",
            "Next action": "STOP_RESEARCH_AND_REASSESS",
        },
    )
    print(
        json.dumps(
            {
                "status": "MARKET_NEUTRAL_BATCH_EXHAUSTED",
                "research_end": RESEARCH_END.isoformat(),
                "validation_end": VALIDATION_END.isoformat(),
                "final_holdout_accessed": False,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
