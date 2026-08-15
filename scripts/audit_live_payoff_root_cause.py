"""Build a read-only R-normalised payoff audit from existing Testnet artifacts."""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

ZERO = Decimal("0")


def _d(value: object | None) -> Decimal | None:
    if value in (None, ""):
        return None
    return Decimal(str(value))


def _dt(value: object | None) -> datetime | None:
    if value in (None, ""):
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _load_facts(path: Path) -> dict[str, list[dict[str, Any]]]:
    facts: dict[str, list[dict[str, Any]]] = defaultdict(list)
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            facts[str(row.get("fact_type", "unknown"))].append(row)
    return facts


def _match_position(episode: dict[str, str], positions: list[dict[str, Any]]) -> dict[str, Any] | None:
    entry_time = _dt(episode.get("entry_time"))
    entry_price = _d(episode.get("entry_price")) or ZERO
    quantity = _d(episode.get("entry_quantity")) or ZERO
    symbol = episode.get("symbol")
    direction = episode.get("direction")
    candidates: list[tuple[float, dict[str, Any]]] = []
    for row in positions:
        if row.get("symbol") != symbol or row.get("direction") != direction:
            continue
        price = _d(row.get("entry_price")) or ZERO
        row_quantity = _d(row.get("quantity")) or ZERO
        projected = _dt(row.get("projected_at"))
        if price <= ZERO or row_quantity <= ZERO or projected is None or entry_time is None:
            continue
        if abs(price - entry_price) / entry_price > Decimal("0.0002"):
            continue
        if abs(row_quantity - quantity) / quantity > Decimal("0.01"):
            continue
        candidates.append((abs((projected - entry_time).total_seconds()), row))
    return min(candidates, key=lambda item: item[0])[1] if candidates else None


def _match_order(position: dict[str, Any], orders: list[dict[str, Any]]) -> dict[str, Any] | None:
    record_id = position.get("order_record_id")
    return next((row for row in orders if row.get("order_record_id") == record_id), None)


