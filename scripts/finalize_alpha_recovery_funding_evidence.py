"""Close the frozen Alpha Recovery funding-evidence ledger without touching trading.

This is a read-only research utility.  It obtains actual ``FUNDING_FEE`` records
from the configured Binance Testnet adapter, joins them only to already-filled
BTC/ETH ``testnet_sampling_v2`` episodes, and writes a normalized evidence
artifact.  It never estimates funding from a rate series and never runs, tunes,
or promotes a strategy.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from services.automated_trading.domain.enums import V2ExecutionMode
from services.automated_trading.infrastructure.binance_adapter import BinanceTestnetAdapter

DB_PATH = Path(".local_paper_console.db")
OUT_PATH = Path("artifacts/alpha_edge_recovery_final_campaign/FINAL_ECONOMIC_EVIDENCE.json")
MD_PATH = Path("artifacts/alpha_edge_recovery_final_campaign/FINAL_ECONOMIC_EVIDENCE.md")
SYMBOLS = {"BTC/USDT", "ETH/USDT"}
FUNDING_START = datetime(2026, 8, 1, tzinfo=UTC)
FUNDING_END = datetime(2026, 8, 30, tzinfo=UTC)


def _decimal(value: Any) -> Decimal:
    return Decimal(str(value))


def _as_utc(value: str) -> datetime:
    """Database timestamps are UTC-naive; normalize them explicitly."""
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _funding_boundaries(opened_at: datetime, closed_at: datetime) -> list[datetime]:
    boundary = opened_at.replace(hour=0, minute=0, second=0, microsecond=0)
    while boundary <= opened_at:
        boundary += timedelta(hours=8)
    result: list[datetime] = []
    while boundary <= closed_at:
        result.append(boundary)
        boundary += timedelta(hours=8)
    return result


def load_closed_episodes(db_path: Path) -> list[dict[str, Any]]:
    """Load the 73 frozen Testnet episodes plus exchange-fill cost evidence."""
    connection = sqlite3.connect(f"file:{db_path.resolve().as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            select p.position_id, p.intent_id, p.symbol, p.direction, p.quantity,
                   p.entry_price, p.projected_at, p.closed_at, p.realized_pnl,
                   coalesce(sum(case when f.reduce_only = 0 then f.commission else 0 end), 0) entry_commission,
                   coalesce(sum(case when f.reduce_only = 1 then f.commission else 0 end), 0) exit_commission,
                   count(case when f.reduce_only = 0 then 1 end) entry_fill_count,
                   count(case when f.reduce_only = 1 then 1 end) exit_fill_count
            from v2_managed_positions p
            join v2_execution_intents i on i.intent_id = p.intent_id
            left join v2_exchange_fills f on f.intent_id = p.intent_id
            where p.state = 'CLOSED'
              and p.execution_mode = 'BINANCE_TESTNET'
              and p.symbol in ('BTC/USDT', 'ETH/USDT')
              and i.candidate_key = 'testnet_sampling_v2'
            group by p.position_id
            order by p.projected_at, p.position_id
            """
        ).fetchall()
    finally:
        connection.close()

    episodes: list[dict[str, Any]] = []
    for row in rows:
        # Exit fills use separate ``exit:<position_id>:...`` intents, so query
        # their exchange receipts by candidate key rather than treating an empty
        # join above as an absent fill.
        connection = sqlite3.connect(f"file:{db_path.resolve().as_posix()}?mode=ro", uri=True)
        try:
            exit_cost = connection.execute(
                """
                select coalesce(sum(f.commission), 0), count(*)
                from v2_exchange_fills f join v2_execution_intents i on i.intent_id = f.intent_id
                where f.reduce_only = 1 and i.candidate_key like ?
                """,
                (f"exit:{row['position_id']}:%",),
            ).fetchone()
        finally:
            connection.close()
        commission = _decimal(row["entry_commission"]) + _decimal(exit_cost[0])
        episodes.append(
            {
                "position_id": row["position_id"],
                "symbol": row["symbol"],
                "direction": row["direction"],
                "quantity": str(_decimal(row["quantity"])),
                "open_time": _as_utc(row["projected_at"]),
                "close_time": _as_utc(row["closed_at"]),
                "trading_realized_pnl_usdt": _decimal(row["realized_pnl"]),
                "commission_usdt": commission,
                "entry_fill_count": int(row["entry_fill_count"]),
                "exit_fill_count": int(exit_cost[1]),
            }
        )
    if len(episodes) != 73:
        raise RuntimeError(f"frozen scope drift: expected 73 BTC/ETH episodes, found {len(episodes)}")
    return episodes


