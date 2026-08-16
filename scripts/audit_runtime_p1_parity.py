"""Read-only R0-R3 parity audit for the P1 dynamic protection hypothesis.

The audit deliberately keeps the production execution path untouched.  It joins
the 30-position P2-A cohort to local V2 fills, protection events and 1m bars,
then measures a loss waterfall:

R0  existing Policy A replay (modeled fees/slippage)
R1  actual entry VWAP + static Policy A geometry (gross path)
R2  the same path with conservative, next-bar P1 stop tightening
R3  actual exchange exit fills (gross and commission-net)

P1 updates are applied only after a bar has survived its current stop/target;
the replacement is therefore effective from the next bar.  This avoids using
unknown intrabar ordering to grant a protection update a same-bar advantage.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from pathlib import Path
from typing import Any, cast

from services.automated_trading.application.risk_controls import p1_profit_protection
from services.research.exit_policy_shadow.contracts import Bar, ExitPolicyId, ExitReason, RealEntry, Regime
from services.research.exit_policy_shadow.loader import build_entry_context, load_bars
from services.research.exit_policy_shadow.policies import build_initial_geometry
from services.research.exit_policy_shadow.replay import replay_entry_under_policy

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / ".local_paper_console.db"
COHORT = ROOT / "docs/audits/2026-08-16-p2a-actual-decomposition.json"
OUT_JSON = ROOT / "docs/audits/2026-08-16-runtime-p1-parity.json"
OUT_MD = ROOT / "docs/audits/2026-08-16-runtime-p1-parity.md"


def dec(value: Any) -> Decimal:
    return Decimal(str(value or "0"))


def parse_dt(value: Any) -> datetime:
    result = datetime.fromisoformat(str(value).replace(" ", "T"))
    return result.replace(tzinfo=UTC) if result.tzinfo is None else result.astimezone(UTC)


def q(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.00000001")))


def json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (ExitReason, ExitPolicyId, Regime)):
        return value.value
    raise TypeError(f"unsupported JSON value: {type(value)!r}")


def tick_size(symbol: str) -> Decimal:
    # Historical protection prices in the V2 ledger carry the exchange precision:
    # BTC prices are one decimal, ETH prices are two decimals.
    return Decimal("0.1") if symbol == "BTC/USDT" else Decimal("0.01")


def round_stop(price: Decimal, *, direction: str, tick: Decimal) -> Decimal:
    rounding = ROUND_CEILING if direction == "long" else ROUND_FLOOR
    return (price / tick).to_integral_value(rounding=rounding) * tick


def gross_pnl(direction: str, entry: Decimal, exit_price: Decimal, quantity: Decimal) -> Decimal:
    return (exit_price - entry) * quantity if direction == "long" else (entry - exit_price) * quantity


@dataclass(frozen=True)
class PathOutcome:
    exit_price: Decimal
    exit_time: datetime
    reason: ExitReason
    gross_pnl: Decimal
    p1_triggers: tuple[dict[str, Any], ...] = ()
    ambiguous: bool = False


def static_path(
    *,
    entry: RealEntry,
    bars: list[Bar],
    stop: Decimal,
    target: Decimal,
) -> PathOutcome:
    for bar in bars:
        if bar.time < entry.fill_timestamp:
            continue
        hit_stop = bar.low <= stop if entry.side == "long" else bar.high >= stop
        hit_target = bar.high >= target if entry.side == "long" else bar.low <= target
        if hit_stop and hit_target:
            return PathOutcome(
                stop,
                bar.time,
                ExitReason.STOP,
                gross_pnl(entry.side, entry.average_fill_price, stop, entry.filled_quantity),
                ambiguous=True,
            )
        if hit_stop:
            return PathOutcome(
                stop,
                bar.time,
                ExitReason.STOP,
                gross_pnl(entry.side, entry.average_fill_price, stop, entry.filled_quantity),
            )
        if hit_target:
            return PathOutcome(
                target,
                bar.time,
                ExitReason.TARGET,
                gross_pnl(entry.side, entry.average_fill_price, target, entry.filled_quantity),
            )
    last = (
        bars[-1]
        if bars
        else Bar(
            time=entry.fill_timestamp,
            open=entry.average_fill_price,
            high=entry.average_fill_price,
            low=entry.average_fill_price,
            close=entry.average_fill_price,
            volume=Decimal("0"),
        )
    )
    return PathOutcome(
        last.close,
        last.time,
        ExitReason.DATA_EXHAUSTED,
        gross_pnl(entry.side, entry.average_fill_price, last.close, entry.filled_quantity),
    )


def dynamic_p1_path(
    *,
    entry: RealEntry,
    bars: list[Bar],
    original_stop: Decimal,
    target: Decimal,
    tick: Decimal,
) -> PathOutcome:
    stop = original_stop
    risk = abs(entry.average_fill_price - original_stop)
    p1_triggers: list[dict[str, Any]] = []
    applied_tier: Decimal = Decimal("0")

    for bar in bars:
        if bar.time < entry.fill_timestamp:
            continue
        hit_stop = bar.low <= stop if entry.side == "long" else bar.high >= stop
        hit_target = bar.high >= target if entry.side == "long" else bar.low <= target
        if hit_stop and hit_target:
            return PathOutcome(
                stop,
                bar.time,
                ExitReason.STOP,
                gross_pnl(entry.side, entry.average_fill_price, stop, entry.filled_quantity),
                tuple(p1_triggers),
                ambiguous=True,
            )
        if hit_stop:
            return PathOutcome(
                stop,
                bar.time,
                ExitReason.STOP,
                gross_pnl(entry.side, entry.average_fill_price, stop, entry.filled_quantity),
                tuple(p1_triggers),
            )
        if hit_target:
            return PathOutcome(
                target,
                bar.time,
                ExitReason.TARGET,
                gross_pnl(entry.side, entry.average_fill_price, target, entry.filled_quantity),
                tuple(p1_triggers),
            )

        favorable_extreme = bar.high if entry.side == "long" else bar.low
        decision = p1_profit_protection(
            direction=entry.side,
            entry_price=entry.average_fill_price,
            original_stop_price=original_stop,
            mark_price=favorable_extreme,
        )
        if decision.trigger_r is None or decision.trigger_r <= applied_tier or decision.stop_price is None:
            continue
        safe_stop = round_stop(decision.stop_price, direction=entry.side, tick=tick)
        tighter = safe_stop > stop if entry.side == "long" else safe_stop < stop
        if not tighter:
            continue
        applied_tier = decision.trigger_r
        stop = safe_stop
        p1_triggers.append(
            {
                "trigger_r": str(decision.trigger_r),
                "lock_r": str(decision.lock_r),
                "raw_stop_price": str(decision.stop_price),
                "rounded_stop_price": str(safe_stop),
                "observed_bar": bar.time.isoformat(),
                "effective_next_bar": True,
            }
        )

    last = (
        bars[-1]
        if bars
        else Bar(
            time=entry.fill_timestamp,
            open=entry.average_fill_price,
            high=entry.average_fill_price,
            low=entry.average_fill_price,
            close=entry.average_fill_price,
            volume=Decimal("0"),
        )
    )
    return PathOutcome(
        last.close,
        last.time,
        ExitReason.DATA_EXHAUSTED,
        gross_pnl(entry.side, entry.average_fill_price, last.close, entry.filled_quantity),
        tuple(p1_triggers),
    )


def load_rows(conn: sqlite3.Connection, position_ids: set[str]) -> list[dict[str, Any]]:
    placeholders = ",".join("?" for _ in position_ids)
    query = f"""
        SELECT mp.*, i.candidate_key, i.decision_bar_timestamp,
               o.average_fill_price, o.fill_timestamp, o.exchange_order_id
        FROM v2_managed_positions mp
        JOIN v2_execution_intents i ON i.intent_id = mp.intent_id
        JOIN v2_exchange_orders o ON o.order_record_id = mp.order_record_id
        WHERE mp.position_id IN ({placeholders})
        ORDER BY o.fill_timestamp
    """
    return [dict(row) for row in conn.execute(query, tuple(position_ids)).fetchall()]


def load_actual_exits(conn: sqlite3.Connection, position_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT f.*, i.candidate_key AS exit_candidate_key
        FROM v2_exchange_fills f
        JOIN v2_execution_intents i ON i.intent_id = f.intent_id
        WHERE i.candidate_key LIKE ? AND f.reduce_only = 1
        ORDER BY f.exchange_event_time, f.trade_id
        """,
        (f"exit:{position_id}:%",),
    ).fetchall()
    return [dict(row) for row in rows]


