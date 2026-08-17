"""Build a read-only Aug 16, 2026 Testnet loss attribution report."""

from __future__ import annotations

import csv
import json
import sqlite3
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any

DB_PATH = Path(".local_paper_console.db")
REPORT_DIR = Path("docs/evidence/loss-attribution")
DATE_START = "2026-08-16 00:00:00"
DATE_END = "2026-08-17 00:00:00"


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    return Decimal(str(value))


def _json(value: Any) -> Any:
    if value is None:
        return None
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return value


def _open_readonly(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _load_episodes(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    positions = connection.execute(
        """
        select position_id, intent_id, order_record_id, symbol, direction, execution_mode,
               quantity, entry_price, entry_fee, state, projected_at, protected_at,
               closed_at, realized_pnl
        from v2_managed_positions
        where state = 'CLOSED' and closed_at >= ? and closed_at < ?
        order by closed_at
        """,
        (DATE_START, DATE_END),
    ).fetchall()
    episodes: list[dict[str, Any]] = []
    for position in positions:
        intent = connection.execute(
            "select * from v2_execution_intents where intent_id = ?",
            (position["intent_id"],),
        ).fetchone()
        order = connection.execute(
            "select * from v2_exchange_orders where order_record_id = ?",
            (position["order_record_id"],),
        ).fetchone()
        entry_fills = connection.execute(
            """
            select * from v2_exchange_fills
            where intent_id = ? and reduce_only = 0
            order by exchange_event_time
            """,
            (position["intent_id"],),
        ).fetchall()
        protection = connection.execute(
            "select * from v2_protection_records where position_id = ? order by created_at desc limit 1",
            (position["position_id"],),
        ).fetchone()
        # Exit fills belong to a dedicated exit intent whose candidate_key is
        # `exit:{position_id}:{reason}` (see scripts/build_exit_order_fill_lineage.py).
        # The protection row's stop/tp ids are algo order ids and never equal the
        # executed reduce-only order id, so they must not be used for this join.
        exit_fills = connection.execute(
            """
            select f.*, i.candidate_key as exit_candidate_key
            from v2_exchange_fills f
            join v2_execution_intents i on i.intent_id = f.intent_id
            where i.candidate_key like ? and f.reduce_only = 1
            order by f.exchange_event_time, f.trade_id
            """,
            (f"exit:{position['position_id']}:%",),
        ).fetchall()
        decision = None
        if intent is not None and intent["decision_id"]:
            decision = connection.execute(
                "select * from v2_execution_decisions where decision_id = ?",
                (intent["decision_id"],),
            ).fetchone()
        episode = dict(position)
        episode.update(
            {
                "episode_id": position["position_id"],
                "candidate_key": intent["candidate_key"] if intent else None,
                "decision_bar_timestamp": intent["decision_bar_timestamp"] if intent else None,
                "entry_order_id": order["exchange_order_id"] if order else None,
                "entry_order_filled_quantity": order["filled_quantity"] if order else None,
                "entry_average_fill_price": order["average_fill_price"] if order else None,
                "entry_fill_ids": [dict(row) for row in entry_fills],
                "exit_fill_ids": [dict(row) for row in exit_fills],
                "exit_reason": sorted({str(row["exit_candidate_key"]).rsplit(":", 1)[-1] for row in exit_fills})
                or ["MISSING_EXIT_FILL"],
                "protection": dict(protection) if protection else None,
                "decision_payload": _json(decision["payload"]) if decision else None,
                "funding": None,
                "slippage_estimate": None,
                "mfe": None,
                "mae": None,
                "mfe_r": None,
                "mae_r": None,
                "equity_at_entry": None,
                "initial_risk_usdt": None,
                "initial_risk_fraction": None,
            }
        )
        entry_price = _decimal(position["entry_price"])
        exit_price = _decimal(exit_fills[0]["fill_price"]) if exit_fills else None
        quantity = _decimal(position["quantity"])
        stop = _decimal(protection["original_stop_loss_price"]) if protection else None
        realized_pnl = _decimal(position["realized_pnl"])
        if entry_price is not None and stop is not None and quantity is not None:
            # Initial risk uses original_stop_loss_price, never the possibly-tightened
            # current stop, so net_r stays normalized by the risk actually taken at entry.
            initial_risk = abs(entry_price - stop) * quantity
            episode["initial_risk_usdt"] = str(initial_risk)
            episode["net_r"] = str(realized_pnl / initial_risk) if initial_risk and realized_pnl is not None else None
        if entry_price is not None and exit_price is not None and quantity is not None:
            gross = (
                (entry_price - exit_price) * quantity
                if position["direction"] == "short"
                else (exit_price - entry_price) * quantity
            )
            entry_fee = _decimal(position["entry_fee"]) or Decimal("0")
            exit_fee = sum((_decimal(row["commission"]) or Decimal("0") for row in exit_fills), Decimal("0"))
            episode["gross_price_pnl"] = str(gross)
            episode["known_commissions"] = str(entry_fee + exit_fee)
            episode["pnl_reconciliation_residual"] = str((realized_pnl or Decimal("0")) - gross + entry_fee + exit_fee)
        episodes.append(episode)
    return episodes


def _primary_cause(episode: dict[str, Any]) -> tuple[str, list[str], str]:
    realized = _decimal(episode.get("realized_pnl")) or Decimal("0")
    residual = _decimal(episode.get("pnl_reconciliation_residual"))
    if residual is not None and abs(residual) > Decimal("0.01"):
        return (
            "OTHER_EVIDENCED",
            ["COST_DRAG"],
            "Local realized_pnl differs from fill-price and commission reconstruction; funding/other exchange income is unavailable.",
        )
    if realized < 0:
        return (
            "OTHER_EVIDENCED",
            [],
            "Loss is evidenced by local CLOSED realized_pnl and exchange entry/exit fills; causal geometry is incomplete.",
        )
    return "OTHER_EVIDENCED", [], "No loss classification required."


def build_report(db_path: Path) -> dict[str, Any]:
    connection = _open_readonly(db_path)
    try:
        episodes = _load_episodes(connection)
    finally:
        connection.close()
    for episode in episodes:
        primary, contributing, evidence = _primary_cause(episode)
        episode["primary_cause"] = primary
        episode["contributing_factors"] = contributing
        episode["cause_evidence"] = evidence
    losses = [row for row in episodes if (_decimal(row.get("realized_pnl")) or Decimal("0")) < 0]
    total_realized = sum((_decimal(row.get("realized_pnl")) or Decimal("0") for row in episodes), Decimal("0"))
    return {
        "report_type": "A-001_REAL_LOSS_ATTRIBUTION",
        "status": "A001_PASS_WITH_LIMITATIONS" if episodes else "A001_BLOCKED_EVIDENCE",
        "scope": {"start_utc": DATE_START, "end_utc": DATE_END, "database": str(db_path)},
        "evidence": {
            "closed_episode_count": len(episodes),
            "loss_episode_count": len(losses),
            "exchange_fill_ids_present": all(row["entry_fill_ids"] and row["exit_fill_ids"] for row in episodes),
            "realized_pnl_present": all(row.get("realized_pnl") is not None for row in episodes),
            "protection_present": all(row.get("protection") is not None for row in episodes),
            "funding": "UNKNOWN",
            "account_equity_at_entry": "UNKNOWN",
            "complete_mfe_mae": "UNKNOWN: 1m coverage ends before all exits",
            "portfolio_timeline": "LIMITED: no complete intraday equity snapshot series",
        },
        "summary": {
            "total_realized_pnl_usdt": str(total_realized),
            "top_loss_symbols": Counter(row["symbol"] for row in losses).most_common(),
            "largest_loss_episode": min(losses, key=lambda row: _decimal(row["realized_pnl"]) or Decimal("0"))[
                "episode_id"
            ]
            if losses
            else None,
            "fixed_1_5R_change_supported": False,
            "execution_defect_confirmed": False,
        },
        "episodes": episodes,
        "limitations": [
            "No Aug 16 account equity snapshot was available in exchange_account_snapshots.",
            "Funding income/expense was not present in the local V2 episode contract.",
            "1m OHLCV coverage ended around 14:58-14:59 UTC, before the final ETH exit.",
            "MFE/MAE and full same-direction portfolio risk timeline cannot be reconstructed without complete intraday data.",
            "Cause labels remain conservative where geometry or funding evidence is missing.",
        ],
    }


def main() -> int:
    report = build_report(DB_PATH)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = REPORT_DIR / "2026-08-16-loss-attribution.json"
    csv_path = REPORT_DIR / "2026-08-16-episodes.csv"
    md_path = REPORT_DIR / "2026-08-16-loss-attribution.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        fields = [
            "episode_id",
            "symbol",
            "direction",
            "projected_at",
            "closed_at",
            "quantity",
            "entry_price",
            "realized_pnl",
            "initial_risk_usdt",
            "net_r",
            "primary_cause",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({field: row.get(field) for field in fields} for row in report["episodes"])
    lines = [
        "# A-001 2026-08-16 真实亏损归因",
        "",
        f"状态：`{report['status']}`。本报告只读读取 V2 本地投影与交易所回执记录，不写运行数据库。",
        "",
        "## 结论",
        f"- CLOSED episodes：{report['evidence']['closed_episode_count']}；亏损 episodes：{report['evidence']['loss_episode_count']}。",
        f"- 当日 local realized PnL 合计：`{report['summary']['total_realized_pnl_usdt']} USDT`。",
        "- 交易所 fill、保护记录和本地 realized PnL 可核验；但 funding、完整 MFE/MAE、权益基线和全天组合风险时间线未知。",
        "- 没有足够证据支持修改固定 1.5R；没有确认 execution defect。",
        "",
        "## Episode",
        "| Episode | Symbol | Direction | Entry | Exit | Realized PnL | Net R | Cause |",
        "|---|---|---|---:|---:|---:|---:|---|",
    ]
    for row in report["episodes"]:
        lines.append(
            f"| `{row['episode_id']}` | {row['symbol']} | {row['direction']} | {row['projected_at']} | {row['closed_at']} | {row['realized_pnl']} | {row.get('net_r', 'UNKNOWN')} | {row['primary_cause']} |"
        )
    lines.extend(["", "## Limitations", *[f"- {item}" for item in report["limitations"]], ""])
    md_path.write_text("\n".join(lines), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": report["status"],
                "episodes": len(report["episodes"]),
                "losses": len([r for r in report["episodes"] if (_decimal(r.get("realized_pnl")) or Decimal("0")) < 0]),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
