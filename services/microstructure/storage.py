from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from services.automated_trading.infrastructure.models import (
    MicrostructureCheckpoint,
    MicrostructureHealth,
    MicrostructureSnapshot,
)


def validate_book(snapshot: dict[str, Any]) -> tuple[bool, str | None]:
    bid = Decimal(str(snapshot["best_bid"]))
    ask = Decimal(str(snapshot["best_ask"]))
    if bid <= 0 or ask <= 0:
        return False, "non_positive_top_of_book"
    if bid >= ask:
        return False, "crossed_book"
    if Decimal(str(snapshot["last_price"])) <= 0:
        return False, "non_positive_last_price"
    if not snapshot.get("bids") or not snapshot.get("asks"):
        return False, "empty_depth"
    return True, None


def persist_snapshot(session: Session, payload: dict[str, Any]) -> bool:
    valid, reason = validate_book(payload)
    row = MicrostructureSnapshot(
        symbol=payload["symbol"],
        exchange_timestamp_ms=int(payload["exchange_timestamp_ms"]),
        received_at=payload.get("received_at") or datetime.now(UTC),
        last_price=Decimal(str(payload["last_price"])),
        mark_price=Decimal(str(payload["mark_price"])) if payload.get("mark_price") is not None else None,
        best_bid=Decimal(str(payload["best_bid"])),
        best_ask=Decimal(str(payload["best_ask"])),
        spread_bps=Decimal(str(payload["spread_bps"])),
        bids=payload.get("bids", []),
        asks=payload.get("asks", []),
        sequence=payload.get("sequence"),
        source=payload.get("source", "binance_testnet_public"),
        latency_ms=payload.get("latency_ms"),
        clock_skew_ms=payload.get("clock_skew_ms"),
        is_valid=valid,
        invalid_reason=reason,
    )
    try:
        session.add(row)
        session.commit()
        return True
    except IntegrityError:
        session.rollback()
        return False


def update_checkpoint(session: Session, collector_id: str, *, exchange_timestamp_ms: int, sequence: int | None) -> None:
    row = session.get(MicrostructureCheckpoint, collector_id)
    if row is None:
        row = MicrostructureCheckpoint(collector_id=collector_id)
        session.add(row)
    row.last_exchange_timestamp_ms = exchange_timestamp_ms
    row.last_sequence = sequence
    row.updated_at = datetime.now(UTC)
    session.commit()


def health_snapshot(session: Session, symbol: str) -> dict[str, Any]:
    row = session.get(MicrostructureHealth, symbol)
    if row is None:
        return {"symbol": symbol, "rows_count": 0, "coverage_ratio": 0.0, "overall_coverage_ratio": 0.0}
    return {
        "symbol": symbol,
        "rows_count": row.rows_count,
        "duplicate_count": row.duplicate_count,
        "gap_count": row.gap_count,
        "crossed_book_count": row.crossed_book_count,
        "invalid_count": row.invalid_count,
        "freshness_ms": row.freshness_ms,
        "coverage_ratio": float(row.coverage_ratio),
        "overall_coverage_ratio": float(row.overall_coverage_ratio),
        "last_error": row.last_error,
    }
