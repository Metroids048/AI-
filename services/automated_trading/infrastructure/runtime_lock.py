"""Runtime lock and engine activation resolution for V2 automated trading.

Enforces the single-writer invariant: only one automated trading engine can
write to Binance Testnet at a time. Provides clear engine activation states
(DISABLED, SHADOW, ACTIVE) and rejects ambiguous legacy modes like
'mirror_to_gateway' and 'binance_simulation_first'.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from services.automated_trading.domain.enums import V2ExecutionMode

if TYPE_CHECKING:
    from shared.config import Settings


class EngineActivation(StrEnum):
    """V2 Engine activation state.

    DISABLED: V2 does not evaluate or submit orders.
    SHADOW: V2 evaluates and creates decision records, but never submits to exchange.
    ACTIVE: V2 is the sole writer to Binance Testnet (or Local Paper in that mode).
    """

    DISABLED = "DISABLED"
    SHADOW = "SHADOW"
    ACTIVE = "ACTIVE"


@dataclass(frozen=True)
class EngineActivationConfig:
    """Resolved engine activation configuration."""

    v2_activation: EngineActivation
    execution_mode: V2ExecutionMode
    allow_legacy_writer: bool
    warnings: list[str]


def resolve_engine_activation(settings: Settings) -> EngineActivationConfig:
    """Resolve engine activation from settings.

    Args:
        settings: Application settings

    Returns:
        EngineActivationConfig with resolved state and warnings

    Raises:
        ValueError: If configuration is invalid (mainnet mode, both engines active, etc.)
    """
    warnings: list[str] = []

    # Read the new canonical config flag
    engine_flag = getattr(settings, "automated_trading_engine", "legacy").lower()

    # Reject mainnet configurations
    if not settings.binance_use_testnet:
        raise ValueError(
            "V2 automated trading requires BINANCE_USE_TESTNET=true. Mainnet execution is not implemented in V2."
        )
    if settings.live_trading_enabled:
        raise ValueError(
            "V2 automated trading requires LIVE_TRADING_ENABLED=false. Mainnet execution is not implemented in V2."
        )

    # Map engine flag to activation state
    if engine_flag == "legacy":
        v2_activation = EngineActivation.DISABLED
        allow_legacy = True
    elif engine_flag == "v2_shadow":
        v2_activation = EngineActivation.SHADOW
        allow_legacy = True
        warnings.append(
            "V2 is in SHADOW mode: decision records will be created but no exchange orders will be submitted."
        )
    elif engine_flag == "v2_active":
        v2_activation = EngineActivation.ACTIVE
        allow_legacy = False
        warnings.append("V2 is ACTIVE: V2 is the sole writer to Binance Testnet. Legacy writer is disabled.")
    else:
        raise ValueError(
            f"Invalid AUTOMATED_TRADING_ENGINE={engine_flag}. Must be one of: legacy, v2_shadow, v2_active."
        )

    # Determine execution mode
    # V2 uses LOCAL_PAPER when binance_auto_execute=false, otherwise BINANCE_TESTNET
    if settings.binance_auto_execute:
        execution_mode = V2ExecutionMode.BINANCE_TESTNET
    else:
        execution_mode = V2ExecutionMode.LOCAL_PAPER
        warnings.append("binance_auto_execute=false: V2 will use LOCAL_PAPER mode (no exchange contact).")

    # Reject both engines active simultaneously
    if v2_activation == EngineActivation.ACTIVE and allow_legacy:
        raise ValueError(
            "Configuration error: V2 is ACTIVE but Legacy Writer is also enabled. "
            "Only one automated trading writer is allowed at a time."
        )

    return EngineActivationConfig(
        v2_activation=v2_activation,
        execution_mode=execution_mode,
        allow_legacy_writer=allow_legacy,
        warnings=warnings,
    )


@dataclass(frozen=True)
class WriterLease:
    """Writer lease for a specific execution scope."""

    engine_id: str
    symbol: str
    execution_mode: V2ExecutionMode
    fencing_token: str
    acquired_at: str  # ISO8601 UTC


def acquire_testnet_writer(engine_id: str, symbol: str, mode: V2ExecutionMode, fencing_token: str) -> WriterLease:
    """Acquire exclusive writer lease for this execution scope.

    Args:
        engine_id: Engine identifier (e.g., 'v2_cycle_service')
        symbol: Trading symbol
        mode: Execution mode
        fencing_token: Fencing token from this cycle

    Returns:
        WriterLease if successful

    Raises:
        RuntimeError: If another writer holds the lease
    """
    from datetime import UTC, datetime

    from services.automated_trading.application.fencing import check_fencing_conflict
    from shared.database import SessionLocal

    with SessionLocal() as session:
        has_conflict, conflicting_token = check_fencing_conflict(
            session=session,
            symbol=symbol,
            mode=mode,
            current_token=fencing_token,
            lookback_minutes=5,
        )
        if has_conflict:
            raise RuntimeError(
                f"Cannot acquire writer lease for {symbol}@{mode.value}: "
                f"conflicting fencing token detected: {conflicting_token}"
            )

    return WriterLease(
        engine_id=engine_id,
        symbol=symbol,
        execution_mode=mode,
        fencing_token=fencing_token,
        acquired_at=datetime.now(UTC).isoformat(),
    )