def _match_protection(position: dict[str, Any], protections: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next((row for row in protections if row.get("position_id") == position.get("position_id")), None)


def _planned_geometry(
    episode: dict[str, str], protection: dict[str, Any] | None
) -> tuple[Decimal | None, Decimal | None]:
    if protection is None:
        return None, None
    stop = _d(protection.get("stop_loss_price"))
    target = _d(protection.get("take_profit_price"))
    return stop, target


def _row(
    episode: dict[str, str],
    *,
    position: dict[str, Any] | None,
    order: dict[str, Any] | None,
    protection: dict[str, Any] | None,
) -> dict[str, Any]:
    entry = _d(episode.get("entry_price")) or ZERO
    quantity = _d(episode.get("entry_quantity")) or ZERO
    exit_price = _d(episode.get("exit_price"))
    commission = _d(episode.get("commission")) or ZERO
    funding = _d(episode.get("funding")) or ZERO
    realized = _d(episode.get("realized_pnl")) or ZERO
    net = _d(episode.get("net_pnl")) or (realized - commission + funding)
    leverage = _d((order or {}).get("leverage"))
    stop, target = _planned_geometry(episode, protection)
    risk_per_unit = abs(entry - stop) if stop is not None else None
    initial_risk = risk_per_unit * quantity if risk_per_unit is not None else None
    planned_rr = (
        abs(target - entry) / risk_per_unit if target is not None and risk_per_unit and risk_per_unit > ZERO else None
    )
    mfe_pct = _d(episode.get("mfe_pct"))
    mae_pct = _d(episode.get("mae_pct"))
    mfe_price = mfe_pct / Decimal("100") * entry if mfe_pct is not None else None
    mae_price = mae_pct / Decimal("100") * entry if mae_pct is not None else None
    gross_r = realized / initial_risk if initial_risk and initial_risk > ZERO else None
    net_r = net / initial_risk if initial_risk and initial_risk > ZERO else None
    mfe_r = (
        mfe_price / initial_risk * quantity if mfe_price is not None and initial_risk and initial_risk > ZERO else None
    )
    mae_r = (
        mae_price / initial_risk * quantity if mae_price is not None and initial_risk and initial_risk > ZERO else None
    )
    realized_move_pct = (
        (
            (exit_price - entry)
            / entry
            * (Decimal("1") if episode.get("direction") == "long" else Decimal("-1"))
            * Decimal("100")
        )
        if exit_price
        else None
    )
    giveback_pct = (
        max(ZERO, mfe_pct - realized_move_pct) if mfe_pct is not None and realized_move_pct is not None else None
    )
    giveback_r = (
        giveback_pct / Decimal("100") * entry * quantity / initial_risk
        if giveback_pct is not None and initial_risk and initial_risk > ZERO
        else None
    )
    return {
        "episode_id": episode.get("episode_id"),
        "symbol": episode.get("symbol"),
        "direction": episode.get("direction"),
        "entry_time": episode.get("entry_time"),
        "exit_time": episode.get("exit_time"),
        "exit_reason": episode.get("exit_reason"),
        "entry_price": str(entry),
        "entry_quantity": str(quantity),
        "entry_notional": str(entry * quantity),
        "entry_equity": None,
        "entry_equity_source": "NOT_PERSISTED_IN_V2_FACTS",
        "leverage": str(leverage) if leverage is not None else None,
        "planned_stop": str(stop) if stop is not None else None,
        "planned_tp": str(target) if target is not None else None,
        "stop_distance": str(risk_per_unit) if risk_per_unit is not None else None,
        "tp_distance": str(abs(target - entry)) if target is not None else None,
        "planned_rr": str(planned_rr) if planned_rr is not None else None,
        "initial_risk_usdt": str(initial_risk) if initial_risk is not None else None,
        "realized_gross_pnl": str(realized),
        "commission": str(commission),
        "funding": str(funding),
        "total_cost_drag_usdt": str(commission - funding),
        "net_pnl": str(net),
        "gross_R": str(gross_r) if gross_r is not None else None,
        "net_R": str(net_r) if net_r is not None else None,
        "MFE_R": str(mfe_r) if mfe_r is not None else None,
        "MAE_R": str(mae_r) if mae_r is not None else None,
        "giveback_pct": str(giveback_pct) if giveback_pct is not None else None,
        "giveback_R": str(giveback_r) if giveback_r is not None else None,
        "position_id": (position or {}).get("position_id"),
        "order_record_id": (position or {}).get("order_record_id"),
        "intent_id": (position or {}).get("intent_id"),
    }


def _mean(rows: list[dict[str, Any]], key: str) -> Decimal | None:
    values = [_d(row.get(key)) for row in rows]
    values = [value for value in values if value is not None]
    return sum(values, ZERO) / Decimal(len(values)) if values else None


def _sum(rows: list[dict[str, Any]], key: str) -> Decimal:
    return sum((_d(row.get(key)) or ZERO for row in rows), ZERO)


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    winners = [row for row in rows if (_d(row.get("net_pnl")) or ZERO) > ZERO]
    losers = [row for row in rows if (_d(row.get("net_pnl")) or ZERO) < ZERO]
    return {
        "trades": len(rows),
        "average_planned_rr": str(_mean(rows, "planned_rr")),
        "average_initial_risk_usdt": str(_mean(rows, "initial_risk_usdt")),
        "average_quantity": str(_mean(rows, "entry_quantity")),
        "average_leverage": str(_mean(rows, "leverage")),
        "average_gross_R": str(_mean(rows, "gross_R")),
        "average_net_R": str(_mean(rows, "net_R")),
        "average_MFE_R": str(_mean(rows, "MFE_R")),
        "average_MAE_R": str(_mean(rows, "MAE_R")),
        "total_realized_gross_pnl": str(_sum(rows, "realized_gross_pnl")),
        "total_cost_drag_usdt": str(_sum(rows, "total_cost_drag_usdt")),
        "total_net_pnl": str(_sum(rows, "net_pnl")),
        "winners": len(winners),
        "losers": len(losers),
    }


def build(*, audit_dir: Path) -> dict[str, Any]:
    canonical = audit_dir / "canonical"
    reports = audit_dir / "reports"
    with (canonical / "trade_episodes.csv").open(encoding="utf-8", newline="") as handle:
        episodes = [
            row
            for row in csv.DictReader(handle)
            if row.get("strategy") == "testnet_sampling_v2" and row.get("status") == "CLOSED"
        ]
    facts = _load_facts(audit_dir / "raw" / "local_v2_facts.jsonl")
    positions = facts.get("position", [])
    orders = facts.get("order", [])
    rows = []
    for episode in episodes:
        position = _match_position(episode, positions)
        order = _match_order(position, orders) if position else None
        protection = _match_protection(position, facts.get("protection", [])) if position else None
        rows.append(_row(episode, position=position, order=order, protection=protection))
    matched = [row for row in rows if row.get("planned_stop") and row.get("planned_tp")]
    winners = [row for row in matched if (_d(row.get("net_pnl")) or ZERO) > ZERO]
    losers = [row for row in matched if (_d(row.get("net_pnl")) or ZERO) < ZERO]
    root_causes = {
        "LOSS_SIZE_IMBALANCE": {
            "winner_average_initial_risk_usdt": str(_mean(winners, "initial_risk_usdt")),
            "loser_average_initial_risk_usdt": str(_mean(losers, "initial_risk_usdt")),
            "loser_to_winner_risk_ratio": str(
                (_mean(losers, "initial_risk_usdt") or ZERO) / (_mean(winners, "initial_risk_usdt") or Decimal("1"))
            ),
        },
        "RR_GEOMETRY_BROKEN": {
            "winner_average_planned_rr": str(_mean(winners, "planned_rr")),
            "loser_average_planned_rr": str(_mean(losers, "planned_rr")),
            "winner_average_gross_R": str(_mean(winners, "gross_R")),
            "loser_average_gross_R": str(_mean(losers, "gross_R")),
        },
        "COST_DRAG": {
            "total_commission": str(_sum(matched, "commission")),
            "total_funding_effect": str(_sum(matched, "funding")),
            "total_cost_drag_usdt": str(_sum(matched, "total_cost_drag_usdt")),
            "winner_cost_drag_usdt": str(_sum(winners, "total_cost_drag_usdt")),
            "loser_cost_drag_usdt": str(_sum(losers, "total_cost_drag_usdt")),
        },
        "ENTRY_BAD_LOCATION": {
            "average_MFE_R": str(_mean(matched, "MFE_R")),
            "average_MAE_R": str(_mean(matched, "MAE_R")),
            "stop_average_MFE_R": str(_mean([row for row in matched if row.get("exit_reason") == "STOP"], "MFE_R")),
        },
        "PROFIT_GIVEBACK": {
            "average_giveback_R": str(_mean(matched, "giveback_R")),
            "episodes_with_MFE_R_over_0_5": sum((_d(row.get("MFE_R")) or ZERO) >= Decimal("0.5") for row in matched),
            "losing_episodes_with_MFE_R_over_0_5": sum(
                (_d(row.get("MFE_R")) or ZERO) >= Decimal("0.5") and (_d(row.get("net_pnl")) or ZERO) < ZERO
                for row in matched
            ),
        },
    }
    output_rows = canonical / "trade_episode_r_normalized.csv"
    fields = list(rows[0]) if rows else []
    with output_rows.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    payload = {
        "status": "LIVE_PAYOFF_ROOT_CAUSE_COMPLETE"
        if len(matched) == len(episodes)
        else "LIVE_PAYOFF_ROOT_CAUSE_PARTIAL",
        "episodes": len(episodes),
        "geometry_matched": len(matched),
        "winners": _summary(winners),
        "losers": _summary(losers),
        "all": _summary(matched),
        "root_causes": root_causes,
        "notes": [
            "Entry equity is not persisted in the exported V2 facts and is intentionally NOT_PERSISTED.",
            "Initial risk uses the confirmed post-fill protection stop and actual entry quantity.",
            "Gross PnL is exchange realisedPnl; net PnL is realisedPnl - commission + funding.",
        ],
    }
    (reports / "live_payoff_root_cause.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Live Payoff Root Cause",
        "",
        f"- status: **{payload['status']}**",
        f"- closed V2 episodes: {len(episodes)}",
        f"- geometry matched: {len(matched)}",
        "",
        "## Winners (net PnL > 0)",
        *(f"- {key}: {value}" for key, value in payload["winners"].items()),
        "",
        "## Losers (net PnL < 0)",
        *(f"- {key}: {value}" for key, value in payload["losers"].items()),
        "",
        "## Root Causes",
        *(f"- {cause}: {json.dumps(values, ensure_ascii=False)}" for cause, values in root_causes.items()),
        "",
        "Entry equity: NOT_PERSISTED_IN_V2_FACTS.",
    ]
    (reports / "live_payoff_root_cause.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-dir", type=Path, default=Path("artifacts/trading_audit"))
    args = parser.parse_args()
    payload = build(audit_dir=args.audit_dir)
    print(payload["status"])
    print(json.dumps({"episodes": payload["episodes"], "geometry_matched": payload["geometry_matched"]}))
    return 0 if payload["status"] == "LIVE_PAYOFF_ROOT_CAUSE_COMPLETE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