def fetch_actual_funding() -> tuple[list[dict[str, Any]], str]:
    """Fetch actual Testnet income events and retain only non-sensitive fields."""
    client = BinanceTestnetAdapter(V2ExecutionMode.BINANCE_TESTNET)._ensure_gateway()
    method = getattr(client, "fapiPrivateGetIncome", None)
    if not callable(method):
        raise RuntimeError("BINANCE_TESTNET_FUNDING_ENDPOINT_UNAVAILABLE")
    raw = method(
        {
            "incomeType": "FUNDING_FEE",
            "startTime": int(FUNDING_START.timestamp() * 1000),
            "endTime": int(FUNDING_END.timestamp() * 1000),
            "limit": 1000,
        }
    )
    events = [
        {
            "time": datetime.fromtimestamp(int(item["time"]) / 1000, tz=UTC),
            "symbol": str(item["symbol"]).replace("USDT", "/USDT"),
            "income_usdt": _decimal(item["income"]),
        }
        for item in raw
        if str(item.get("symbol", "")).replace("USDT", "/USDT") in SYMBOLS
    ]
    normalized = [
        (cast(datetime, item["time"]).isoformat(), item["symbol"], str(item["income_usdt"])) for item in events
    ]
    return events, hashlib.sha256(json.dumps(normalized, separators=(",", ":")).encode()).hexdigest()


