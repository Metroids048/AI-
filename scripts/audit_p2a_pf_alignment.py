"""Align P2-A local positions with exchange episodes before funding attribution.

Read-only evidence builder. It does not query the exchange or mutate SQLite. The
purpose is to make the cohort and profit-factor definitions explicit:

* P2-A: local ``v2_managed_positions`` with candidate ``testnet_sampling_v2`` and
  state ``CLOSED`` (the 30 positions replayed by P2-A).
* Audit PF: exchange ``TradeEpisode`` rows whose first/associated fill carried a
  local strategy context (the existing 32-episode report).

The two are intentionally reported side by side; they are not silently merged.
"""

from __future__ import annotations

import csv
import json
import sqlite3
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / ".local_paper_console.db"
DECOMP = ROOT / "docs/audits/2026-08-16-p2a-actual-decomposition.json"
P2A_RESULT = ROOT / "docs/audits/2026-08-11-p2a-exit-policy-shadow-results.json"
AUDIT_DIR = ROOT / "docs/audits/2026-08-16-testnet-history"
AUDIT_CSV = AUDIT_DIR / "canonical/trade_episodes.csv"
RAW_TRADES = AUDIT_DIR / "raw/binance_user_trades.jsonl"
RAW_ALGO = AUDIT_DIR / "raw/binance_algo_orders.jsonl"
OUT_JSON = ROOT / "docs/audits/2026-08-16-pf-cohort-alignment.json"
OUT_MD = ROOT / "docs/audits/2026-08-16-pf-cohort-alignment.md"


