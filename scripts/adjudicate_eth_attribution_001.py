"""Recover the two quarantined ETH lifecycles from immutable Binance evidence.

The script does not construct, submit, cancel, or amend exchange orders.  It
only reads the frozen Binance JSONL audit records and invokes the existing
two-phase local adjudication service.  Run without ``--apply`` for a complete
rollback-only preflight.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from services.automated_trading.application.adjudication import (
    AdjudicationManifest,
    ExchangeAggregateExitEvidence,
    finalize_adjudication,
    prepare_adjudication,
)
from services.automated_trading.infrastructure.account_writer import database_identity
from services.automated_trading.infrastructure.models import (
    V2AdjudicationFinalization,
    V2ManagedPosition,
)

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / ".local" / "eth-attribution-20260903" / "raw"
ORDER_AUDIT = RAW / "binance_orders.jsonl"
TRADE_AUDIT = RAW / "binance_user_trades.jsonl"
EXPECTED_AUDIT_HASHES = {
    ORDER_AUDIT: "19855EA8C43E4A7F828F4851171A57DC77264B1BE0D7631C135500CBA1BF8605",
    TRADE_AUDIT: "19D9FEDFBFC45C026AB77607E50E5CC7D2F45AE59B0846F38122479A9A83B5A8",
}
ACCOUNT_SCOPE_KEY = "BINANCE:TESTNET:primary_testnet"
ORDER_ID = "16782498190"
TRADE_ID = "322607950"
SYMBOL = "ETH/USDT"
ALLOCATION = Decimal("0.02")
AGGREGATE_QUANTITY = Decimal("0.04")
QUANTITY_PRECISION = Decimal("0.00000001")
DATABASES = (
    (ROOT / ".local" / "acceptance-fresh.db", "26cfbc6e-6cbc-4aa4-8584-bc631abb0808"),
    (ROOT / ".local" / "acceptance-fresh-2.db", "d06084e4-947f-47d8-9153-0b4fe474875b"),
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _normalized_quantity(value: Decimal) -> Decimal:
    return Decimal(str(value)).quantize(QUANTITY_PRECISION)


def _matching_record(path: Path, field: str, expected: str) -> dict[str, Any]:
    matches = [record for record in _records(path) if str(record.get(field)) == expected]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one {field}={expected} in {path.name}")
    return matches[0]


def _records(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def immutable_evidence() -> ExchangeAggregateExitEvidence:
    for path, expected_hash in EXPECTED_AUDIT_HASHES.items():
        if _sha256(path) != expected_hash:
            raise ValueError(f"immutable audit hash changed: {path.name}")
    order = _matching_record(ORDER_AUDIT, "orderId", ORDER_ID)
    trade = _matching_record(TRADE_AUDIT, "id", TRADE_ID)
    expected_order = {
        "symbol": "ETHUSDT",
        "side": "BUY",
        "reduceOnly": True,
        "status": "FILLED",
        "executedQty": "0.040",
        "avgPrice": "2398.05000",
    }
    if any(order.get(field) != value for field, value in expected_order.items()):
        raise ValueError("immutable order evidence does not match ETH_ATTRIBUTION_001")
    expected_trade = {
        "orderId": int(ORDER_ID),
        "symbol": "ETHUSDT",
        "side": "BUY",
        "qty": "0.040",
        "price": "2398.05",
    }
    if any(trade.get(field) != value for field, value in expected_trade.items()):
        raise ValueError("immutable trade evidence does not match ETH_ATTRIBUTION_001")
    if order["time"] != trade["time"]:
        raise ValueError("immutable order/trade timestamps disagree")
    occurred_at = datetime.fromtimestamp(int(trade["time"]) / 1000, tz=UTC)
    return ExchangeAggregateExitEvidence(
        exchange_order_id=ORDER_ID,
        exchange_trade_id=TRADE_ID,
        symbol=SYMBOL,
        side="BUY",
        reduce_only=True,
        executed_quantity=AGGREGATE_QUANTITY,
        trade_quantity=AGGREGATE_QUANTITY,
        price=Decimal("2398.05"),
        exchange_event_time=occurred_at,
        verification_reference=(
            "immutable-binance-audit:"
            f"orders={EXPECTED_AUDIT_HASHES[ORDER_AUDIT].lower()};"
            f"trades={EXPECTED_AUDIT_HASHES[TRADE_AUDIT].lower()}"
        ),
    )


def _database_url(path: Path) -> str:
    return f"sqlite:///{path.resolve().as_posix()}"


def _manifest() -> AdjudicationManifest:
    allocations = tuple(
        (database_identity(_database_url(path)), position_id, ALLOCATION) for path, position_id in DATABASES
    )
    evidence = immutable_evidence()
    return AdjudicationManifest(
        adjudication_id="eth-attribution-001",
        symbol=SYMBOL,
        exchange_order_id=ORDER_ID,
        exchange_trade_id=TRADE_ID,
        exchange_fill_quantity=AGGREGATE_QUANTITY,
        exchange_fill_side=evidence.side,
        exchange_fill_price=evidence.price,
        exchange_fill_timestamp=evidence.exchange_event_time,
        operator_identity="local_operator",
        operator_reason="ETH_ATTRIBUTION_001 immutable aggregate exit recovery",
        allocations=allocations,
        account_scope_key=ACCOUNT_SCOPE_KEY,
    )


def _provider(*_args: str) -> ExchangeAggregateExitEvidence:
    return immutable_evidence()


def _verify_finalized(sessions: dict[str, Session], manifest: AdjudicationManifest) -> dict[str, str]:
    results: dict[str, str] = {}
    for item in manifest.normalized_allocations():
        session = sessions[item.database_identity]
        session.expire_all()
        position = session.get(V2ManagedPosition, item.position_id)
        finalization = session.scalar(
            select(V2AdjudicationFinalization).where(V2AdjudicationFinalization.position_id == item.position_id)
        )
        if (
            position is None
            or position.state != "CLOSED"
            or finalization is None
            or finalization.exchange_order_id != ORDER_ID
            or finalization.exchange_trade_id != TRADE_ID
            or _normalized_quantity(finalization.aggregate_quantity) != AGGREGATE_QUANTITY
            or _normalized_quantity(finalization.allocated_quantity) != ALLOCATION
        ):
            raise ValueError("ETH_ATTRIBUTION_001 finalization verification failed")
        results[item.position_id] = position.state
    return results


def run(*, apply: bool) -> dict[str, Any]:
    manifest = _manifest()
    engines = []
    sessions: dict[str, Session] = {}
    try:
        for path, _position_id in DATABASES:
            engine = create_engine(_database_url(path))
            engines.append(engine)
            identity = database_identity(_database_url(path))
            sessions[identity] = sessionmaker(bind=engine, expire_on_commit=False)()
        for identity, session in sessions.items():
            prepare_adjudication(session, manifest=manifest, database_identity=identity, evidence_provider=_provider)
            if apply:
                session.commit()
        finalize_adjudication(sessions, manifest=manifest, evidence_provider=_provider)
        if apply:
            for session in sessions.values():
                session.commit()
            state = _verify_finalized(sessions, manifest)
        else:
            state = {item.position_id: "PREFLIGHT_ROLLBACK" for item in manifest.normalized_allocations()}
        return {
            "mode": "APPLIED" if apply else "DRY_RUN",
            "exchange_writes": 0,
            "manifest_hash": manifest.manifest_hash,
            "evidence_hash": immutable_evidence().evidence_hash,
            "positions": state,
        }
    finally:
        for session in sessions.values():
            session.rollback()
            session.close()
        for engine in engines:
            engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="commit the already-validated local adjudication")
    args = parser.parse_args()
    print(json.dumps(run(apply=args.apply), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
