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


def _recovery_writer_valid(adapter) -> bool:
    """Require the machine account fence for Binance adapters; keep offline adapters usable."""
    from services.automated_trading.infrastructure.account_writer import AccountWriterCapability, capability_is_current
    from services.automated_trading.infrastructure.binance_adapter import BinanceTestnetAdapter

    capability = getattr(adapter, "writer_capability", None)
    if isinstance(adapter, BinanceTestnetAdapter):
        return isinstance(capability, AccountWriterCapability) and capability_is_current(capability)
    if not isinstance(capability, AccountWriterCapability):
        return True

    return capability_is_current(capability)


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

    if action.action_type is RecoveryActionType.RESOLVE_UNKNOWN_INTENT_BY_CLIENT_ID:
        return _resolve_unknown_intent(action, adapter=adapter, local_state=local_state)

    if action.action_type is RecoveryActionType.MARK_INTENT_NOT_SUBMITTED:
        return _mark_intent_not_submitted(action)

    if action.action_type is RecoveryActionType.RESUBMIT_PROTECTION:
        return _resubmit_protection(action, adapter=adapter, snapshot=snapshot)

    if action.action_type is RecoveryActionType.MANUAL_INTERVENTION_REQUIRED:
        _record_quarantine_incident(action, incident_type="MANUAL_INTERVENTION_REQUIRED")
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
        fencing_token_valid=_recovery_writer_valid(adapter),
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
            severity="MEDIUM",
            related_aggregate_id=action.target_ref,
            description=action.reason,
            context={"symbol": action.symbol},
            position_id=action.target_ref,
        )
        repo.commit()
    return True


def _record_quarantine_incident(action: RecoveryAction, *, incident_type: str) -> None:
    from services.automated_trading.infrastructure.repository import AutomatedTradingRepository
    from services.database import get_session_factory

    with get_session_factory()() as session:
        repo = AutomatedTradingRepository(session)
        repo.record_incident(
            incident_type=incident_type,
            severity="MEDIUM",
            related_aggregate_id=action.target_ref,
            description=action.reason,
            context={"symbol": action.symbol},
        )
        repo.commit()


def _mark_intent_not_submitted(action: RecoveryAction) -> bool:
    from sqlalchemy import select

    from services.automated_trading.domain.enums import V2IntentState
    from services.automated_trading.infrastructure.models import V2ExchangeOrder, V2ExecutionIntent
    from services.automated_trading.infrastructure.repository import AutomatedTradingRepository
    from services.database import get_session_factory

    with get_session_factory()() as session:
        intent = session.get(V2ExecutionIntent, action.target_ref)
        if intent is None:
            raise ValueError(f"recovery intent {action.target_ref} not found")
        known_order = session.scalar(select(V2ExchangeOrder).where(V2ExchangeOrder.intent_id == action.target_ref))
        if known_order is not None and known_order.exchange_order_id:
            return False
        current = V2IntentState(intent.state)
        if current in {
            V2IntentState.CANCELLED,
            V2IntentState.REJECTED,
            V2IntentState.EXPIRED,
        }:
            return True
        AutomatedTradingRepository(session).transition_intent(
            action.target_ref,
            expected_current=current,
            next_state=V2IntentState.CANCELLED,
            event_type="RecoveryConfirmedNotSubmitted",
            payload={"reason": action.reason},
        )
        session.commit()
    return True


def _resolve_unknown_intent(
    action: RecoveryAction,
    *,
    adapter,
    local_state: LocalStateView | None,
) -> bool:
    from sqlalchemy import select

    from services.automated_trading.domain.enums import V2IntentState
    from services.automated_trading.infrastructure.models import (
        V2ExchangeOrder,
        V2ExecutionIntent,
    )
    from services.automated_trading.infrastructure.repository import AutomatedTradingRepository
    from services.database import get_session_factory

    intent_view = None
    if local_state is not None:
        intent_view = next(
            (intent for intent in local_state.intents if intent.intent_id == action.target_ref),
            None,
        )
    if intent_view is None:
        raise ValueError(f"no local pending-intent identity for {action.target_ref}")
    receipt = adapter.query_order_by_client_id(
        intent_view.symbol,
        intent_view.client_order_id,
    )
    if receipt is None:
        return _mark_intent_not_submitted(action)

    with get_session_factory()() as session:
        repo = AutomatedTradingRepository(session)
        intent = session.get(V2ExecutionIntent, action.target_ref)
        order = session.scalar(select(V2ExchangeOrder).where(V2ExchangeOrder.intent_id == action.target_ref))
        if intent is None or order is None:
            raise ValueError(f"pending intent/order facts missing for {action.target_ref}")
        repo.save_exchange_order_receipt(
            order_record_id=order.order_record_id,
            exchange_order_id=receipt.exchange_order_id,
            acknowledged_at=receipt.acknowledged_at,
        )
        current = V2IntentState(intent.state)
        if current is not V2IntentState.EXCHANGE_ACKNOWLEDGED:
            repo.transition_intent(
                action.target_ref,
                expected_current=current,
                next_state=V2IntentState.EXCHANGE_ACKNOWLEDGED,
                event_type="RecoveryExchangeAcknowledged",
                payload={"exchange_order_id": receipt.exchange_order_id},
            )
        repo.commit()

    # Fill recovery deliberately stops at an acknowledged fact here. A missing
    # persisted risk plan cannot be guessed; the next pass remains Entry-blocked
    # and requires a safe emergency-close workflow instead of blind adoption.
    return True