def dec(value: Any) -> Decimal:
    return Decimal(str(value or "0"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def pf(values: list[Decimal]) -> Decimal | None:
    wins = sum((value for value in values if value > 0), Decimal("0"))
    losses = abs(sum((value for value in values if value < 0), Decimal("0")))
    return wins / losses if losses else None


def load_db() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    conn = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        SELECT mp.position_id, mp.intent_id, mp.order_record_id, mp.symbol,
               mp.direction, mp.state, mp.closed_at, mp.entry_price, mp.entry_fee,
               i.candidate_key, o.exchange_order_id, o.fill_timestamp,
               o.trade_ids
        FROM v2_managed_positions mp
        JOIN v2_execution_intents i ON i.intent_id = mp.intent_id
        JOIN v2_exchange_orders o ON o.order_record_id = mp.order_record_id
        WHERE i.candidate_key = 'testnet_sampling_v2'
        ORDER BY o.fill_timestamp
        """
    )
    positions = [dict(row) for row in cur.fetchall()]
    cur.execute(
        """
        SELECT position_id, stop_exchange_order_id, tp_exchange_order_id,
               state, stop_loss_price, take_profit_price
        FROM v2_protection_records
        """
    )
    protections = [dict(row) for row in cur.fetchall()]
    conn.close()
    return positions, protections


def build() -> dict[str, Any]:
    decomposition = json.loads(DECOMP.read_text(encoding="utf-8"))
    p2a_result = json.loads(P2A_RESULT.read_text(encoding="utf-8"))
    audit_rows = list(csv.DictReader(AUDIT_CSV.open(encoding="utf-8", newline="")))
    trades = read_jsonl(RAW_TRADES)
    algo_orders = read_jsonl(RAW_ALGO)
    positions, protections = load_db()

    p2a_ids = {row["position_id"] for row in decomposition}
    closed = [row for row in positions if row["state"] == "CLOSED"]
    p2a_positions = [row for row in closed if row["position_id"] in p2a_ids]
    protection_by_position = {row["position_id"]: row for row in protections}
    actual_order_by_algo = {
        str(row["algoId"]): str(row["actualOrderId"])
        for row in algo_orders
        if row.get("algoId") and row.get("actualOrderId")
    }

    trades_by_order: dict[str, list[dict[str, Any]]] = {}
    for trade in trades:
        trades_by_order.setdefault(str(trade.get("orderId")), []).append(trade)

    aligned_rows: list[dict[str, Any]] = []
    for position in p2a_positions:
        protection = protection_by_position.get(position["position_id"], {})
        entry_order = str(position["exchange_order_id"])
        algo_ids = [
            str(value)
            for value in (protection.get("stop_exchange_order_id"), protection.get("tp_exchange_order_id"))
            if value
        ]
        actual_exit_orders = [actual_order_by_algo.get(value, value) for value in algo_ids]
        entry_trades = trades_by_order.get(entry_order, [])
        exit_trades = [trade for order_id in actual_exit_orders for trade in trades_by_order.get(order_id, [])]
        entry_commission = sum((dec(row.get("commission")) for row in entry_trades), Decimal("0"))
        exit_commission = sum((dec(row.get("commission")) for row in exit_trades), Decimal("0"))
        realized_gross = sum((dec(row.get("realizedPnl")) for row in [*entry_trades, *exit_trades]), Decimal("0"))
        net_excluding_funding = realized_gross - entry_commission - exit_commission
        aligned_rows.append(
            {
                "position_id": position["position_id"],
                "intent_id": position["intent_id"],
                "symbol": position["symbol"],
                "direction": position["direction"],
                "entry_time": position["fill_timestamp"],
                "entry_order_id": entry_order,
                "entry_trade_ids": [str(row.get("id")) for row in entry_trades],
                "exit_algo_order_ids": algo_ids,
                "exit_actual_order_ids": actual_exit_orders,
                "exit_trade_ids": [str(row.get("id")) for row in exit_trades],
                "entry_commission": str(entry_commission),
                "exit_commission": str(exit_commission),
                "realized_gross_pnl": str(realized_gross),
                "net_pnl_excluding_funding": str(net_excluding_funding),
                "entry_trade_count": len(entry_trades),
                "exit_trade_count": len(exit_trades),
                "exit_complete": bool(exit_trades),
            }
        )

    audit_v2 = [
        row for row in audit_rows if row.get("strategy") == "testnet_sampling_v2" and row.get("status") == "CLOSED"
    ]
    audit_by_intent = {row.get("v2_intent_id"): row for row in audit_v2 if row.get("v2_intent_id")}
    p2a_intents = {row["intent_id"] for row in p2a_positions}
    audit_extra = [row for row in audit_v2 if row.get("v2_intent_id") not in p2a_intents]
    audit_missing = [row for row in p2a_positions if row["intent_id"] not in audit_by_intent]
    quarantine_extras = [
        row
        for row in positions
        if row["state"] == "QUARANTINED" and row["intent_id"] in {item.get("v2_intent_id") for item in audit_extra}
    ]
    merged_episode = next((row for row in audit_rows if row.get("episode_id") == "ETH-USDT-0065"), None)

    aligned_values = [dec(row["net_pnl_excluding_funding"]) for row in aligned_rows if row["exit_complete"]]
    decomposition_values = [dec(row["net_r"]) for row in decomposition]
    aligned_gross = sum((dec(row["realized_gross_pnl"]) for row in aligned_rows), Decimal("0"))
    aligned_commission = sum(
        (dec(row["entry_commission"]) + dec(row["exit_commission"]) for row in aligned_rows), Decimal("0")
    )
    audit_values = [dec(row["net_pnl"]) for row in audit_v2]
    replay_pf = dec(p2a_result["overall"]["A_CURRENT_CONTROL"]["profit_factor"])
    payload = {
        "status": "COHORT_MISMATCH_CONFIRMED",
        "legacy_value_check": {
            "reported_value": "0.48688",
            "reproducible_in_current_repo": False,
            "current_30_row_normalized_r_pf": str(pf(decomposition_values)),
            "note": "The reported 0.48688 does not occur in the current repository/artifacts and cannot be reconstructed from the current 30-row decomposition without an external formula or prior artifact.",
        },
        "definitions": {
            "p2a_replay_pf": "Policy A replay PF over 30 local CLOSED testnet_sampling_v2 positions; modeled policy outcomes and modeled fee drag.",
            "p2a_aligned_actual_pf": "Exchange realizedPnl minus exchange commissions for those same 30 local CLOSED positions, before funding attribution.",
            "audit_pf": "Exchange TradeEpisode PF over 32 episodes whose episode local_context carries strategy=testnet_sampling_v2; net_pnl includes funding.",
        },
        "counts": {
            "p2a_decomposition_rows": len(decomposition),
            "db_candidate_positions": len(positions),
            "db_candidate_state_counts": dict(Counter(row["state"] for row in positions)),
            "audit_strategy_closed_episodes": len(audit_v2),
            "p2a_positions_present_in_audit_by_intent": len(p2a_positions) - len(audit_missing),
            "p2a_positions_missing_as_standalone_audit_episode": len(audit_missing),
            "audit_extra_vs_p2a": len(audit_extra),
        },
        "pf": {
            "p2a_replay_policy_a": str(replay_pf),
            "p2a_aligned_actual_pre_funding": str(pf(aligned_values)) if aligned_values else None,
            "p2a_aligned_actual_pre_funding_trade_count": len(aligned_values),
            "p2a_actual_decomposition_normalized_r": str(pf(decomposition_values)),
            "p2a_actual_decomposition_normalized_r_trade_count": len(decomposition_values),
            "p2a_aligned_actual_pre_funding_net_pnl_usdt": str(sum(aligned_values, Decimal("0"))),
            "p2a_aligned_actual_pre_funding_gross_pnl_usdt": str(aligned_gross),
            "p2a_aligned_actual_pre_funding_commission_usdt": str(aligned_commission),
            "audit_report_v2": "0.3965963545259739475978455318",
            "audit_recomputed_from_csv": str(pf(audit_values)),
        },
        "p2a_missing_standalone_audit_episode": [
            {
                "position_id": row["position_id"],
                "intent_id": row["intent_id"],
                "entry_time": row["fill_timestamp"],
                "entry_order_id": row["exchange_order_id"],
            }
            for row in audit_missing
        ],
        "audit_extra_episodes": [
            {
                "episode_id": row.get("episode_id"),
                "intent_id": row.get("v2_intent_id"),
                "entry_time": row.get("entry_time"),
                "net_pnl": row.get("net_pnl"),
                "local_order_id": row.get("local_order_id"),
            }
            for row in audit_extra
        ],
        "episode_merge_evidence": {
            "episode_id": merged_episode.get("episode_id") if merged_episode else None,
            "entry_time": merged_episode.get("entry_time") if merged_episode else None,
            "exit_time": merged_episode.get("exit_time") if merged_episode else None,
            "local_order_id": merged_episode.get("local_order_id") if merged_episode else None,
            "v2_intent_id": merged_episode.get("v2_intent_id") if merged_episode else None,
            "net_pnl": merged_episode.get("net_pnl") if merged_episode else None,
            "reason": "The 2026-08-10 10:15:44 ETH short entry is not a standalone audit episode; build_trade_episodes groups same-symbol account fills into one lifecycle, so it is absorbed into an earlier ETH episode with no V2 strategy context.",
        },
        "aligned_rows": aligned_rows,
        "audit_extra_rows": audit_extra,
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# P2-A / Binance PF Cohort Alignment",
        "",
        "## Verdict",
        "",
        "`COHORT_MISMATCH_CONFIRMED` — do not run the funding-only experiment yet.",
        "",
        "## Definitions",
        "",
        "- P2-A replay PF `1.1122990302449454`: Policy A replay over 30 local `CLOSED` positions; modeled policy outcomes and modeled fee drag.",
        "- Aligned actual PF: the same 30 local positions mapped to exchange entry/protection fills; realized PnL minus exchange commissions, before funding.",
        "- Audit PF `0.3965963545259739`: 32 exchange `TradeEpisode` rows carrying local strategy context; `net_pnl` includes funding.",
        "",
        "## Cohort evidence",
        "",
        f"- P2-A rows: {len(decomposition)}; DB candidate positions: {len(positions)} ({dict(Counter(row['state'] for row in positions))}).",
        f"- Audit V2 closed episodes: {len(audit_v2)}.",
        f"- Audit extras not in P2-A: {len(audit_extra)}; these are the quarantined entries at 2026-08-11 07:45:41 and 2026-08-14 16:00:16/16:00:25.",
        "- One P2-A position (ETH 2026-08-10 10:15:44.664000, intent `e80dc888-ebc8-4102-9fd3-a5971f604680`) has no standalone audit episode.",
        "- The missing standalone episode is an episode-construction issue, not evidence that the exchange fill is absent: the account-level episode builder groups same-symbol fills into one lifecycle and absorbs that ETH short into an earlier episode with no V2 strategy context.",
        "",
        "## PF result",
        "",
        f"- Aligned actual pre-funding PF: `{pf(aligned_values)}` over {len(aligned_values)} positions.",
        f"- P2-A decomposition normalized-R PF: `{pf(decomposition_values)}` over {len(decomposition_values)} positions. The previously cited `0.48688` is not present in the current repository/artifacts and is not reproducible from these rows; treat it as unsupported until its source/formula is recovered.",
        f"- Aligned actual pre-funding totals: gross `{aligned_gross}` USDT, commission `{aligned_commission}` USDT, net `{sum(aligned_values, Decimal('0'))}` USDT.",
        "- This is the only actual PF that is cohort-comparable to the 30-position P2-A decomposition.",
        "- Funding window matching has since been attempted; the account-level income ledger is not uniquely position-attributable, so no funding-only experiment is authorized.",
        "",
        "## Decision",
        "",
        "The 1.11 vs 0.3966 gap cannot be attributed to funding alone. First compare the 30-position aligned actual PF against replay using the same realized exchange fills and explicit commission/slippage assumptions; then add point-in-time funding per position. The 32-episode audit PF remains a separate account-episode metric until episode splitting is repaired or a position-keyed mapping is used.",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(OUT_JSON)
    print(OUT_MD)
    print(json.dumps(payload["pf"], ensure_ascii=False))


if __name__ == "__main__":
    build()
