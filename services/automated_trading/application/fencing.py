"""Fencing token mechanism for single-writer enforcement.

Prevents multiple V2 Runtime instances from writing simultaneously to the same
(symbol, execution_mode) combination. Each cycle generates a unique fencing token
and registers it in the database. Concurrent cycles with conflicting tokens are
rejected.

Usage:
    token = generate_fencing_token(symbol="BTC/USDT", mode=V2ExecutionMode.BINANCE_TESTNET)
    # Store token in V2ExecutionCycle
    # On next cycle, check for conflicts before proceeding
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select

from services.automated_trading.domain.enums import V2ExecutionMode
from services.automated_trading.infrastructure.models import V2ExecutionCycle

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def generate_fencing_token(symbol: str, mode: V2ExecutionMode, instance_id: str | None = None) -> str:
    """Generate unique fencing token for this cycle.

    Format: {symbol_normalized}@{mode}@{instance_id}@{timestamp}@{uuid}

    Args:
        symbol: Trading symbol (e.g., "BTC/USDT")
        mode: Execution mode
        instance_id: Optional runtime instance identifier

    Returns:
        Fencing token string
    """
    instance = instance_id or "default"
    timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    token_uuid = str(uuid.uuid4())[:8]
    symbol_normalized = symbol.replace("/", "")
    return f"{symbol_normalized}@{mode.value}@{instance}@{timestamp}@{token_uuid}"


def check_fencing_conflict(
    session: Session,
    symbol: str,
    mode: V2ExecutionMode,
    current_token: str,
    lookback_minutes: int = 5,
) -> tuple[bool, str | None]:
    """Check for fencing conflicts from concurrent cycles.

    A conflict exists if another fencing token for the same (symbol, mode)
    was created within lookback_minutes and belongs to a different cycle.

    Args:
        session: Database session
        symbol: Trading symbol
        mode: Execution mode
        current_token: This cycle's fencing token
        lookback_minutes: How far back to check for conflicts

    Returns:
        (has_conflict, conflicting_token)
    """
    cutoff = datetime.now(UTC) - timedelta(minutes=lookback_minutes)

    stmt = (
        select(V2ExecutionCycle.fencing_token, V2ExecutionCycle.started_at)
        .where(
            V2ExecutionCycle.symbol == symbol,
            V2ExecutionCycle.execution_mode == mode.value,
            V2ExecutionCycle.fencing_token != current_token,
        )
        .order_by(V2ExecutionCycle.started_at.desc())
        .limit(1)
    )

    result = session.execute(stmt).first()
    if result:
        token, started_at = result
        # Convert naive datetime from SQLite to aware UTC
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=UTC)
        if started_at >= cutoff:
            return True, token
    return False, None


def assert_no_active_v2_positions(session: Session, mode: V2ExecutionMode) -> None:
    """Assert no active V2 managed positions exist for this execution mode.

    This check is used during Legacy Writer startup to prevent concurrent
    position management. If V2 has open positions, Legacy Writer must not start.

    Raises:
        RuntimeError: If active V2 positions exist
    """
    from services.automated_trading.infrastructure.models import V2ManagedPosition

    stmt = select(V2ManagedPosition).where(
        V2ManagedPosition.execution_mode == mode.value,
        V2ManagedPosition.state.in_(["POSITION_PROJECTED", "PROTECTED", "REDUCING"]),
    )

    active_positions = session.scalars(stmt).all()
    if active_positions:
        position_summary = ", ".join(
            f"{p.symbol} {p.direction} (state={p.state}, id={p.position_id})" for p in active_positions
        )
        raise RuntimeError(
            f"Cannot start Legacy Writer: {len(active_positions)} active V2 managed positions exist "
            f"for mode={mode.value}. V2 positions: [{position_summary}]. "
            "Wait for V2 to close all positions or manually quarantine them before starting Legacy Writer."
        )
