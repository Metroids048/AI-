"""Build Gate 5B's read-only failure matrix from V2 execution facts.

This is descriptive research evidence.  It neither changes a strategy nor turns
unknown funding/slippage into a negative or positive result.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from services.research.exit_policy_shadow.loader import build_point_in_time_context, classify_entry_regime, load_bars

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE = ROOT / ".local_paper_console.db"
DEFAULT_FORENSICS = ROOT / ".local/trade-lifecycle-forensics-cost-truth.json"
DEFAULT_OUTPUT = ROOT / ".local/gate5-failure-decomposition.json"


def _decimal(value: Any) -> Decimal | None:
    return Decimal(str(value)) if value is not None else None


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _bucket(value: Decimal | float | None, *, low: Decimal, high: Decimal) -> str:
    if value is None:
        return "UNKNOWN"
    numeric = Decimal(str(value))
    if numeric < low:
        return "LOW"
    if numeric < high:
        return "MEDIUM"
    return "HIGH"


def _holding_bucket(minutes: Decimal | None) -> str:
    if minutes is None:
        return "UNKNOWN"
    if minutes <= 60:
        return "LE_1H"
    if minutes <= 240:
        return "_1H_TO_4H"
    return "GT_4H"


def _direction_accuracy(database: Path, *, row: dict[str, Any], horizon: timedelta) -> bool | None:
    if row.get("entry_time") is None or row.get("entry_price") is None:
        return None
    entry_time = _time(str(row["entry_time"]))
    target = entry_time + horizon
    bars = load_bars(database, symbol=str(row["symbol"]), timeframe="1m", start=entry_time, end=target)
    if not bars or bars[-1].time < target - timedelta(minutes=1):
        return None
    entry = _decimal(row["entry_price"])
    if entry is None:
        return None
    return bars[-1].close > entry if row["direction"] == "long" else bars[-1].close < entry


def _entry_features(database: Path, row: dict[str, Any]) -> dict[str, Any]:
    if row.get("decision_bar") is None:
        return {"regime": "UNKNOWN", "trend_strength": None, "volatility_expansion": None, "entry_extension_atr": None}
    try:
        decision_bar = _time(str(row["decision_bar"]))
        context = build_point_in_time_context(database, symbol=str(row["symbol"]), decision_bar=decision_bar)
        label = classify_entry_regime(database, symbol=str(row["symbol"]), decision_bar=decision_bar)
    except (KeyError, ValueError):
        return {"regime": "UNKNOWN", "trend_strength": None, "volatility_expansion": None, "entry_extension_atr": None}
    atr = context.volatility.atr_14
    signal_close = context.bars_15m.bars[-1].close if context.bars_15m.bars else None
    entry = _decimal(row.get("entry_price"))
    extension = (
        abs(entry - signal_close) / atr if entry is not None and signal_close is not None and atr and atr > 0 else None
    )
    return {
        "regime": label.regime.value,
        "trend_strength": Decimal(str(label.direction_score)),
        "volatility_expansion": Decimal(str(label.expansion)),
        "entry_extension_atr": extension,
    }


def annotate_episode(database: Path, row: dict[str, Any]) -> dict[str, Any]:
    features = _entry_features(database, row)
    entry_time = _time(str(row["entry_time"])) if row.get("entry_time") else None
    exit_time = _time(str(row["exit_time"])) if row.get("exit_time") else None
    holding_minutes = Decimal(str((exit_time - entry_time).total_seconds() / 60)) if entry_time and exit_time else None
    direction_accuracy = {
        label: _direction_accuracy(database, row=row, horizon=horizon)
        for label, horizon in {
            "30m": timedelta(minutes=30),
            "1h": timedelta(hours=1),
            "2h": timedelta(hours=2),
            "4h": timedelta(hours=4),
        }.items()
    }
    entry_price = _decimal(row.get("entry_price"))
    stop_price = _decimal(row.get("initial_stop"))
    quantity = _decimal(row.get("quantity"))
    risk = (
        abs(entry_price - stop_price) * quantity
        if entry_price is not None and stop_price is not None and quantity is not None
        else None
    )
    commission = _decimal(row.get("cost_truth", {}).get("commission_usdt"))
    return {
        **row,
        "taxonomy_primary": str(row.get("taxonomy", {}).get("primary", "UNKNOWN")),
        "features": {
            **{key: str(value) if isinstance(value, Decimal) else value for key, value in features.items()},
            "volatility_regime": _bucket(features["volatility_expansion"], low=Decimal("0.33"), high=Decimal("0.66")),
            "trend_strength_regime": _bucket(features["trend_strength"], low=Decimal("0.33"), high=Decimal("0.66")),
            "entry_extension_atr_regime": _bucket(
                features["entry_extension_atr"], low=Decimal("0.25"), high=Decimal("0.75")
            ),
            "holding_minutes": str(holding_minutes) if holding_minutes is not None else None,
            "holding_horizon": _holding_bucket(holding_minutes),
        },
        "direction_accuracy": direction_accuracy,
        "fees_r": str(commission / risk) if commission is not None and risk is not None and risk > 0 else None,
    }


def _slice(rows: list[dict[str, Any]], field: str) -> dict[str, dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        value = row.get(field) if field in row else row.get("features", {}).get(field)
        buckets[str(value if value is not None else "UNKNOWN")].append(row)
    result: dict[str, dict[str, Any]] = {}
    for value, items in sorted(buckets.items()):
        net = [_decimal(item.get("cost_truth", {}).get("net_before_funding_usdt")) for item in items]
        known_net = [value for value in net if value is not None]
        result[value] = {
            "episodes": len(items),
            "wins_before_funding": sum(value > 0 for value in known_net),
            "net_before_funding_usdt": str(sum(known_net, Decimal("0"))) if known_net else None,
            "taxonomy": dict(sorted(Counter(item["taxonomy"]["primary"] for item in items).items())),
        }
    return result


def rank_root_causes(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rank observed failure labels; unobserved cost truth is a limitation, not a cause."""
    causes = ("DIRECTION_FAILURE", "STOP_GEOMETRY_FAILURE", "ENTRY_TIMING_FAILURE", "EXECUTION_FAILURE")
    counts = Counter(str(row.get("taxonomy", {}).get("primary")) for row in rows)
    return [
        {
            "rank": rank,
            "root_cause": cause,
            "episodes": counts[cause],
            "evidence": f"{counts[cause]} of {len(rows)} authoritative closed V2 episodes carry taxonomy {cause}",
        }
        for rank, cause in enumerate(sorted(causes, key=lambda cause: (-counts[cause], cause)), start=1)
        if counts[cause]
    ]


