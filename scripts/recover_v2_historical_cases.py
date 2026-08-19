#!/usr/bin/env python3
"""Recover the six fixed Gate 0 legacy V2 cases from Binance Testnet facts.

The command never submits, cancels, or alters an exchange order.  Without
``--apply`` it only inventories database identities and re-reads Binance
Testnet.  With ``--apply`` it can do exactly two safe things:

* project a QUARANTINED V2 position to CLOSED from one exact, confirmed,
  reduce-only Binance exit order and its real trade receipt(s); or
* record a Testnet-only ``HISTORICAL_LEDGER_GAP`` for a flat legacy entry whose
  later aggregate exit cannot be assigned to that individual entry.

The latter is intentionally not CLOSED, creates no exit fill, and has UNKNOWN
PnL.  It separates current exposure safety from historical ledger completeness.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select

from services.automated_trading.application.exit_service import (
    ExitExecutionResult,
    ExitExecutionStatus,
    ExitReason,
)
from services.automated_trading.application.fact_persistence import persist_exit_result
from services.automated_trading.application.historical_ledger import (
    HistoricalEvidenceSource,
    record_historical_ledger_gap,
)
from services.automated_trading.domain.enums import V2ExecutionMode, V2PositionState
from services.automated_trading.infrastructure.binance_adapter import BinanceTestnetAdapter
from services.automated_trading.infrastructure.models import (
    V2ExchangeFill,
    V2ExchangeOrder,
    V2ExecutionIntent,
    V2ManagedPosition,
    V2ProtectionRecord,
)
from services.database import get_session_factory


@dataclass(frozen=True)
class FixedCase:
    case_id: str
    intent_id: str
    position_id: str | None
    symbol: str
    direction: str
    exact_exit_order_id: str | None = None
    aggregate_exit_order_id: str | None = None
    aggregate_exit_trade_id: str | None = None


CASES = (
    FixedCase(
        "G0-UNPROJECTED-SOL-42A8445D",
        "42a8445d-c09e-42b9-97da-5e2dc05b60d4",
        None,
        "SOL/USDT",
        "long",
        aggregate_exit_order_id="4184219234",
        aggregate_exit_trade_id="86472616",
    ),
    FixedCase(
        "G0-UNPROJECTED-ETH-ABADAA18",
        "abadaa18-f186-43f3-8d1c-37e440cdbc17",
        None,
        "ETH/USDT",
        "long",
        aggregate_exit_order_id="16763680931",
        aggregate_exit_trade_id="314966739",
    ),
    FixedCase(
        "G0-UNPROJECTED-ETH-30C33C70",
        "30c33c70-54da-48d0-9423-74883545787f",
        None,
        "ETH/USDT",
        "long",
        aggregate_exit_order_id="16763680931",
        aggregate_exit_trade_id="314966739",
    ),
    FixedCase(
        "G0-QUARANTINED-ETH-D0645870",
        "b7adc491-2f2b-4492-8f10-bee46d7bb72c",
        "d0645870-ed02-40f2-9e34-a4069f0a92e1",
        "ETH/USDT",
        "short",
        exact_exit_order_id="16758097224",
    ),
    FixedCase(
        "G0-QUARANTINED-BTC-75718400",
        "379781fd-3dab-42cd-b3be-ed3836156bfe",
        "75718400-d51d-4167-9bce-b50f2a2dd7a4",
        "BTC/USDT",
        "long",
        exact_exit_order_id="28541209831",
    ),
    FixedCase(
        "G0-QUARANTINED-ETH-4062D69A",
        "0893b75b-befd-4c5c-8d86-9e4234ef74a9",
        "4062d69a-89c7-40fa-a569-f2f641a19663",
        "ETH/USDT",
        "long",
        exact_exit_order_id="16760968984",
    ),
)

_CUTOVER_EPOCH = "gate0-historical-ledger-cutover-2026-08-18"
_WRITER_STARTED_AT = datetime(2026, 8, 18, tzinfo=UTC)


def _json_default(value: Any) -> str:
    if isinstance(value, (datetime, Decimal)):
        return value.isoformat() if isinstance(value, datetime) else str(value)
    raise TypeError(type(value).__name__)


def _opposite_side(direction: str) -> str:
    if direction == "long":
        return "sell"
    if direction == "short":
        return "buy"
    raise ValueError(f"unsupported direction {direction!r}")


class CachedAuditAdapter:
    """Read-only adapter over a just-produced Binance audit artifact.

    This keeps recovery deterministic when direct history endpoints are slow or
    rate-limited.  ``--apply`` still requires a separately fresh, automatic
    current-flat confirmation; cached history is never used to infer exposure.
    """

    def __init__(self, evidence_dir: Path) -> None:
        self._orders = {
            str(row.get("orderId")): row for row in _read_jsonl(evidence_dir / "raw" / "binance_orders.jsonl")
        }
        self._trades = _read_jsonl(evidence_dir / "raw" / "binance_user_trades.jsonl")

    def query_filled_order_by_id(self, _symbol: str, exchange_order_id: str):
        row = self._orders.get(str(exchange_order_id))
        if row is None:
            return None
        return SimpleNamespace(
            exchange_order_id=str(row["orderId"]),
            client_order_id=str(row.get("clientOrderId") or ""),
            side=str(row.get("side") or "").lower(),
            status=str(row.get("status") or "").lower(),
            reduce_only=bool(row.get("reduceOnly")),
        )

    def fetch_fills(self, _symbol: str, exchange_order_id: str) -> tuple[Any, ...]:
        return tuple(
            SimpleNamespace(
                exchange_order_id=str(row["orderId"]),
                trade_id=str(row["id"]),
                filled_quantity=Decimal(str(row["qty"])),
                fill_price=Decimal(str(row["price"])),
                commission=Decimal(str(row.get("commission") or "0")),
                exchange_event_time=datetime.fromtimestamp(int(row["time"]) / 1000, tz=UTC),
            )
            for row in self._trades
            if str(row.get("orderId")) == str(exchange_order_id)
        )


def _read_jsonl(path: Path) -> tuple[dict[str, Any], ...]:
    if not path.is_file():
        raise RuntimeError(f"required Binance audit artifact is missing: {path}")
    with path.open(encoding="utf-8") as handle:
        return tuple(json.loads(line) for line in handle if line.strip())


def _local_search_counts(case: FixedCase) -> dict[str, int]:
    """Search local logs/project evidence and Git history without changing them."""
    tokens = [
        case.intent_id,
        case.position_id or "",
        case.exact_exit_order_id or "",
        case.aggregate_exit_order_id or "",
    ]
    roots = (Path("logs"), Path("docs/evidence"), Path("docs/audits"), Path("artifacts"), Path(".local"))
    files_examined = 0
    files_matched = 0
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.stat().st_size > 2_000_000:
                continue
            files_examined += 1
            try:
                content = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if any(token and token in content for token in tokens):
                files_matched += 1
    git_queries = 0
    for token in tokens:
        if not token:
            continue
        subprocess.run(
            ["git", "log", "--all", "--format=%H", f"-S{token}", "--"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            text=True,
        )
        git_queries += 1
    return {"local_files_examined": files_examined, "local_files_matched": files_matched, "git_queries": git_queries}


def _inventory_row(
    case: FixedCase, adapter: BinanceTestnetAdapter, snapshot
) -> tuple[dict[str, Any], Any, Any, tuple[Any, ...]]:
    with get_session_factory()() as session:
        intent = session.get(V2ExecutionIntent, case.intent_id)
        if intent is None:
            raise RuntimeError(f"fixed Gate 0 intent is missing: {case.intent_id}")
        entry_order = session.scalar(select(V2ExchangeOrder).where(V2ExchangeOrder.intent_id == case.intent_id))
        entry_fills = tuple(
            session.scalars(
                select(V2ExchangeFill).where(
                    V2ExchangeFill.intent_id == case.intent_id,
                    V2ExchangeFill.reduce_only.is_(False),
                )
            )
        )
        position = session.get(V2ManagedPosition, case.position_id) if case.position_id else None
        protections = tuple(
            session.scalars(select(V2ProtectionRecord).where(V2ProtectionRecord.position_id == case.position_id))
            if case.position_id
            else ()
        )
        exit_order = (
            adapter.query_filled_order_by_id(case.symbol, case.exact_exit_order_id)
            if case.exact_exit_order_id
            else None
        )
        exit_fills = adapter.fetch_fills(case.symbol, case.exact_exit_order_id) if case.exact_exit_order_id else ()
        current_positions = [
            {"direction": value.direction, "quantity": str(value.quantity)}
            for value in snapshot.positions
            if value.symbol == case.symbol
        ]
        current_orders = [
            {"exchange_order_id": value.exchange_order_id, "client_order_id": value.client_order_id, "side": value.side}
            for value in snapshot.pending_orders
            if value.symbol == case.symbol
        ]
        row = {
            "case_id": case.case_id,
            "symbol": case.symbol,
            "direction": intent.direction,
            "entry_intent_id": intent.intent_id,
            "entry_order_record_id": entry_order.order_record_id if entry_order else None,
            "exchange_entry_order_id": entry_order.exchange_order_id if entry_order else None,
            "entry_trade_id": [str(fill.trade_id) for fill in entry_fills if fill.trade_id],
            "entry_fill_ids": [fill.fill_id for fill in entry_fills],
            "entry_fill_accounts": [fill.account_id for fill in entry_fills],
            "entry_fill_time": [fill.exchange_event_time for fill in entry_fills],
            "entry_quantity": str(sum((fill.filled_quantity for fill in entry_fills), Decimal("0"))),
            "entry_price": str(
                sum((fill.filled_quantity * fill.fill_price for fill in entry_fills), Decimal("0"))
                / sum((fill.filled_quantity for fill in entry_fills), Decimal("0"))
            ),
            "local_position_id": position.position_id if position else None,
            "local_position_state": position.state if position else None,
            "protection_ids": [item.protection_id for item in protections],
            "known_exit_order_ids": [case.exact_exit_order_id]
            if case.exact_exit_order_id
            else [case.aggregate_exit_order_id],
            "known_reduce_only_fill_ids": [str(fill.trade_id) for fill in exit_fills if fill.trade_id],
            "quarantine_reason": "exact-terminal-evidence-pending" if position else "aggregate-exit-not-allocatable",
            "current_exchange_position": current_positions,
            "current_exchange_orders": current_orders,
        }
    return row, position, exit_order, tuple(exit_fills)


def _validate_exact(case: FixedCase, position, order, fills: tuple[Any, ...]) -> ExitExecutionResult:
    if position is None or position.state not in {
        V2PositionState.QUARANTINED.value,
        V2PositionState.CLOSED.value,
    }:
        raise RuntimeError(f"{case.case_id}: expected a QUARANTINED or already CLOSED managed position")
    if order is None or order.status not in {"closed", "filled"} or not order.reduce_only:
        raise RuntimeError(f"{case.case_id}: Binance exit order is not a terminal reduce-only receipt")
    if order.side != _opposite_side(position.direction):
        raise RuntimeError(f"{case.case_id}: Binance exit side does not reduce the entry direction")
    total_qty = sum((fill.filled_quantity for fill in fills), Decimal("0"))
    if not fills or total_qty != position.quantity:
        raise RuntimeError(f"{case.case_id}: exit fill quantity is not an exact position flatten")
    weighted_price = sum((fill.filled_quantity * fill.fill_price for fill in fills), Decimal("0")) / total_qty
    return ExitExecutionResult(
        status=ExitExecutionStatus.CLOSED,
        position_state=V2PositionState.CLOSED,
        client_order_id=order.client_order_id,
        exchange_order_id=order.exchange_order_id,
        trade_ids=tuple(str(fill.trade_id) for fill in fills if fill.trade_id),
        reduced_quantity=total_qty,
        average_fill_price=weighted_price,
        total_fee=sum((fill.commission for fill in fills), Decimal("0")),
        remaining_quantity=Decimal("0"),
        fill_timestamp=max(fill.exchange_event_time for fill in fills),
        detail="Gate 0 exact historical Binance Testnet exit recovery",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="persist only validated local recovery/cutover facts")
    parser.add_argument("--case-id", action="append", choices=[case.case_id for case in CASES])
    parser.add_argument("--evidence-dir", type=Path, default=Path(".local/gate0-history-20260819"))
    parser.add_argument(
        "--live", action="store_true", help="re-read order history and current account from Binance Testnet"
    )
    parser.add_argument(
        "--current-flat-confirmed-at",
        type=datetime.fromisoformat,
        help="timestamp from a fresh automatic read-only Testnet current-account probe; required with --apply unless --live",
    )
    args = parser.parse_args()
    selected = tuple(case for case in CASES if not args.case_id or case.case_id in args.case_id)
    if args.live:
        adapter: Any = BinanceTestnetAdapter(V2ExecutionMode.BINANCE_TESTNET)
        snapshot = adapter.fetch_authoritative_snapshot()
        snapshot_source = "live_binance_testnet"
    else:
        adapter = CachedAuditAdapter(args.evidence_dir)
        snapshot = SimpleNamespace(positions=(), pending_orders=(), snapshot_timestamp=args.current_flat_confirmed_at)
        snapshot_source = "cached_binance_audit_with_separate_current_probe"
    if args.apply and snapshot.snapshot_timestamp is None:
        raise SystemExit(
            "--apply requires --live or a fresh --current-flat-confirmed-at from an automatic Testnet probe"
        )
    report: list[dict[str, Any]] = []

    for case in selected:
        row, position, exit_order, exit_fills = _inventory_row(case, adapter, snapshot)
        row["source_search"] = _local_search_counts(case)
        if case.exact_exit_order_id:
            result = _validate_exact(case, position, exit_order, exit_fills)
            row["resolution"] = "EXACT_RECOVERED"
            row["authoritative_exit"] = {
                "exchange_order_id": result.exchange_order_id,
                "client_order_id": result.client_order_id,
                "trade_ids": list(result.trade_ids),
                "quantity": str(result.reduced_quantity),
                "price": str(result.average_fill_price),
                "fee": str(result.total_fee),
                "time": result.fill_timestamp,
            }
            if args.apply:
                persist_exit_result(
                    cycle_id=f"historical-recovery-{case.position_id}",
                    position_id=case.position_id or "",
                    execution_mode=V2ExecutionMode.BINANCE_TESTNET,
                    reason=ExitReason.MANUAL_REDUCE_ONLY,
                    result=result,
                    fencing_token=f"historical-recovery:{case.position_id}",
                )
        else:
            if row["current_exchange_position"]:
                raise RuntimeError(f"{case.case_id}: cannot cut over while current exchange exposure exists")
            if position is not None:
                raise RuntimeError(f"{case.case_id}: cutover applies only to an unprojected legacy entry")
            row["resolution"] = "HISTORICAL_LEDGER_GAP"
            row["authoritative_exit"] = {
                "aggregate_exit_order_id": case.aggregate_exit_order_id,
                "aggregate_exit_trade_id": case.aggregate_exit_trade_id,
                "allocation": "not attributable to an individual entry; no local exit fact is created",
            }
            if args.apply:
                with get_session_factory()() as session:
                    resolution = record_historical_ledger_gap(
                        session,
                        intent_id=case.intent_id,
                        current_flat_symbols=frozenset({case.symbol}),
                        current_open_order_ids=frozenset(),
                        active_protection_ids=frozenset(),
                        sources_checked=frozenset(HistoricalEvidenceSource),
                        cutover_epoch=_CUTOVER_EPOCH,
                        writer_started_at=_WRITER_STARTED_AT,
                        resolution_reason="confirmed aggregate reduce-only exit cannot be allocated to this individual legacy entry",
                        last_known_exchange_evidence={
                            "aggregate_exit_order_id": case.aggregate_exit_order_id or "",
                            "aggregate_exit_trade_id": case.aggregate_exit_trade_id or "",
                        },
                        observed_at=snapshot.snapshot_timestamp,
                    )
                    session.commit()
                row["historical_gap_incident_id"] = resolution.incident_id
                row["historical_gap_created"] = resolution.created
        report.append(row)

    print(
        json.dumps(
            {
                "applied": bool(args.apply),
                "current_snapshot_at": snapshot.snapshot_timestamp,
                "snapshot_source": snapshot_source,
                "cases": report,
            },
            ensure_ascii=False,
            indent=2,
            default=_json_default,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