def load_protection(conn: sqlite3.Connection, position_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM v2_protection_records WHERE position_id=?", (position_id,)).fetchone()
    return dict(row) if row else None


def load_protection_events(conn: sqlite3.Connection, protection_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM v2_execution_events WHERE aggregate_id=? ORDER BY occurred_at",
        (protection_id,),
    ).fetchall()
    result = []
    for row in rows:
        payload = json.loads(row["event_payload"] or "{}")
        result.append({"event_type": row["event_type"], "occurred_at": row["occurred_at"], "payload": payload})
    return result


def r_multiple(pnl: Decimal, entry: Decimal, stop: Decimal, quantity: Decimal) -> Decimal:
    risk = abs(entry - stop) * quantity
    return pnl / risk if risk > 0 else Decimal("0")


def main() -> None:
    cohort = json.loads(COHORT.read_text(encoding="utf-8"))
    position_ids = {str(row["position_id"]) for row in cohort}
    conn = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    positions = load_rows(conn, position_ids)
    rows: list[dict[str, Any]] = []

    for position in positions:
        entry = RealEntry(
            position_id=position["position_id"],
            symbol=position["symbol"],
            side=position["direction"],
            average_fill_price=dec(position["average_fill_price"]),
            fill_timestamp=parse_dt(position["fill_timestamp"]),
            filled_quantity=dec(position["quantity"]),
            entry_fee_usdt=dec(position["entry_fee"]),
            candidate_key=position["candidate_key"],
            decision_bar_timestamp=parse_dt(position["decision_bar_timestamp"]),
            exchange_order_id=str(position["exchange_order_id"]),
        )
        context = build_entry_context(DB, symbol=entry.symbol, decision_bar=entry.decision_bar_timestamp)
        if context is None:
            rows.append({"position_id": entry.position_id, "status": "MISSING_ATR_CONTEXT"})
            continue
        bars = load_bars(
            DB,
            symbol=entry.symbol,
            timeframe="1m",
            start=entry.fill_timestamp,
            end=parse_dt(position["closed_at"]),
        )
        replay_bars = load_bars(
            DB,
            symbol=entry.symbol,
            timeframe="1m",
            start=entry.fill_timestamp,
            end=entry.fill_timestamp + timedelta(days=7),
        )
        stop, target = build_initial_geometry(
            policy=ExitPolicyId.CURRENT_CONTROL,
            side=entry.side,
            entry_price=entry.average_fill_price,
            entry_context=context,
            regime=Regime.UNKNOWN,
        )
        if target is None:
            raise RuntimeError(f"Policy A target missing for {entry.position_id}")
        protection = load_protection(conn, entry.position_id)
        runtime_stop = dec(protection["original_stop_loss_price"]) if protection else stop
        runtime_target = (
            dec(protection["take_profit_price"]) if protection and protection["take_profit_price"] else target
        )
        r0 = replay_entry_under_policy(
            entry=entry,
            bars=replay_bars,
            policy=ExitPolicyId.CURRENT_CONTROL,
            regime=Regime.UNKNOWN,
            entry_context=context,
        )
        r1 = static_path(entry=entry, bars=bars, stop=runtime_stop, target=runtime_target)
        r2 = dynamic_p1_path(
            entry=entry,
            bars=bars,
            original_stop=runtime_stop,
            target=runtime_target,
            tick=tick_size(entry.symbol),
        )
        actual_exits = load_actual_exits(conn, entry.position_id)
        actual_gross = sum(
            (
                gross_pnl(entry.side, entry.average_fill_price, dec(fill["fill_price"]), dec(fill["filled_quantity"]))
                for fill in actual_exits
            ),
            Decimal("0"),
        )
        actual_commission = sum((dec(fill["commission"]) for fill in actual_exits), Decimal("0"))
        actual_exit_time = parse_dt(actual_exits[-1]["exchange_event_time"]) if actual_exits else None
        protection_events = load_protection_events(conn, protection["protection_id"]) if protection else []
        replacement_events = [
            event for event in protection_events if event["event_type"] == "ProfitProtectionStopTightened"
        ]
        rows.append(
            {
                "position_id": entry.position_id,
                "symbol": entry.symbol,
                "direction": entry.side,
                "entry_time": entry.fill_timestamp,
                "closed_at": parse_dt(position["closed_at"]),
                "entry_price": entry.average_fill_price,
                "quantity": entry.filled_quantity,
                "initial_stop": stop,
                "initial_target": target,
                "runtime_initial_stop": runtime_stop,
                "runtime_initial_target": runtime_target,
                "risk_usdt": abs(entry.average_fill_price - stop) * entry.filled_quantity,
                "bars": len(bars),
                "replay_bars_7d": len(replay_bars),
                "r0_static_replay": {
                    "net_pnl_usdt": r0.net_pnl_usdt,
                    "net_r": r_multiple(r0.net_pnl_usdt, entry.average_fill_price, stop, entry.filled_quantity),
                    "gross_pnl_usdt": r0.gross_pnl_usdt,
                    "reason": r0.final_reason,
                    "exit_time": r0.legs[-1].filled_at if r0.legs else None,
                },
                "r1_static_actual_entry": {
                    "gross_pnl_usdt": r1.gross_pnl,
                    "gross_r": r_multiple(r1.gross_pnl, entry.average_fill_price, runtime_stop, entry.filled_quantity),
                    "reason": r1.reason,
                    "exit_price": r1.exit_price,
                    "exit_time": r1.exit_time,
                    "ambiguous_intrabar": r1.ambiguous,
                },
                "r2_dynamic_p1": {
                    "gross_pnl_usdt": r2.gross_pnl,
                    "gross_r": r_multiple(r2.gross_pnl, entry.average_fill_price, runtime_stop, entry.filled_quantity),
                    "reason": r2.reason,
                    "exit_price": r2.exit_price,
                    "exit_time": r2.exit_time,
                    "ambiguous_intrabar": r2.ambiguous,
                    "p1_triggered": bool(r2.p1_triggers),
                    "p1_trigger_count": len(r2.p1_triggers),
                    "p1_triggers": r2.p1_triggers,
                },
                "r3_actual_exchange": {
                    "exit_fill_count": len(actual_exits),
                    "exit_time": actual_exit_time,
                    "gross_pnl_usdt": actual_gross,
                    "gross_r": r_multiple(actual_gross, entry.average_fill_price, runtime_stop, entry.filled_quantity),
                    "commission_usdt": actual_commission,
                    "net_pnl_usdt": actual_gross - entry.entry_fee_usdt - actual_commission,
                    "net_r": r_multiple(
                        actual_gross - entry.entry_fee_usdt - actual_commission,
                        entry.average_fill_price,
                        runtime_stop,
                        entry.filled_quantity,
                    ),
                    "exit_reasons": sorted(
                        {str(fill["exit_candidate_key"]).rsplit(":", 1)[-1] for fill in actual_exits}
                    ),
                },
                "observed_runtime_p1": {
                    "policy": protection["policy"] if protection else None,
                    "final_stop": dec(protection["stop_loss_price"]) if protection else None,
                    "original_stop": dec(protection["original_stop_loss_price"]) if protection else None,
                    "replacement_count": len(replacement_events),
                    "replacement_events": replacement_events,
                },
                "status": "OK" if actual_exits else "MISSING_ACTUAL_EXIT_FILL",
            }
        )
    conn.close()

    valid = [row for row in rows if row.get("status") == "OK"]

    def mean(path: str) -> Decimal:
        values = [row[path.split(".")[0]][path.split(".")[1]] for row in valid]
        return sum((dec(value) for value in values), Decimal("0")) / Decimal(len(values)) if values else Decimal("0")

    means = {
        "r0_net_r": mean("r0_static_replay.net_r"),
        "r1_gross_r": mean("r1_static_actual_entry.gross_r"),
        "r2_gross_r": mean("r2_dynamic_p1.gross_r"),
        "r3_actual_gross_r": mean("r3_actual_exchange.gross_r"),
        "r3_actual_net_r": mean("r3_actual_exchange.net_r"),
    }

    def stage_pf(stage: str, field: str) -> Decimal | None:
        values = [dec(row[stage][field]) for row in valid]
        gains = sum((value for value in values if value > 0), Decimal("0"))
        losses = abs(sum((value for value in values if value < 0), Decimal("0")))
        return gains / losses if losses > 0 else None

    stage_pf_values = {
        "R0_gross_usdt": stage_pf("r0_static_replay", "gross_pnl_usdt"),
        "R0_net_usdt": stage_pf("r0_static_replay", "net_pnl_usdt"),
        "R1_gross_usdt": stage_pf("r1_static_actual_entry", "gross_pnl_usdt"),
        "R2_gross_usdt": stage_pf("r2_dynamic_p1", "gross_pnl_usdt"),
        "R3_gross_usdt": stage_pf("r3_actual_exchange", "gross_pnl_usdt"),
        "R3_net_usdt": stage_pf("r3_actual_exchange", "net_pnl_usdt"),
    }

    means_usdt = {
        "r0_net_usdt": mean("r0_static_replay.net_pnl_usdt"),
        "r1_gross_usdt": mean("r1_static_actual_entry.gross_pnl_usdt"),
        "r2_gross_usdt": mean("r2_dynamic_p1.gross_pnl_usdt"),
        "r3_actual_gross_usdt": mean("r3_actual_exchange.gross_pnl_usdt"),
        "r3_actual_net_usdt": mean("r3_actual_exchange.net_pnl_usdt"),
    }
    waterfall = {
        "r0_to_r1_entry_and_model_cost_effect": means["r1_gross_r"] - means["r0_net_r"],
        "r1_to_r2_p1_dynamic_effect": means["r2_gross_r"] - means["r1_gross_r"],
        "r2_to_r3_execution_and_fill_effect": means["r3_actual_net_r"] - means["r2_gross_r"],
        "r0_to_r3_total_net_effect": means["r3_actual_net_r"] - means["r0_net_r"],
    }
    payload = {
        "status": "COMPLETE" if len(valid) == len(position_ids) else "INCOMPLETE_COHORT",
        "scope": {
            "cohort_source": str(COHORT.relative_to(ROOT)),
            "cohort_size": len(position_ids),
            "rows": len(rows),
            "valid_rows_with_actual_exit": len(valid),
            "read_only": True,
            "final_holdout_read": False,
        },
        "semantics": {
            "R0": "Existing P2-A Policy A replay; actual entry VWAP, modeled exit fee and 1bp exit slippage.",
            "R1": "Actual entry VWAP and frozen Policy A stop/1.5R target; exact 1m path, gross only.",
            "R2": "R1 plus P1 0.60R->+0.05R and 1.00R->+0.40R stop locks, exchange tick rounding, conservative stop-first intrabar and next-bar effectiveness.",
            "R3": "Actual V2 exchange reduce-only fills; gross and commission-net, excluding funding.",
            "p1_observed": "Only ProfitProtectionStopTightened events count as historical runtime replacement evidence; a P1 policy label alone is not a replacement.",
        },
        "means_r": means,
        "means_usdt": means_usdt,
        "profit_factor": stage_pf_values,
        "waterfall_mean_r": waterfall,
        "counts": {
            "p1_simulated_triggered": sum(bool(row.get("r2_dynamic_p1", {}).get("p1_triggered")) for row in valid),
            "p1_simulated_triggered_but_r2_not_target": sum(
                bool(row.get("r2_dynamic_p1", {}).get("p1_triggered"))
                and row.get("r2_dynamic_p1", {}).get("reason") != ExitReason.TARGET
                for row in valid
            ),
            "p1_observed_replacement_rows": sum(
                row.get("observed_runtime_p1", {}).get("replacement_count", 0) > 0 for row in valid
            ),
            "p1_observed_replacement_events": sum(
                row.get("observed_runtime_p1", {}).get("replacement_count", 0) for row in valid
            ),
            "r0_reason": dict(Counter(str(row["r0_static_replay"]["reason"]) for row in valid)),
            "r1_reason": dict(Counter(str(row["r1_static_actual_entry"]["reason"]) for row in valid)),
            "r2_reason": dict(Counter(str(row["r2_dynamic_p1"]["reason"]) for row in valid)),
        },
        "rows": rows,
    }
    counts = cast(dict[str, Any], payload["counts"])
    OUT_JSON.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=json_default) + "\n", encoding="utf-8"
    )

    lines = [
        "# Runtime P1 Parity Audit",
        "",
        f"- Status: `{payload['status']}`; cohort `{len(position_ids)}`; valid actual-exit rows `{len(valid)}`.",
        "- Read-only: SQLite opened with `mode=ro`; no execution or risk configuration changed.",
        "",
        "## Mean R Waterfall",
        "",
        f"- R0 static Policy A replay net: `{q(means['r0_net_r'])}R`",
        f"- R1 actual-entry static path gross: `{q(means['r1_gross_r'])}R`",
        f"- R2 dynamic P1 path gross: `{q(means['r2_gross_r'])}R`",
        f"- R3 actual exchange gross: `{q(means['r3_actual_gross_r'])}R`; commission-net: `{q(means['r3_actual_net_r'])}R`",
        f"- Stage PF (USDT): R0 gross `{stage_pf_values['R0_gross_usdt']}`, R0 net `{stage_pf_values['R0_net_usdt']}`, R1 `{stage_pf_values['R1_gross_usdt']}`, R2 `{stage_pf_values['R2_gross_usdt']}`, R3 gross `{stage_pf_values['R3_gross_usdt']}`, R3 net `{stage_pf_values['R3_net_usdt']}`",
        f"- R1→R2 P1 effect: `{q(waterfall['r1_to_r2_p1_dynamic_effect'])}R`",
        f"- R2→R3 execution/fill effect: `{q(waterfall['r2_to_r3_execution_and_fill_effect'])}R`",
        "",
        "## P1 Evidence",
        "",
        f"- Simulated P1-triggered rows: `{counts['p1_simulated_triggered']}`; triggered but did not reach target: `{counts['p1_simulated_triggered_but_r2_not_target']}`.",
        f"- Historical `ProfitProtectionStopTightened` rows: `{counts['p1_observed_replacement_rows']}`; events: `{counts['p1_observed_replacement_events']}`.",
        "- The P1 policy label on a protection record is not treated as proof of a replacement; only the explicit protection event is.",
        "",
        "## Decision",
        "",
        "This artifact is the parity gate. If R2 remains materially below R3, continue exchange-fill/order-identity attribution before any signal or funding experiment. If historical replacement events are zero while simulated triggers are common, the next question is runtime observation cadence/mark-price availability, not signal quality.",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(OUT_JSON)
    print(OUT_MD)
    print(
        json.dumps(
            {
                "status": payload["status"],
                "means_r": {key: str(value) for key, value in means.items()},
                "means_usdt": {key: str(value) for key, value in means_usdt.items()},
                "profit_factor": {
                    key: str(value) if value is not None else None for key, value in stage_pf_values.items()
                },
                "waterfall_mean_r": {key: str(value) for key, value in waterfall.items()},
                "counts": payload["counts"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
