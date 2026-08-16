from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session

from services.automated_trading.infrastructure.models import MicrostructureSnapshot


def apply_retention(session: Session, *, retention_days: int = 30) -> int:
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    result = session.execute(delete(MicrostructureSnapshot).where(MicrostructureSnapshot.received_at < cutoff))
    session.commit()
    return int(getattr(result, "rowcount", 0) or 0)