def _resubmit_protection(
    action: RecoveryAction,
    *,
    adapter,
    snapshot,
) -> bool:
    import uuid
    from datetime import UTC, datetime

    from sqlalchemy import select

    from services.automated_trading.application.protection_service import (
        ProtectionPlan,
        ensure_protection,
    )
    from services.automated_trading.domain.client_order_id import (
        stop_client_order_id,
        target_client_order_id,
    )
    from services.automated_trading.domain.enums import V2ProtectionState
    from services.automated_trading.domain.receipts import ProtectionReceipt
    from services.automated_trading.infrastructure.models import (
        V2ManagedPosition,
        V2ProtectionRecord,
    )
    from services.automated_trading.infrastructure.repository import AutomatedTradingRepository
    from services.database import get_session_factory

    with get_session_factory()() as session:
        position = session.get(V2ManagedPosition, action.target_ref)
        if position is None:
            raise ValueError(f"protection recovery position {action.target_ref} not found")
        previous = session.scalar(
            select(V2ProtectionRecord)
            .where(V2ProtectionRecord.position_id == action.target_ref)
            .order_by(V2ProtectionRecord.created_at.desc())
            .limit(1)
        )
        if previous is None:
            raise ValueError(
                f"position {action.target_ref} has no persisted protection prices; "
                "cannot invent stop/take-profit during recovery"
            )
        exchange_position = next(
            (
                item
                for item in snapshot.positions
                if item.symbol == position.symbol and item.direction == position.direction
            ),
            None,
        )
        if exchange_position is None or exchange_position.quantity <= 0:
            return False
        revision = int(previous.version) + 2
        stop_client_id = stop_client_order_id(position.position_id, revision=revision)
        tp_client_id = (
            target_client_order_id(position.position_id, revision=revision)
            if previous.take_profit_price is not None
            else None
        )
        plan = ProtectionPlan(
            position_id=position.position_id,
            symbol=position.symbol,
            direction=position.direction,
            quantity=exchange_position.quantity,
            average_fill_price=position.entry_price,
            stop_price=previous.stop_loss_price,
            take_profit_price=previous.take_profit_price,
            stop_client_order_id=stop_client_id,
            tp_client_order_id=tp_client_id,
            attempt=revision,
        )

    protection_result = ensure_protection(plan, adapter=adapter)
    if not protection_result.is_active or not protection_result.stop_exchange_order_id:
        _record_quarantine_incident(
            action,
            incident_type="PROTECTION_RECOVERY_FAILED",
        )
        return False

    with get_session_factory()() as session:
        repo = AutomatedTradingRepository(session)
        previous = session.scalar(
            select(V2ProtectionRecord)
            .where(V2ProtectionRecord.position_id == action.target_ref)
            .order_by(V2ProtectionRecord.created_at.desc())
            .limit(1)
        )
        if previous is not None and previous.state == V2ProtectionState.PROTECTION_ACTIVE.value:
            repo.transition_protection(
                previous.protection_id,
                expected_current=V2ProtectionState.PROTECTION_ACTIVE,
                next_state=V2ProtectionState.PROTECTION_CANCELLED,
                event_type="ProtectionSupersededByRecovery",
                payload={"reason": action.reason},
            )
        protection_id = str(uuid.uuid4())
        repo.save_protection(
            protection_id=protection_id,
            position_id=action.target_ref,
            stop_loss_price=float(plan.stop_price),
            take_profit_price=float(plan.take_profit_price) if plan.take_profit_price is not None else None,
            stop_client_order_id=plan.stop_client_order_id,
            tp_client_order_id=plan.tp_client_order_id,
            state=V2ProtectionState.PROTECTION_INTENT,
        )
        repo.update_protection_active(
            protection_id,
            receipt=ProtectionReceipt(
                position_id=action.target_ref,
                stop_exchange_order_id=protection_result.stop_exchange_order_id,
                tp_exchange_order_id=protection_result.tp_exchange_order_id,
                submission_timestamp=datetime.now(UTC),
            ),
            new_state=V2ProtectionState.PROTECTION_ACTIVE,
            activated_at=datetime.now(UTC),
        )
        repo.commit()
    return True
