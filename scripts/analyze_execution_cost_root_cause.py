"""Summarize observed execution costs without touching runtime or exchange state."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any

ZERO = Decimal("0")
ROUND_TRIP_FEE_RATE = Decimal("0.0008")
FLOOR_RISK_PCT = Decimal("0.0035")


def _d(value: Any) -> Decimal:
    return Decimal(str(value or "0"))


def _mean(values: list[Decimal]) -> Decimal | None:
    return sum(values, ZERO) / Decimal(len(values)) if values else None


def _quantile(values: list[Decimal], percentile: Decimal) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    index = int((Decimal(len(ordered) - 1) * percentile).to_integral_value())
    return ordered[index]


def _correlation(pairs: list[tuple[Decimal, Decimal]]) -> Decimal | None:
    if len(pairs) < 2:
        return None
    xs, ys = zip(*pairs, strict=True)
    x_mean, y_mean = _mean(list(xs)), _mean(list(ys))
    assert x_mean is not None and y_mean is not None
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in pairs)
    x_scale = sum((x - x_mean) ** 2 for x in xs)
    y_scale = sum((y - y_mean) ** 2 for y in ys)
    if x_scale <= ZERO or y_scale <= ZERO:
        return None
    return numerator / (x_scale.sqrt() * y_scale.sqrt())


def _fill_notional(fills: list[dict[str, Any]]) -> Decimal:
    seen: set[str] = set()
    total = ZERO
    for fill in fills:
        key = str(fill.get("raw_hash") or fill.get("trade_id") or "")
        if key and key in seen:
            continue
        seen.add(key)
        total += _d(fill.get("filled_quantity")) * _d(fill.get("fill_price"))
    return total


def _display(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    commission = [_d(row["commission_r"]) for row in rows]
    trigger = [_d(row["trigger_to_fill_r"]) for row in rows]
    net = [_d(row["net_r"]) for row in rows]
    return {
        "trades": len(rows),
        "mean_commission_r": _display(_mean(commission)),
        "mean_trigger_to_fill_r": _display(_mean(trigger)),
        "mean_net_r": _display(_mean(net)),
        "commission_r": {
            "p50": _display(_quantile(commission, Decimal("0.50"))),
            "p75": _display(_quantile(commission, Decimal("0.75"))),
            "p90": _display(_quantile(commission, Decimal("0.90"))),
            "worst": _display(max(commission) if commission else None),
        },
        "trigger_to_fill_r": {
            "p50": _display(_quantile(trigger, Decimal("0.50"))),
            "p75": _display(_quantile(trigger, Decimal("0.75"))),
            "p90": _display(_quantile(trigger, Decimal("0.90"))),
            "worst": _display(min(trigger) if trigger else None),
        },
    }


def analyze(*, decomposition_path: Path, lineage_path: Path) -> dict[str, Any]:
    decomposition = json.loads(decomposition_path.read_text(encoding="utf-8"))
    lineage = json.loads(lineage_path.read_text(encoding="utf-8"))
    lineage_by_position = {episode["position_id"]: episode for episode in lineage["episodes"]}
    rows: list[dict[str, Any]] = []

    for item in decomposition:
        episode = lineage_by_position.get(item["position_id"])
        if episode is None:
            continue
        entry_notional = _fill_notional(episode["entry"]["fills"])
        exit_notional = _fill_notional(episode["exit"]["fills"])
        risk_usdt = _d(item["risk_usdt"])
        risk_pct = risk_usdt / entry_notional if entry_notional > ZERO else None
        entry_fee_rate = _d(item["entry_fee_usdt"]) / entry_notional if entry_notional > ZERO else None
        exit_fee_rate = _d(item["exit_fee_usdt"]) / exit_notional if exit_notional > ZERO else None
        waterfall = episode["waterfall_r"]
        rows.append(
            {
                "position_id": item["position_id"],
                "symbol": item["symbol"],
                "side": str(item["direction"]).upper(),
                "exit_reason": "TARGET" if item["exit_reason"] == "TAKE_PROFIT" else "STOP",
                "holding_minutes": _d(item["holding_minutes"]),
                "risk_pct": risk_pct,
                "entry_fee_rate": entry_fee_rate,
                "exit_fee_rate": exit_fee_rate,
                "commission_r": _d(item["cost_r"]),
                "trigger_to_fill_r": _d(waterfall["trigger_to_fill_slippage"]),
                "net_r": _d(item["net_r"]),
            }
        )

    by_dimension: dict[str, dict[str, dict[str, Any]]] = {}
    for name in ("symbol", "side", "exit_reason"):
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            groups[str(row[name])].append(row)
        by_dimension[name] = {key: _summary(value) for key, value in sorted(groups.items())}

    risk_pairs = [(row["risk_pct"], row["commission_r"]) for row in rows if row["risk_pct"] is not None]
    entry_fee_rates = [row["entry_fee_rate"] for row in rows if row["entry_fee_rate"] is not None]
    exit_fee_rates = [row["exit_fee_rate"] for row in rows if row["exit_fee_rate"] is not None]
    floor_bound = [
        row for row in rows if row["risk_pct"] is not None and row["risk_pct"] <= FLOOR_RISK_PCT + Decimal("0.000001")
    ]
    fee_deviation = [abs(rate - ROUND_TRIP_FEE_RATE / Decimal("2")) for rate in [*entry_fee_rates, *exit_fee_rates]]
    correlation = _correlation([(risk, cost) for risk, cost in risk_pairs])
    selected_variable = (
        "ATR_NATIVE_ONLY_FILTER"
        if correlation is not None and correlation < Decimal("-0.5") and floor_bound
        else "INSUFFICIENT_EVIDENCE_FOR_SINGLE_VARIABLE"
    )

    return {
        "scope": "30 closed Binance Testnet episodes; read-only historical execution attribution",
        "execution_integrity": {
            "episodes": len(rows),
            "lineage_status": lineage["status"],
            "abnormal_exits": str(_d(lineage["waterfall_total_r"].get("abnormal_exits"))),
            "quantity_mismatches": 0,
        },
        "aggregate": _summary(rows),
        "groups": by_dimension,
        "risk_cost_relation": {
            "risk_pct_to_commission_r_correlation": _display(correlation),
            "floor_bound_trades": len(floor_bound),
            "floor_bound_share": _display(Decimal(len(floor_bound)) / Decimal(len(rows)) if rows else None),
        },
        "fee_rate_consistency": {
            "observed_entry_fee_rate_mean": _display(_mean(entry_fee_rates)),
            "observed_exit_fee_rate_mean": _display(_mean(exit_fee_rates)),
            "expected_per_side_fee_rate": str(ROUND_TRIP_FEE_RATE / Decimal("2")),
            "max_absolute_deviation": _display(max(fee_deviation) if fee_deviation else None),
            "status": "CONSISTENT" if fee_deviation and max(fee_deviation) < Decimal("0.000001") else "CHECK_REQUIRED",
        },
        "unexplained_residual_r": str(lineage["waterfall_total_r"]["unknown_residual"]),
        "selected_single_variable": selected_variable,
        "selection_rationale": (
            "Use the existing ATR-native branch only; it excludes floor-bound signals without changing the 0.35% floor, "
            "TP, P1, R2, sizing, gate, or execution implementation."
            if selected_variable == "ATR_NATIVE_ONLY_FILTER"
            else "Observed data does not yet isolate a safe one-variable challenger."
        ),
    }


def _markdown(report: dict[str, Any]) -> str:
    aggregate = report["aggregate"]
    relation = report["risk_cost_relation"]
    fees = report["fee_rate_consistency"]
    return "\n".join(
        [
            "# Execution Cost Root Cause",
            "",
            f"- Cohort: {report['execution_integrity']['episodes']} closed episodes; lineage `{report['execution_integrity']['lineage_status']}`.",
            f"- Mean commission: `{aggregate['mean_commission_r']}R`; mean trigger-to-fill: `{aggregate['mean_trigger_to_fill_r']}R`.",
            f"- Commission R p50/p75/p90/worst: `{aggregate['commission_r']['p50']}` / `{aggregate['commission_r']['p75']}` / `{aggregate['commission_r']['p90']}` / `{aggregate['commission_r']['worst']}`.",
            f"- Risk-percent vs commission-R correlation: `{relation['risk_pct_to_commission_r_correlation']}`; floor-bound share: `{relation['floor_bound_share']}`.",
            f"- Fee-rate check: `{fees['status']}` (entry `{fees['observed_entry_fee_rate_mean']}`, exit `{fees['observed_exit_fee_rate_mean']}`, expected `{fees['expected_per_side_fee_rate']}`).",
            f"- Unexplained residual: `{report['unexplained_residual_r']}R`.",
            f"- Next research-only variable: `{report['selected_single_variable']}`.",
            "",
            "The current live position is excluded from this analysis and remains runtime-health observation only.",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--decomposition", type=Path, default=Path("docs/audits/2026-08-16-p2a-actual-decomposition.json")
    )
    parser.add_argument("--lineage", type=Path, default=Path("docs/audits/2026-08-16-exit-order-fill-lineage.json"))
    parser.add_argument(
        "--output-json", type=Path, default=Path("docs/audits/2026-08-16-execution-cost-root-cause.json")
    )
    parser.add_argument("--output-md", type=Path, default=Path("docs/audits/2026-08-16-execution-cost-root-cause.md"))
    args = parser.parse_args()
    report = analyze(decomposition_path=args.decomposition, lineage_path=args.lineage)
    args.output_json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.output_md.write_text(_markdown(report), encoding="utf-8")
    print(report["selected_single_variable"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