def attribute_funding(episodes: list[dict[str, Any]], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attribute only an unambiguous event inside one real filled episode."""
    ledger: list[dict[str, Any]] = []
    for episode in episodes:
        matches = [
            event
            for event in events
            if event["symbol"] == episode["symbol"] and episode["open_time"] < event["time"] <= episode["close_time"]
        ]
        ambiguous = False
        funding = Decimal("0")
        for event in matches:
            active = [
                candidate
                for candidate in episodes
                if candidate["symbol"] == event["symbol"]
                and candidate["open_time"] < event["time"] <= candidate["close_time"]
            ]
            if len(active) != 1:
                ambiguous = True
            else:
                funding += event["income_usdt"]
        boundaries = _funding_boundaries(episode["open_time"], episode["close_time"])
        if ambiguous:
            status = "AMBIGUOUS_FUNDING_ATTRIBUTION"
            funding_value: Decimal | None = None
        elif matches:
            status = "FUNDING_EXACT"
            funding_value = funding
        elif not boundaries:
            # A position held wholly between two fixed 8-hour funding timestamps
            # has a mathematically exact zero, independent of API omission rules.
            status = "FUNDING_ZERO_BY_NO_EVENT"
            funding_value = Decimal("0")
        else:
            status = "FUNDING_MISSING"
            funding_value = None

        # ``realized_pnl`` is already net of entry+exit commission in the V2
        # persistence path.  Never deduct commission again from this value.
        economic = episode["trading_realized_pnl_usdt"] + funding_value if funding_value is not None else None
        ledger.append(
            {
                "position_id": episode["position_id"],
                "symbol": episode["symbol"],
                "direction": episode["direction"],
                "quantity": episode["quantity"],
                "open_time": episode["open_time"].isoformat(),
                "close_time": episode["close_time"].isoformat(),
                "entry_fill_count": episode["entry_fill_count"],
                "exit_fill_count": episode["exit_fill_count"],
                "trading_gross_pnl_before_commission_usdt": str(
                    episode["trading_realized_pnl_usdt"] + episode["commission_usdt"]
                ),
                "commission_usdt": str(episode["commission_usdt"]),
                "trading_realized_pnl_after_commission_usdt": str(episode["trading_realized_pnl_usdt"]),
                "funding_status": status,
                "funding_usdt": str(funding_value) if funding_value is not None else None,
                "slippage_status": "HISTORICAL_EXACT_SLIPPAGE_UNAVAILABLE",
                "slippage_usdt": None,
                "economic_net_pnl_usdt": str(economic) if economic is not None else None,
            }
        )
    return ledger


def build_evidence() -> dict[str, Any]:
    episodes = load_closed_episodes(DB_PATH)
    events, event_hash = fetch_actual_funding()
    ledger = attribute_funding(episodes, events)
    statuses = Counter(row["funding_status"] for row in ledger)
    exact = [row for row in ledger if row["funding_status"] == "FUNDING_EXACT"]
    known = [row for row in ledger if row["funding_usdt"] is not None]
    commission = sum((_decimal(row["commission_usdt"]) for row in ledger), Decimal("0"))
    return {
        "report_type": "ALPHA_EDGE_RECOVERY_FUNDING_ECONOMIC_LEDGER",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "source": {
            "kind": "BINANCE_TESTNET_INCOME_HISTORY",
            "income_type": "FUNDING_FEE",
            "coverage_utc": [FUNDING_START.isoformat(), FUNDING_END.isoformat()],
            "raw_event_count_btc_eth": len(events),
            "normalized_event_sha256": event_hash,
            "raw_income_not_persisted": True,
        },
        "accounting_semantics": {
            "trading_realized_pnl_after_commission": "v2_managed_positions.realized_pnl is assigned gross_pnl - entry_fee - exit_fee in fact_persistence.py; commission is diagnostic/reconciled, not deducted again.",
            "economic_net_formula": "trading_realized_pnl_after_commission + funding - exact_slippage_if_separately_observable",
            "commission_reconciliation_usdt": str(commission),
            "historical_exact_slippage": "UNAVAILABLE: persisted fill/order timestamps do not contain contemporaneous reference bid/ask/mid for every episode; no price series was invented.",
        },
        "scope": {"episodes": len(ledger), "symbols": sorted(SYMBOLS), "candidate_key": "testnet_sampling_v2"},
        "funding_attribution": {
            "funding_exact": statuses["FUNDING_EXACT"],
            "funding_zero_by_no_event": statuses["FUNDING_ZERO_BY_NO_EVENT"],
            "funding_ambiguous": statuses["AMBIGUOUS_FUNDING_ATTRIBUTION"],
            "funding_missing": statuses["FUNDING_MISSING"],
            "exact_event_count": sum(
                1
                for event in events
                if any(
                    row["funding_status"] == "FUNDING_EXACT"
                    and row["symbol"] == event["symbol"]
                    and row["open_time"] < event["time"].isoformat() <= row["close_time"]
                    for row in ledger
                )
            ),
            "known_funding_net_usdt": str(sum((_decimal(row["funding_usdt"]) for row in known), Decimal("0"))),
            "exact_only_funding_net_usdt": str(sum((_decimal(row["funding_usdt"]) for row in exact), Decimal("0"))),
        },
        "ledger": ledger,
        "frozen_oos_adjudication": {
            "hypotheses": [
                {
                    "id": "H1_ENTRY_CONFIRMATION_1BAR",
                    "frozen_oos_expectancy_r": "-0.2331134151742947672212032901",
                    "frozen_oos_profit_factor": "0.6848650952178725254285530433",
                },
                {
                    "id": "H2_SHORT_ONLY",
                    "frozen_oos_expectancy_r": "-0.2503088673770840811137971192",
                    "frozen_oos_profit_factor": "0.6657651350194321873486034779",
                },
            ],
            "historical_replay_period": "2023-01-01 through 2026-07-29; chronological 70/30",
            "historical_funding_evidence": "UNAVAILABLE: the actual Testnet income source begins 2026-08-01 and cannot be assigned to counterfactual 2023-2026 OOS trades. Market funding-rate rows are not substituted for actual income evidence.",
            "replay_performed": False,
            "replay_reason": "A funding-complete replay cannot be honestly executed from this source; rerunning the old no-funding screen would add no permitted evidence.",
            "required_funding_credit_to_break_even_r": {
                "H1_ENTRY_CONFIRMATION_1BAR": "0.2331134151742947672212032901 per OOS trade",
                "H2_SHORT_ONLY": "0.2503088673770840811137971192 per OOS trade",
            },
            "candidate_status": "EVIDENCE_BLOCKED",
        },
        "campaign_final_status": "BLOCKED_BY_FUNDING_EVIDENCE",
        "promotion": {"validated": 0, "shadow_authorized": False, "testnet_canary_authorized": False},
    }


def markdown(evidence: dict[str, Any]) -> str:
    attribution = evidence["funding_attribution"]
    frozen = evidence["frozen_oos_adjudication"]
    return "\n".join(
        [
            "# Alpha Recovery — Final Funding Economic Evidence",
            "",
            "终态：`BLOCKED_BY_FUNDING_EVIDENCE`。没有新增策略、假设、参数或运行时改动。",
            "",
            "## 73 个真实 BTC/ETH Testnet Episode",
            f"- Funding exact / zero / ambiguous / missing：`{attribution['funding_exact']}` / `{attribution['funding_zero_by_no_event']}` / `{attribution['funding_ambiguous']}` / `{attribution['funding_missing']}`。",
            f"- 可确认 Funding 净额：`{attribution['known_funding_net_usdt']} USDT`；其中逐笔 event 归属净额：`{attribution['exact_only_funding_net_usdt']} USDT`。",
            f"- Commission reconciliation：`{evidence['accounting_semantics']['commission_reconciliation_usdt']} USDT`。`realized_pnl` 已扣 entry/exit commission，Economic Net 不重复扣费。",
            "- Historical exact slippage：`UNAVAILABLE`；未编造历史 bid/ask/mid。",
            "",
            "## Frozen H1/H2 Final Adjudication",
            "- H1：冻结 OOS `-0.2331134151742947672212032901R` / PF `0.6848650952178725254285530433`；H2：`-0.2503088673770840811137971192R` / PF `0.6657651350194321873486034779`。",
            "- 两项均需要至少对应的每笔 Funding credit 才能到 break-even；实际 Testnet Funding 只覆盖 2026-08 当前账户，不能移植到 2023-2026-07 的反事实 OOS。",
            "- 因此不重跑旧的无 Funding 屏幕：那不会把 Funding evidence 从 INCOMPLETE 变成 COMPLETE。两项 Candidate 均为 `EVIDENCE_BLOCKED`，没有 Promotion。",
            "",
        ]
    )


def main() -> int:
    evidence = build_evidence()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    MD_PATH.write_text(markdown(evidence), encoding="utf-8")
    print(json.dumps({"status": evidence["campaign_final_status"], "episodes": 73}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
