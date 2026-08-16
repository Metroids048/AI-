from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from services.automated_trading.infrastructure.models import MicrostructureHealth, MicrostructureSnapshot


def refresh_health(
    session: Session, symbol: str, *, expected_interval_ms: int = 1000
) -> dict[str, float | int | str | None]:
    rows = list(
        session.scalars(
            select(MicrostructureSnapshot)
            .where(MicrostructureSnapshot.symbol == symbol)
            .order_by(MicrostructureSnapshot.exchange_timestamp_ms)
        )
    )
    valid = [row for row in rows if row.is_valid]
    duplicates = max(0, len(rows) - len({row.exchange_timestamp_ms for row in rows}))
    gaps = sum(
        1
        for left, right in zip(valid, valid[1:], strict=False)
        if right.exchange_timestamp_ms - left.exchange_timestamp_ms > expected_interval_ms * 3
    )
    crossed = sum(1 for row in rows if row.invalid_reason == "crossed_book")
    invalid = sum(1 for row in rows if not row.is_valid)
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    freshness = now_ms - valid[-1].exchange_timestamp_ms if valid else None
    row = session.get(MicrostructureHealth, symbol) or MicrostructureHealth(symbol=symbol)
    row.rows_count = len(rows)
    row.duplicate_count = duplicates
    row.gap_count = gaps
    row.crossed_book_count = crossed
    row.invalid_count = invalid
    row.last_exchange_timestamp_ms = valid[-1].exchange_timestamp_ms if valid else None
    row.last_received_at = valid[-1].received_at if valid else None
    row.freshness_ms = freshness
    row.clock_skew_ms = valid[-1].clock_skew_ms if valid else None
    row.coverage_ratio = (
        Decimal("1")
        if valid and len(valid) == len(rows)
        else Decimal(str(len(valid) / len(rows)))
        if rows
        else Decimal("0")
    )
    row.overall_coverage_ratio = row.coverage_ratio
    row.updated_at = datetime.now(UTC)
    session.add(row)
    session.commit()
    return {
        "symbol": symbol,
        "rows_count": len(rows),
        "duplicates": duplicates,
        "gaps": gaps,
        "invalid": invalid,
        "freshness_ms": freshness,
    }
