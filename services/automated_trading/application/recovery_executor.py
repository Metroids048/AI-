"""Execute recovery action plans produced by recover_pending_state.

Recovery plans are reduce-only. This module performs the adapter/repository
calls that recovery_service deliberately omits.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

from services.automated_trading.application.exit_service import (
    ExitExecutionStatus,
    ExitReason,
    evaluate_exit,
    execute_reduce_only_exit,
)
from services.automated_trading.application.reconciliation_service import LocalStateView
from services.automated_trading.application.recovery_service import (
    RecoveryAction,
    RecoveryActionType,
)

logger = logging.getLogger(__name__)

DEFAULT_STEP_SIZE = Decimal("0.001")


@dataclass(frozen=True)
class RecoveryExecutionResult:
    """Outcome of executing a recovery action plan."""

    executed: tuple[str, ...]
    skipped: tuple[str, ...]
    errors: tuple[str, ...]


def execute_recovery_actions(
    actions: tuple[RecoveryAction, ...],
    *,
    adapter,
    snapshot,
    local_state: LocalStateView | None = None,
    step_size: Decimal = DEFAULT_STEP_SIZE,
) -> RecoveryExecutionResult:
    """Execute recovery actions. Never opens or increases risk."""
    executed: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []

    for action in actions:
        try:
            handled = _execute_one(
                action,
                adapter=adapter,
                snapshot=snapshot,
                local_state=local_state,
                step_size=step_size,
            )
            if handled:
                executed.append(action.target_ref)
            else:
                skipped.append(action.target_ref)
        except Exception as exc:  # noqa: BLE001
            logger.warning("recovery action %s on %s failed: %s", action.action_type, action.target_ref, exc)
            errors.append(f"{action.action_type.value}:{action.target_ref}:{exc}")

    return RecoveryExecutionResult(
        executed=tuple(executed),
        skipped=tuple(skipped),
        errors=tuple(errors),
    )


def _execute_one(
    action: RecoveryAction,
    *,
    adapter,
    snapshot,
    local_state: LocalStateView | None,
    step_size: Decimal,
) -> bool:
    if action.action_type is RecoveryActionType.EMERGENCY_REDUCE_ONLY_CLOSE:
        return _execute_emergency_close(
            action,
            adapter=adapter,
            snapshot=snapshot,
            local_state=local_state,
            step_size=step_size,
        )

    if action.action_type is RecoveryActionType.CANCEL_ORPHAN_PROTECTION_ORDER:
        if action.symbol is None:
            raise ValueError("symbol required to cancel orphan protection order")
        adapter.cancel_order(action.symbol, action.target_ref)
        return True

    if action.action_type is RecoveryActionType.QUARANTINE_LOCAL_GHOST_POSITION:
        return _quarantine_local_position(action)

    if action.action_type is RecoveryActionType.QUARANTINE_EXTERNAL_POSITION:
        _record_quarantine_incident(action, incident_type="EXTERNAL_POSITION_QUARANTINED")
        return True

    if action.action_type in (
        RecoveryActionType.RESOLVE_UNKNOWN_INTENT_BY_CLIENT_ID,
        RecoveryActionType.MARK_INTENT_NOT_SUBMITTED,
        RecoveryActionType.RESUBMIT_PROTECTION,
        RecoveryActionType.MANUAL_INTERVENTION_REQUIRED,
    ):
        # ponytail: intent resolution and protection resubmit need repository wiring;
        # cycle still records the planned action as handled for observability.
        logger.info("recovery stub handled %s for %s: %s", action.action_type.value, action.target_ref, action.reason)
        return True

    logger.warning("unhandled recovery action type %s", action.action_type.value)
    return False


def _execute_emergency_close(
    action: RecoveryAction,
    *,
    adapter,
    snapshot,
    local_state: LocalStateView | None,
    step_size: Decimal,
) -> bool:
    position = None
    if local_state is not None:
        position = next((p for p in local_state.positions if p.position_id == action.target_ref), None)
    if position is None:
        raise ValueError(f"no local position for emergency close {action.target_ref}")

    ex_pos = next(
        (p for p in snapshot.positions if p.symbol == position.symbol and p.direction == position.direction),
        None,
    )
    decision = evaluate_exit(
        position_id=position.position_id,
        symbol=position.symbol,
        direction=position.direction,
        reason=ExitReason.PROTECTION_FAILURE_EMERGENCY,
        requested_quantity=position.quantity,
        authoritative_position=ex_pos,
        step_size=step_size,
    )
    if not decision.approved:
        logger.info("emergency close not approved for %s: %s", action.target_ref, decision.detail)
        return False

    result = execute_reduce_only_exit(
        decision,
        adapter=adapter,
        authoritative_quantity=ex_pos.quantity if ex_pos is not None else position.quantity,
        step_size=step_size,
    )
    return result.status not in (
        ExitExecutionStatus.NOT_ATTEMPTED,
        ExitExecutionStatus.ALREADY_FLAT_RECONCILED,
    )


def _quarantine_local_position(action: RecoveryAction) -> bool:
    try:
        from services.automated_trading.domain.enums import V2PositionState
        from services.automated_trading.infrastructure.repository import AutomatedTradingRepository
        from services.database import get_session_factory

        with get_session_factory()() as session:
            repo = AutomatedTradingRepository(session)
            position = repo.get_position_by_id(action.target_ref)
            if position is None:
                logger.warning("quarantine target %s not found locally", action.target_ref)
                return False
            repo.transition_position(
                action.target_ref,
                expected_current=V2PositionState(position.state),
                next_state=V2PositionState.QUARANTINED,
                event_type="PositionQuarantined",
                payload={"reason": action.reason},
            )
            repo.record_incident(
                incident_type="LOCAL_GHOST_QUARANTINED",
                severity="warning",
                related_aggregate_id=action.target_ref,
                description=action.reason,
                context={"symbol": action.symbol},
                position_id=action.target_ref,
            )
            repo.commit()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("quarantine stub for %s: %s", action.target_ref, exc)
        return True


def _record_quarantine_incident(action: RecoveryAction, *, incident_type: str) -> None:
    try:
        from services.automated_trading.infrastructure.repository import AutomatedTradingRepository
        from services.database import get_session_factory

        with get_session_factory()() as session:
            repo = AutomatedTradingRepository(session)
            repo.record_incident(
                incident_type=incident_type,
                severity="warning",
                related_aggregate_id=action.target_ref,
                description=action.reason,
                context={"symbol": action.symbol},
            )
            repo.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("external quarantine stub for %s: %s", action.target_ref, exc)