def build_report(*, database: Path, forensics: Path) -> dict[str, Any]:
    payload = json.loads(forensics.read_text(encoding="utf-8"))
    rows = [annotate_episode(database, row) for row in payload["episodes"]]
    accuracy: dict[str, dict[str, Any]] = {
        horizon: {
            "known": sum(row["direction_accuracy"][horizon] is not None for row in rows),
            "correct": sum(row["direction_accuracy"][horizon] is True for row in rows),
        }
        for horizon in ("30m", "1h", "2h", "4h")
    }
    for summary in accuracy.values():
        summary["rate"] = summary["correct"] / summary["known"] if summary["known"] else None
    return {
        "schema_version": 1,
        "status": "READ_ONLY_FAILURE_DECOMPOSITION",
        "holdout_accessed": False,
        "source_forensics": str(forensics),
        "episode_count": len(rows),
        "cost_truth_coverage": dict(sorted(Counter(row["cost_truth"]["net_pnl_status"] for row in rows).items())),
        "direction_accuracy": accuracy,
        "failure_root_cause_ranking": rank_root_causes(rows),
        "slices": {
            "symbol": _slice(rows, "symbol"),
            "side": _slice(rows, "direction"),
            "market_regime": _slice(rows, "regime"),
            "volatility_regime": _slice(rows, "volatility_regime"),
            "trend_strength": _slice(rows, "trend_strength_regime"),
            "entry_extension_atr": _slice(rows, "entry_extension_atr_regime"),
            "holding_horizon": _slice(rows, "holding_horizon"),
            "exit_reason": _slice(rows, "exit_reason"),
            "failure_type": _slice(rows, "taxonomy_primary"),
        },
        "limitations": [
            "Funding is position-unattributable in the current account-level ledger, so net PnL including funding is UNKNOWN.",
            "Execution slippage is available only where an immutable signal reference survived; market movement is not inferred as slippage.",
            "The 0.33/0.66 descriptive score buckets are reporting labels, not strategy parameters or promotion thresholds.",
        ],
        "episodes": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--forensics", type=Path, default=DEFAULT_FORENSICS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = build_report(database=args.database, forensics=args.forensics)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {"episodes": report["episode_count"], "ranking": report["failure_root_cause_ranking"]}, ensure_ascii=False
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
