"""Attribute Binance funding income to the 30-position P2-A cohort.

This is a read-only, position-keyed calculation.  It intentionally uses the
exchange income ledger and the actual entry/close timestamps from the existing
P2-A decomposition; it does not change cost-gate inputs or production code.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DECOMP = ROOT / "docs/audits/2026-08-16-p2a-actual-decomposition.json"
ALIGNMENT = ROOT / "docs/audits/2026-08-16-pf-cohort-alignment.json"
INCOME = ROOT / "docs/audits/2026-08-16-testnet-history/raw/binance_income.jsonl"
DB = ROOT / ".local_paper_console.db"
OUT_JSON = ROOT / "docs/audits/2026-08-16-p2a-funding-attribution.json"
OUT_MD = ROOT / "docs/audits/2026-08-16-p2a-funding-attribution.md"


def dec(value: Any) -> Decimal:
    return Decimal(str(value or "0"))


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def load_income() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with INCOME.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("incomeType") != "FUNDING_FEE":
                continue
            row["event_time"] = datetime.fromtimestamp(int(row["time"]) / 1000, tz=UTC)
            row["income_decimal"] = dec(row.get("income"))
            rows.append(row)
    return rows


def load_position_snapshots() -> list[dict[str, Any]]:
    """Load read-only exchange position snapshots for ambiguity checks."""
    conn = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = [
        dict(row)
        for row in conn.execute(
            """
        SELECT symbol, side, quantity, snapshot_time, run_type, position_record_id
        FROM position_snapshots
        WHERE quantity != 0
        ORDER BY snapshot_time
        """
        )
    ]
    conn.close()
    for row in rows:
        row["snapshot_time"] = parse_iso(str(row["snapshot_time"]).replace(" ", "T"))
    return rows


def pf(values: list[Decimal]) -> Decimal | None:
    wins = sum((value for value in values if value > 0), Decimal("0"))
    losses = abs(sum((value for value in values if value < 0), Decimal("0")))
    return wins / losses if losses else None


def build() -> dict[str, Any]:
    decomposition = json.loads(DECOMP.read_text(encoding="utf-8"))
    alignment = json.loads(ALIGNMENT.read_text(encoding="utf-8"))
    income = load_income()
    snapshots = load_position_snapshots()
    aligned_by_position = {row["position_id"]: row for row in alignment["aligned_rows"]}

    rows: list[dict[str, Any]] = []
    for position in decomposition:
        start = parse_iso(position["entry_fill_timestamp"])
        end = parse_iso(position["closed_at"])
        symbol = position["symbol"].replace("/", "")
        events = [event for event in income if event["symbol"] == symbol and start < event["event_time"] <= end]
        event_context: list[dict[str, Any]] = []
        for event in events:
            prior = [
                snapshot
                for snapshot in snapshots
                if snapshot["symbol"].replace("/", "") == symbol and snapshot["snapshot_time"] <= event["event_time"]
            ]
            latest_time = max((item["snapshot_time"] for item in prior), default=None)
            latest = [item for item in prior if item["snapshot_time"] == latest_time]
            event_context.append(
                {
                    "funding_time": event["event_time"].isoformat(),
                    "latest_snapshot_time": latest_time.isoformat() if latest_time else None,
                    "positions": [
                        {
                            "side": item["side"],
                            "quantity": str(item["quantity"]),
                            "run_type": item["run_type"],
                            "position_record_id": item["position_record_id"],
                        }
                        for item in latest
                    ],
                }
            )
        funding_usdt = sum((event["income_decimal"] for event in events), Decimal("0"))
        risk_usdt = dec(position["risk_usdt"])
        funding_r = funding_usdt / risk_usdt if risk_usdt else Decimal("0")
        aligned = aligned_by_position.get(position["position_id"], {})
        actual_pre_funding_usdt = dec(aligned.get("net_pnl_excluding_funding"))
        actual_post_funding_usdt = actual_pre_funding_usdt + funding_usdt
        actual_risk_usdt = risk_usdt
        rows.append(
            {
                "position_id": position["position_id"],
                "symbol": position["symbol"],
                "direction": position["direction"],
                "entry_time": position["entry_fill_timestamp"],
                "exit_time": position["closed_at"],
                "holding_minutes": position["holding_minutes"],
                "reported_funding_boundaries_crossed": position["funding_boundaries_crossed"],
                "matched_funding_event_count": len(events),
                "funding_events": [
                    {
                        "time": event["event_time"].isoformat(),
                        "income_usdt": str(event["income_decimal"]),
                        "tran_id": event.get("tranId"),
                    }
                    for event in events
                ],
                "funding_event_position_context": event_context,
                "funding_usdt": str(funding_usdt),
                "funding_r": str(funding_r),
                "net_r_pre_funding": str(dec(position["net_r"])),
                "net_r_post_funding": str(dec(position["net_r"]) + funding_r),
                "actual_net_pnl_pre_funding_usdt": str(actual_pre_funding_usdt),
                "actual_net_pnl_post_funding_usdt": str(actual_post_funding_usdt),
                "actual_net_r_post_funding": str(
                    actual_post_funding_usdt / actual_risk_usdt if actual_risk_usdt else Decimal("0")
                ),
            }
        )

    funding_values = [dec(row["funding_usdt"]) for row in rows]
    net_r_pre = [dec(row["net_r_pre_funding"]) for row in rows]
    net_r_post = [dec(row["net_r_post_funding"]) for row in rows]
    actual_pre = [dec(row["actual_net_pnl_pre_funding_usdt"]) for row in rows]
    actual_post = [dec(row["actual_net_pnl_post_funding_usdt"]) for row in rows]
    explicit_commission = sum(
        (dec(item["entry_fee_usdt"]) + dec(item["exit_fee_usdt"]) for item in decomposition),
    )
    funding_total = sum(funding_values, Decimal("0"))
    payload = {
        "status": "FUNDING_WINDOW_MATCHED_ACCOUNT_LEVEL_AMBIGUOUS",
        "method": {
            "cohort": "The same 30 P2-A local CLOSED testnet_sampling_v2 positions.",
            "funding_source": str(INCOME.relative_to(ROOT)),
            "interval": "entry_fill_timestamp < funding_event_time <= closed_at",
            "unit": "Binance account income in USDT; negative means funding paid.",
            "external_overlap_caveat": "This assigns account-level funding events by symbol and time. Position snapshots are included to flag simultaneous external exposure; an account-level funding event is not uniquely attributable when more than one position is present.",
        },
        "counts": {
            "positions": len(rows),
            "positions_with_funding_event": sum(1 for row in rows if row["matched_funding_event_count"]),
            "matched_funding_events": sum(row["matched_funding_event_count"] for row in rows),
            "reported_boundary_total": sum(row["reported_funding_boundaries_crossed"] for row in rows),
            "actual_event_count_by_position": dict(Counter(row["matched_funding_event_count"] for row in rows)),
            "events_with_snapshot_context": sum(
                1
                for row in rows
                for context in row["funding_event_position_context"]
                if context["latest_snapshot_time"]
            ),
            "events_with_multiple_snapshot_positions": sum(
                1 for row in rows for context in row["funding_event_position_context"] if len(context["positions"]) > 1
            ),
            "events_with_stale_snapshot_context": sum(
                1
                for row in rows
                for context in row["funding_event_position_context"]
                if context["latest_snapshot_time"]
                and (parse_iso(context["funding_time"]) - parse_iso(context["latest_snapshot_time"])).total_seconds()
                > 30 * 60
            ),
        },
        "aggregate": {
            "funding_usdt": str(funding_total),
            "funding_abs_usdt": str(abs(funding_total)),
            "explicit_commission_usdt": str(explicit_commission),
            "funding_share_of_commission_plus_funding": str(
                abs(funding_total) / (explicit_commission + abs(funding_total))
                if explicit_commission + abs(funding_total)
                else Decimal("0")
            ),
            "p2a_normalized_r_pf_pre_funding": str(pf(net_r_pre)),
            "p2a_normalized_r_pf_post_funding": str(pf(net_r_post)),
            "aligned_actual_usdt_pf_pre_funding": str(pf(actual_pre)),
            "aligned_actual_usdt_pf_post_funding": str(pf(actual_post)),
            "aligned_actual_net_pnl_pre_funding_usdt": str(sum(actual_pre, Decimal("0"))),
            "aligned_actual_net_pnl_post_funding_usdt": str(sum(actual_post, Decimal("0"))),
            "mean_funding_r": str(sum((dec(row["funding_r"]) for row in rows), Decimal("0")) / len(rows)),
        },
        "rows": rows,
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    aggregate = payload["aggregate"]
    lines = [
        "# P2-A Point-in-Time Funding Attribution",
        "",
        "## Verdict",
        "",
        "`FUNDING_WINDOW_MATCHED_ACCOUNT_LEVEL_AMBIGUOUS` — exchange funding events overlap the same 30 position windows, but the income ledger is account-level and cannot be uniquely assigned to a strategy position from this artifact alone.",
        "",
        "## Method",
        "",
        "- Cohort: the same 30 P2-A local `CLOSED` `testnet_sampling_v2` positions.",
        "- Funding source: `binance_income.jsonl`, `incomeType=FUNDING_FEE`.",
        "- Assignment window: `entry_fill_timestamp < event_time <= closed_at`.",
        "- Negative funding is paid funding. Events are only window-matched by symbol/time; account-level income is not uniquely strategy-position attributable when external exposure or stale snapshots are present.",
        "",
        "## Results",
        "",
        f"- Matched funding events: `{payload['counts']['matched_funding_events']}` across `{payload['counts']['positions_with_funding_event']}` of `{payload['counts']['positions']}` positions; `{payload['counts']['events_with_stale_snapshot_context']}` have stale (>30m) snapshot context.",
        f"- Funding: `{aggregate['funding_usdt']}` USDT; explicit commission: `{aggregate['explicit_commission_usdt']}` USDT; funding share: `{aggregate['funding_share_of_commission_plus_funding']}`.",
        f"- Normalized-R PF: `{aggregate['p2a_normalized_r_pf_pre_funding']}` before funding -> `{aggregate['p2a_normalized_r_pf_post_funding']}` after funding.",
        f"- Aligned actual USDT PF: `{aggregate['aligned_actual_usdt_pf_pre_funding']}` before funding -> `{aggregate['aligned_actual_usdt_pf_post_funding']}` after funding.",
        f"- Aligned actual net PnL: `{aggregate['aligned_actual_net_pnl_pre_funding_usdt']}` USDT before funding -> `{aggregate['aligned_actual_net_pnl_post_funding_usdt']}` USDT after funding.",
        "",
        "## Interpretation",
        "",
        "The naive window sum is not a valid strategy-only funding attribution: the income ledger is account-level, and contemporaneous position snapshots are stale or show external/contradictory exposure. Therefore the post-funding PF is illustrative only and must not drive a funding-only experiment. The 32-episode `0.3966` remains a separate account-episode metric until position-keyed exchange income or a trustworthy exposure ledger is available.",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(OUT_JSON)
    print(OUT_MD)
    print(json.dumps(payload["aggregate"], ensure_ascii=False))


if __name__ == "__main__":
    build()
