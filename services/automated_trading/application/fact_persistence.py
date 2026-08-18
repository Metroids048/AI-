"""Persist V2 execution facts after confirmed exchange receipts.

Called from the cycle when ``CycleRequest.persist_facts`` is True.
Never invents fills — only writes facts already confirmed by the adapter.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select, text

from services.automated_trading.application.entry_service import EntryExecutionStatus
from services.automated_trading.application.exit_service import (
    ExitExecutionResult,
    ExitExecutionStatus,
    ExitReason,
)
from services.automated_trading.audit.forward_baseline import build_shadow_outcome
from services.automated_trading.domain.enums import (
    V2CandidateType,
    V2ExecutionMode,
    V2IntentState,
    V2PositionState,
    V2ProtectionState,
)
from services.automated_trading.domain.portfolio_risk import (
    MAX_OPEN_POSITIONS,
    evaluate_portfolio_risk,
    portfolio_risk_blocks,
)
from services.automated_trading.domain.receipts import ProtectionReceipt
from services.automated_trading.infrastructure.models import (
    V2DecisionSnapshot,
    V2ExchangeOrder,
    V2ExecutionCycle,
    V2ExecutionDecision,
    V2ExecutionIntent,
    V2ManagedPosition,
    V2ProtectionRecord,
    V2RuntimeControl,
)
from services.automated_trading.infrastructure.repository import AutomatedTradingRepository
from services.database import get_session_factory

logger = logging.getLogger(__name__)


def _exit_intent_id(client_order_id: str) -> str:
    """Return the stable local intent identity for one exchange exit attempt."""
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"automated-trading-v2:exit:{client_order_id}",
        )
    )


def persist_entry_intent_before_submission(
    *,
    cycle_id: str,
    decision_id: str | None,
    intent_id: str,
    symbol: str,
    direction: str,
    candidate_key: str,
    candidate_type: V2CandidateType,
    execution_mode: V2ExecutionMode,
    decision_bar_timestamp: datetime,
    fencing_token: str,
    initial_risk_usdt: Decimal,
    account_equity: Decimal | None = None,
    portfolio_risk_enforced: bool = True,
    max_open_positions: int = MAX_OPEN_POSITIONS,
) -> str | None:
    """Atomically gate and reserve one new entry's durable initial risk.

    SQLite's immediate write transaction serializes the budget read with intent
    insertion. A competing writer cannot evaluate the same unreserved budget.
    """
    if not initial_risk_usdt.is_finite() or initial_risk_usdt <= 0:
        raise ValueError("initial_risk_usdt must be finite and positive for new exposure")
    with get_session_factory()() as session:
        if session.bind is not None:
            if session.bind.dialect.name == "sqlite":
                session.execute(text("BEGIN IMMEDIATE"))
            elif session.bind.dialect.name == "postgresql":
                session.execute(
                    text("SELECT pg_advisory_xact_lock(hashtext(:scope))"),
                    {"scope": f"automated-trading-v2:portfolio-risk:{execution_mode.value}"},
                )
        repo = AutomatedTradingRepository(session)
        control = session.get(V2RuntimeControl, "global")
        if control is not None and not control.entry_enabled:
            session.rollback()
            return "ENTRY_PAUSED"
        committed, has_unknown_active_risk = repo.list_active_initial_risk_exposures(execution_mode)
        if has_unknown_active_risk:
            session.rollback()
            return "UNKNOWN_ACTIVE_INTENT_RISK"
        if account_equity is not None:
            verdict = evaluate_portfolio_risk(
                equity=account_equity,
                candidate_symbol=symbol,
                candidate_direction=direction,
                candidate_initial_risk_usdt=initial_risk_usdt,
                committed=committed,
                max_open_positions=max_open_positions,
            )
            if portfolio_risk_blocks(verdict, diagnostic=not portfolio_risk_enforced):
                session.rollback()
                return verdict.reason_code
        if session.get(V2ExecutionCycle, cycle_id) is None:
            repo.create_cycle(
                cycle_id=cycle_id,
                symbol=symbol,
                timeframe="15m",
                bar_timestamp=decision_bar_timestamp,
                execution_mode=execution_mode,
                fencing_token=fencing_token,
            )
        if decision_id and session.get(V2ExecutionDecision, decision_id) is None:
            repo.create_decision(
                decision_id=decision_id,
                cycle_id=cycle_id,
                candidate_key=candidate_key,
                terminal_reason=None,
                payload={},
            )
        intent = session.get(V2ExecutionIntent, intent_id)
        if intent is None:
            repo.create_intent(
                intent_id=intent_id,
                cycle_id=cycle_id,
                symbol=symbol,
                direction=direction,
                candidate_key=candidate_key,
                candidate_type=candidate_type,
                execution_mode=execution_mode,
                decision_bar_timestamp=decision_bar_timestamp,
                decision_funnel_id=None,
                state=V2IntentState.INTENT_CREATED,
                decision_id=decision_id,
                initial_risk_usdt=initial_risk_usdt,
            )
            repo.transition_intent(
                intent_id,
                expected_current=V2IntentState.INTENT_CREATED,
                next_state=V2IntentState.EXCHANGE_SUBMITTING,
                event_type="EntrySubmitting",
                payload={"client_order_identity": "derived_from_intent_id"},
            )
        session.commit()
    return None


def persist_entry_submission_result(
    *,
    intent_id: str,
    leverage: int,
    result: Any,
) -> None:
    """Persist exchange acknowledgement/unknown/rejection without inventing a fill."""
    with get_session_factory()() as session:
        repo = AutomatedTradingRepository(session)
        intent = session.get(V2ExecutionIntent, intent_id)
        if intent is None:
            raise ValueError(f"Intent {intent_id!r} not found before exchange result")
        current = V2IntentState(intent.state)

        if result.status is EntryExecutionStatus.UNKNOWN:
            repo.transition_intent(
                intent_id,
                expected_current=current,
                next_state=V2IntentState.EXCHANGE_UNKNOWN,
                event_type="EntryOutcomeUnknown",
                payload={"client_order_id": result.client_order_id},
            )
        elif result.status is EntryExecutionStatus.REJECTED and result.exchange_order_id:
            order = session.scalar(select(V2ExchangeOrder).where(V2ExchangeOrder.intent_id == intent_id))
            order_record_id = (
                order.order_record_id
                if order is not None
                else repo.save_order_submission(
                    intent_id=intent_id,
                    client_order_id=result.client_order_id,
                    quantity=float(result.requested_quantity or result.filled_quantity),
                    leverage=leverage,
                    submitted_at=result.fill_timestamp or datetime.now(UTC),
                )
            )
            repo.save_exchange_order_receipt(
                order_record_id=order_record_id,
                exchange_order_id=str(result.exchange_order_id),
                acknowledged_at=result.fill_timestamp or datetime.now(UTC),
            )
            if result.trade_ids and result.filled_quantity > 0 and result.average_fill_price is not None:
                fill_count = len(result.trade_ids)
                per_qty = (result.filled_quantity / fill_count).quantize(Decimal("0.00000001"))
                allocated = Decimal("0")
                for index, trade_id in enumerate(result.trade_ids):
                    quantity = result.filled_quantity - allocated if index == fill_count - 1 else per_qty
                    allocated += quantity
                    repo.save_exchange_fill_receipt(
                        intent_id=intent_id,
                        exchange_order_record_id=order_record_id,
                        account_id="binance_testnet",
                        exchange_order_id=str(result.exchange_order_id),
                        trade_id=str(trade_id),
                        symbol=intent.symbol,
                        side="BUY" if intent.direction == "long" else "SELL",
                        reduce_only=False,
                        filled_quantity=quantity,
                        fill_price=result.average_fill_price,
                        commission=(result.total_fee / fill_count),
                        commission_asset="USDT",
                        exchange_event_time=result.fill_timestamp or datetime.now(UTC),
                        received_at=datetime.now(UTC),
                        raw_hash=f"{result.exchange_order_id}:{trade_id}",
                    )
            if current in {V2IntentState.EXCHANGE_SUBMITTING, V2IntentState.EXCHANGE_UNKNOWN}:
                repo.transition_intent(
                    intent_id,
                    expected_current=current,
                    next_state=V2IntentState.EXCHANGE_ACKNOWLEDGED,
                    event_type="EntryAcknowledgedWithMarginGuard",
                    payload={
                        "exchange_order_id": result.exchange_order_id,
                        "trade_ids": list(result.trade_ids),
                        "margin_guard_exchange_order_id": result.guard_exchange_order_id,
                        "margin_guard_client_order_id": result.guard_client_order_id,
                        "detail": result.detail,
                    },
                )
        elif result.status is EntryExecutionStatus.REJECTED:
            repo.transition_intent(
                intent_id,
                expected_current=current,
                next_state=V2IntentState.REJECTED,
                event_type="EntryRejected",
                payload={"detail": result.detail},
            )
        elif result.status is EntryExecutionStatus.NOT_ATTEMPTED:
            repo.transition_intent(
                intent_id,
                expected_current=current,
                next_state=V2IntentState.CANCELLED,
                event_type="EntryCancelledBeforeSubmit",
                payload={"detail": result.detail},
            )
        elif result.exchange_order_id:
            order = session.scalar(select(V2ExchangeOrder).where(V2ExchangeOrder.intent_id == intent_id))
            if order is None:
                order_record_id = repo.save_order_submission(
                    intent_id=intent_id,
                    client_order_id=result.client_order_id,
                    quantity=float(result.requested_quantity),
                    leverage=leverage,
                    submitted_at=datetime.now(UTC),
                )
            else:
                order_record_id = order.order_record_id
            repo.save_exchange_order_receipt(
                order_record_id=order_record_id,
                exchange_order_id=str(result.exchange_order_id),
                acknowledged_at=datetime.now(UTC),
            )
            if current in {
                V2IntentState.EXCHANGE_SUBMITTING,
                V2IntentState.EXCHANGE_UNKNOWN,
            }:
                repo.transition_intent(
                    intent_id,
                    expected_current=current,
                    next_state=V2IntentState.EXCHANGE_ACKNOWLEDGED,
                    event_type="EntryAcknowledged",
                    payload={"exchange_order_id": result.exchange_order_id},
                )
        session.commit()


def persist_entry_and_protection(
    *,
    cycle_id: str,
    decision_id: str | None,
    intent_id: str,
    symbol: str,
    direction: str,
    candidate_key: str,
    candidate_type: V2CandidateType,
    execution_mode: V2ExecutionMode,
    decision_bar_timestamp: datetime,
    fencing_token: str,
    leverage: int,
    entry_result: Any,
    position_id: str,
    protection_result: Any | None,
    stop_loss_price: Decimal | None,
    take_profit_price: Decimal | None,
    stop_client_order_id: str | None,
    tp_client_order_id: str | None,
    initial_risk_usdt: Decimal | None = None,
    project_position: bool = True,
    allow_existing_position_aggregation: bool = False,
) -> None:
    """Write entry facts after a confirmed fill without inventing historical risk.

    Recovery first keeps the original durable reservation. For legacy entries it
    reconstructs only from the confirmed fill and the persisted original stop;
    otherwise the active intent remains UNKNOWN and blocks later new exposure.
    """
    reconstructed_initial_risk = initial_risk_usdt
    if reconstructed_initial_risk is None and stop_loss_price is not None:
        fill_price = getattr(entry_result, "average_fill_price", None)
        fill_quantity = getattr(entry_result, "filled_quantity", None)
        if fill_price is not None and fill_quantity is not None:
            reconstructed_initial_risk = abs(Decimal(str(fill_price)) - stop_loss_price) * Decimal(str(fill_quantity))
    if reconstructed_initial_risk is not None and reconstructed_initial_risk <= 0:
        raise ValueError("initial_risk_usdt must be positive when reconstructable")
    if not getattr(entry_result, "position_projectable", False):
        return

    with get_session_factory()() as session:
        repo = AutomatedTradingRepository(session)
        if session.get(V2ExecutionCycle, cycle_id) is None:
            repo.create_cycle(
                cycle_id=cycle_id,
                symbol=symbol,
                timeframe="15m",
                bar_timestamp=decision_bar_timestamp,
                execution_mode=execution_mode,
                fencing_token=fencing_token,
            )

        if decision_id and session.get(V2ExecutionDecision, decision_id) is None:
            repo.create_decision(
                decision_id=decision_id,
                cycle_id=cycle_id,
                candidate_key=candidate_key,
                terminal_reason=None,
                payload={},
            )

        intent = session.get(V2ExecutionIntent, intent_id)
        if intent is None:
            repo.create_intent(
                intent_id=intent_id,
                cycle_id=cycle_id,
                symbol=symbol,
                direction=direction,
                candidate_key=candidate_key,
                candidate_type=candidate_type,
                execution_mode=execution_mode,
                decision_bar_timestamp=decision_bar_timestamp,
                decision_funnel_id=None,
                state=V2IntentState.INTENT_CREATED,
                decision_id=decision_id,
                initial_risk_usdt=reconstructed_initial_risk,
            )
            repo.transition_intent(
                intent_id,
                expected_current=V2IntentState.INTENT_CREATED,
                next_state=V2IntentState.EXCHANGE_SUBMITTING,
                event_type="Submitting",
                payload={},
            )
            intent = session.get(V2ExecutionIntent, intent_id)
        elif intent.initial_risk_usdt is None and reconstructed_initial_risk is not None:
            intent.initial_risk_usdt = reconstructed_initial_risk
            repo.append_event(
                aggregate_id=intent_id,
                aggregate_type="INTENT",
                event_type="InitialRiskReconstructedFromAuthoritativeFillAndStop",
                event_payload={
                    "initial_risk_usdt": str(reconstructed_initial_risk),
                    "source": "authoritative_fill_and_original_stop",
                },
                occurred_at=entry_result.fill_timestamp or datetime.now(UTC),
            )
            session.flush()
        if intent is None:
            raise ValueError(f"Intent {intent_id!r} was not durable after creation")
        current_intent_state = V2IntentState(intent.state)
        if current_intent_state in {
            V2IntentState.EXCHANGE_SUBMITTING,
            V2IntentState.EXCHANGE_UNKNOWN,
        }:
            repo.transition_intent(
                intent_id,
                expected_current=current_intent_state,
                next_state=V2IntentState.EXCHANGE_ACKNOWLEDGED,
                event_type="Acked",
                payload={"exchange_order_id": entry_result.exchange_order_id},
            )

        existing_order = session.scalar(select(V2ExchangeOrder).where(V2ExchangeOrder.intent_id == intent_id))
        order_id = (
            existing_order.order_record_id
            if existing_order is not None
            else repo.save_order_submission(
                intent_id=intent_id,
                client_order_id=entry_result.client_order_id,
                quantity=float(entry_result.requested_quantity or entry_result.filled_quantity),
                leverage=leverage,
                submitted_at=entry_result.fill_timestamp or datetime.now(UTC),
            )
        )
        repo.save_exchange_order_receipt(
            order_record_id=order_id,
            exchange_order_id=str(entry_result.exchange_order_id),
            acknowledged_at=entry_result.fill_timestamp or datetime.now(UTC),
        )
        if getattr(entry_result, "fill_source", "BINANCE_USER_TRADE") == "BINANCE_ORDER_STATUS_RECOVERY":
            repo.save_order_level_fill_recovery(
                intent_id=intent_id,
                exchange_order_record_id=order_id,
                account_id="binance_testnet",
                exchange_order_id=str(entry_result.exchange_order_id),
                symbol=symbol,
                side="BUY" if direction == "long" else "SELL",
                reduce_only=False,
                filled_quantity=entry_result.filled_quantity,
                fill_price=entry_result.average_fill_price or Decimal("0"),
                exchange_event_time=entry_result.fill_timestamp or datetime.now(UTC),
                received_at=datetime.now(UTC),
            )
        else:
            n = max(len(entry_result.trade_ids), 1)
            per_qty = (entry_result.filled_quantity / n).quantize(Decimal("0.00000001"))
            allocated = Decimal("0")
            for idx, trade_id in enumerate(entry_result.trade_ids):
                qty = entry_result.filled_quantity - allocated if idx == n - 1 else per_qty
                allocated += qty
                fee = (entry_result.total_fee / n) if n else entry_result.total_fee
                existing_fill = repo.get_exchange_fill_by_trade(
                    account_id="binance_testnet",
                    trade_id=str(trade_id),
                )
                if existing_fill is not None:
                    if (
                        existing_fill.intent_id != intent_id
                        or existing_fill.exchange_order_record_id != order_id
                        or existing_fill.exchange_order_id != str(entry_result.exchange_order_id)
                    ):
                        raise ValueError(
                            "exchange fill identity is already attributed to a different V2 order: "
                            f"account_id='binance_testnet' trade_id={trade_id!r}"
                        )
                    continue
                repo.save_exchange_fill_receipt(
                    intent_id=intent_id,
                    exchange_order_record_id=order_id,
                    account_id="binance_testnet",
                    exchange_order_id=str(entry_result.exchange_order_id),
                    trade_id=str(trade_id),
                    symbol=symbol,
                    side="BUY" if direction == "long" else "SELL",
                    reduce_only=False,
                    filled_quantity=qty,
                    fill_price=entry_result.average_fill_price or Decimal("0"),
                    commission=fee,
                    commission_asset="USDT",
                    exchange_event_time=entry_result.fill_timestamp or datetime.now(UTC),
                    received_at=datetime.now(UTC),
                    raw_hash=f"{entry_result.exchange_order_id}:{trade_id}",
                )
        repo.reconcile_intent_from_confirmed_fill(intent_id)

        if project_position:
            repo.project_position_from_confirmed_fills(
                position_id=position_id,
                intent_id=intent_id,
                order_record_id=order_id,
                symbol=symbol,
                direction=direction,
                execution_mode=execution_mode,
                projected_at=datetime.now(UTC),
                allow_existing_position_aggregation=allow_existing_position_aggregation,
            )

        if protection_result is not None and stop_client_order_id and stop_loss_price is not None:
            prot_id = str(uuid.uuid4())
            repo.save_protection(
                protection_id=prot_id,
                position_id=position_id,
                stop_loss_price=float(stop_loss_price),
                take_profit_price=float(take_profit_price) if take_profit_price else None,
                stop_client_order_id=stop_client_order_id,
                tp_client_order_id=tp_client_order_id,
                state=V2ProtectionState.PROTECTION_INTENT,
            )
            if getattr(protection_result, "is_active", False) and protection_result.stop_exchange_order_id:
                repo.transition_protection(
                    protection_id=prot_id,
                    expected_current=V2ProtectionState.PROTECTION_INTENT,
                    next_state=V2ProtectionState.PROTECTION_SUBMITTING,
                    event_type="Submitting",
                    payload={},
                )
                repo.update_protection_active(
                    protection_id=prot_id,
                    receipt=ProtectionReceipt(
                        position_id=position_id,
                        stop_exchange_order_id=protection_result.stop_exchange_order_id,
                        tp_exchange_order_id=protection_result.tp_exchange_order_id,
                        submission_timestamp=datetime.now(UTC),
                    ),
                    new_state=V2ProtectionState.PROTECTION_ACTIVE,
                    activated_at=datetime.now(UTC),
                )
                position = session.get(V2ManagedPosition, position_id)
                if position is not None and position.state == V2PositionState.POSITION_PROJECTED.value:
                    repo.transition_position(
                        position_id=position_id,
                        expected_current=V2PositionState.POSITION_PROJECTED,
                        next_state=V2PositionState.PROTECTED,
                        event_type="Protected",
                        payload={},
                    )

        session.commit()
        logger.info(
            "persisted entry facts cycle=%s intent=%s position=%s trades=%s",
            cycle_id,
            intent_id,
            position_id,
            list(entry_result.trade_ids),
        )


def persist_exit_intent_before_submission(
    *,
    cycle_id: str,
    position_id: str,
    execution_mode: V2ExecutionMode,
    reason: ExitReason,
    client_order_id: str,
    requested_quantity: Decimal,
    fencing_token: str,
    submitted_at: datetime,
) -> str:
    """Durably record an exit attempt before calling the exchange.

    The intent id is derived from the deterministic client order id, so an
    acknowledged order can always be recovered without symbol/price guessing.
    No position quantity or state is changed at this boundary.
    """
    if requested_quantity <= 0:
        raise ValueError("exit requested_quantity must be positive")
    exit_intent_id = _exit_intent_id(client_order_id)
    with get_session_factory()() as session:
        repo = AutomatedTradingRepository(session)
        position = session.get(V2ManagedPosition, position_id)
        if position is None:
            raise ValueError(f"Position {position_id!r} not found")
        entry_intent = session.get(V2ExecutionIntent, position.intent_id)
        if entry_intent is None:
            raise ValueError(f"Entry intent {position.intent_id!r} not found")
        entry_order = session.get(V2ExchangeOrder, position.order_record_id)
        if entry_order is None:
            raise ValueError(f"Entry order {position.order_record_id!r} not found")

        if session.get(V2ExecutionCycle, cycle_id) is None:
            repo.create_cycle(
                cycle_id=cycle_id,
                symbol=position.symbol,
                timeframe="15m",
                bar_timestamp=submitted_at,
                execution_mode=execution_mode,
                fencing_token=fencing_token,
            )

        intent = session.get(V2ExecutionIntent, exit_intent_id)
        if intent is None:
            repo.create_intent(
                intent_id=exit_intent_id,
                cycle_id=cycle_id,
                symbol=position.symbol,
                direction=position.direction,
                candidate_key=f"exit:{position_id}:{reason.value}",
                candidate_type=V2CandidateType(entry_intent.candidate_type),
                execution_mode=execution_mode,
                decision_bar_timestamp=submitted_at,
                decision_funnel_id=None,
                state=V2IntentState.INTENT_CREATED,
                decision_id=None,
            )
            repo.transition_intent(
                exit_intent_id,
                expected_current=V2IntentState.INTENT_CREATED,
                next_state=V2IntentState.EXCHANGE_SUBMITTING,
                event_type="ExitSubmitting",
                payload={
                    "position_id": position_id,
                    "reason": reason.value,
                    "reduce_only": True,
                    "client_order_id": client_order_id,
                },
                occurred_at=submitted_at,
            )

        order = session.scalar(select(V2ExchangeOrder).where(V2ExchangeOrder.client_order_id == client_order_id))
        if order is None:
            repo.save_order_submission(
                intent_id=exit_intent_id,
                client_order_id=client_order_id,
                quantity=float(requested_quantity),
                leverage=int(entry_order.leverage),
                submitted_at=submitted_at,
            )
        elif order.intent_id != exit_intent_id:
            raise ValueError(
                f"Exit client order id {client_order_id!r} belongs to "
                f"intent {order.intent_id!r}, expected {exit_intent_id!r}"
            )
        repo.commit()
    return exit_intent_id


def persist_exit_submission_result(
    *,
    position_id: str,
    result: ExitExecutionResult,
) -> None:
    """Persist exit ACK/unknown/failure immediately, before any fill exists."""
    exit_intent_id = _exit_intent_id(result.client_order_id)
    with get_session_factory()() as session:
        repo = AutomatedTradingRepository(session)
        position = session.get(V2ManagedPosition, position_id)
        if position is None:
            raise ValueError(f"Position {position_id!r} not found")
        intent = session.get(V2ExecutionIntent, exit_intent_id)
        if intent is None:
            raise ValueError(f"Exit intent {exit_intent_id!r} not found before exchange result")
        order = session.scalar(select(V2ExchangeOrder).where(V2ExchangeOrder.client_order_id == result.client_order_id))
        if order is None:
            raise ValueError(f"Exit order {result.client_order_id!r} not found before exchange result")
        current = V2IntentState(intent.state)

        if result.status is ExitExecutionStatus.UNKNOWN:
            if current is V2IntentState.EXCHANGE_SUBMITTING:
                repo.transition_intent(
                    exit_intent_id,
                    expected_current=current,
                    next_state=V2IntentState.EXCHANGE_UNKNOWN,
                    event_type="ExitOutcomeUnknown",
                    payload={"client_order_id": result.client_order_id},
                )
        elif result.status in {
            ExitExecutionStatus.FAILED,
            ExitExecutionStatus.NOT_ATTEMPTED,
        }:
            if current in {
                V2IntentState.EXCHANGE_SUBMITTING,
                V2IntentState.EXCHANGE_UNKNOWN,
            }:
                next_state = (
                    V2IntentState.REJECTED if result.status is ExitExecutionStatus.FAILED else V2IntentState.CANCELLED
                )
                repo.transition_intent(
                    exit_intent_id,
                    expected_current=current,
                    next_state=next_state,
                    event_type="ExitSubmissionFailed",
                    payload={"detail": result.detail},
                )
        elif result.exchange_order_id:
            repo.save_exchange_order_receipt(
                order_record_id=order.order_record_id,
                exchange_order_id=str(result.exchange_order_id),
                acknowledged_at=result.fill_timestamp or datetime.now(UTC),
            )
            if current in {
                V2IntentState.EXCHANGE_SUBMITTING,
                V2IntentState.EXCHANGE_UNKNOWN,
            }:
                repo.transition_intent(
                    exit_intent_id,
                    expected_current=current,
                    next_state=V2IntentState.EXCHANGE_ACKNOWLEDGED,
                    event_type="ExitAcknowledged",
                    payload={"exchange_order_id": result.exchange_order_id},
                )
            position_state = V2PositionState(position.state)
            if position_state in {
                V2PositionState.POSITION_PROJECTED,
                V2PositionState.PROTECTED,
            }:
                repo.transition_position(
                    position_id,
                    expected_current=position_state,
                    next_state=V2PositionState.REDUCING,
                    event_type="PositionReducing",
                    payload={
                        "exit_intent_id": exit_intent_id,
                        "exchange_order_id": result.exchange_order_id,
                    },
                    occurred_at=result.fill_timestamp or datetime.now(UTC),
                )
        repo.commit()


def reconcile_closed_position_protections(
    *,
    execution_mode: V2ExecutionMode,
    symbol: str,
    exchange_open_order_ids: frozenset[str],
    observed_at: datetime,
) -> int:
    """Close stale local protection projections after exchange-flat truth.

    Only protections belonging to an already-CLOSED managed position are
    touched, and only when none of their exchange order ids remains open.
    """
    cancellable_states = {
        V2ProtectionState.PROTECTION_INTENT,
        V2ProtectionState.PROTECTION_SUBMITTING,
        V2ProtectionState.PROTECTION_ACTIVE,
        V2ProtectionState.PROTECTION_FAILED,
        V2ProtectionState.PROTECTION_UNKNOWN,
    }
    with get_session_factory()() as session:
        repo = AutomatedTradingRepository(session)
        protections = tuple(
            session.scalars(
                select(V2ProtectionRecord)
                .join(
                    V2ManagedPosition,
                    V2ManagedPosition.position_id == V2ProtectionRecord.position_id,
                )
                .where(
                    V2ManagedPosition.execution_mode == execution_mode.value,
                    V2ManagedPosition.symbol == symbol,
                    V2ManagedPosition.state == V2PositionState.CLOSED.value,
                    V2ProtectionRecord.state.in_([state.value for state in cancellable_states]),
                )
            )
        )
        changed = 0
        for protection in protections:
            protection_exchange_ids = {
                order_id
                for order_id in (
                    protection.stop_exchange_order_id,
                    protection.tp_exchange_order_id,
                )
                if order_id
            }
            if protection_exchange_ids & exchange_open_order_ids:
                continue
            current = V2ProtectionState(protection.state)
            repo.transition_protection(
                protection.protection_id,
                expected_current=current,
                next_state=V2ProtectionState.PROTECTION_CANCELLED,
                event_type="ProtectionReconciledCancelledAfterPositionClosed",
                payload={
                    "exchange_open_order_ids": sorted(exchange_open_order_ids),
                    "position_state": V2PositionState.CLOSED.value,
                },
                occurred_at=observed_at,
            )
            changed += 1
        if changed:
            repo.commit()
        return changed


def persist_exit_result(
    *,
    cycle_id: str,
    position_id: str,
    execution_mode: V2ExecutionMode,
    reason: ExitReason,
    result: ExitExecutionResult,
    fencing_token: str,
    protection_exchange_order_id: str | None = None,
) -> None:
    """Persist a confirmed reduce-only exit and project exchange-flat locally."""
    if result.status not in {
        ExitExecutionStatus.CLOSED,
        ExitExecutionStatus.PARTIALLY_REDUCED,
    }:
        return
    if (
        not result.exchange_order_id
        or not result.trade_ids
        or result.reduced_quantity <= 0
        or result.average_fill_price is None
        or result.fill_timestamp is None
    ):
        raise ValueError("confirmed exit persistence requires order id, trade ids, quantity, price, and timestamp")

    persist_exit_intent_before_submission(
        cycle_id=cycle_id,
        position_id=position_id,
        execution_mode=execution_mode,
        reason=reason,
        client_order_id=result.client_order_id,
        requested_quantity=result.reduced_quantity,
        fencing_token=fencing_token,
        submitted_at=result.fill_timestamp,
    )
    persist_exit_submission_result(position_id=position_id, result=result)

    with get_session_factory()() as session:
        repo = AutomatedTradingRepository(session)
        position = session.get(V2ManagedPosition, position_id)
        if position is None:
            raise ValueError(f"Position {position_id!r} not found")
        entry_intent = session.get(V2ExecutionIntent, position.intent_id)
        if entry_intent is None:
            raise ValueError(f"Entry intent {position.intent_id!r} not found")
        entry_order = session.get(V2ExchangeOrder, position.order_record_id)
        if entry_order is None:
            raise ValueError(f"Entry order {position.order_record_id!r} not found")

        exit_intent_id = _exit_intent_id(result.client_order_id)
        exit_order = session.scalar(
            select(V2ExchangeOrder).where(V2ExchangeOrder.client_order_id == result.client_order_id)
        )
        if exit_order is None:
            raise ValueError(f"Exit order {result.client_order_id!r} disappeared before fill persistence")
        exit_order_id = exit_order.order_record_id
        n = len(result.trade_ids)
        per_qty = (result.reduced_quantity / n).quantize(Decimal("0.00000001"))
        allocated = Decimal("0")
        for index, trade_id in enumerate(result.trade_ids):
            quantity = result.reduced_quantity - allocated if index == n - 1 else per_qty
            allocated += quantity
            existing_fill = repo.get_exchange_fill_by_trade(
                account_id="binance_testnet",
                trade_id=str(trade_id),
            )
            if existing_fill is not None:
                if (
                    existing_fill.intent_id != exit_intent_id
                    or existing_fill.exchange_order_record_id != exit_order_id
                    or existing_fill.exchange_order_id != result.exchange_order_id
                ):
                    raise ValueError(
                        "exchange fill identity is already attributed to a different V2 order: "
                        f"account_id='binance_testnet' trade_id={trade_id!r}"
                    )
                continue
            repo.save_exchange_fill_receipt(
                intent_id=exit_intent_id,
                exchange_order_record_id=exit_order_id,
                account_id="binance_testnet",
                exchange_order_id=result.exchange_order_id,
                trade_id=str(trade_id),
                symbol=position.symbol,
                side="SELL" if position.direction == "long" else "BUY",
                reduce_only=True,
                filled_quantity=quantity,
                fill_price=result.average_fill_price,
                commission=result.total_fee / n,
                commission_asset="USDT",
                exchange_event_time=result.fill_timestamp,
                received_at=datetime.now(UTC),
                raw_hash=f"{result.exchange_order_id}:{trade_id}",
            )
        repo.transition_intent(
            exit_intent_id,
            expected_current=V2IntentState.EXCHANGE_ACKNOWLEDGED,
            next_state=V2IntentState.FILLED,
            event_type="ExitFilled",
            payload={
                "position_id": position_id,
                "trade_ids": list(result.trade_ids),
                "reduce_only": True,
            },
        )

        current_state = V2PositionState(position.state)
        if current_state is V2PositionState.QUARANTINED:
            repo.repair_quarantined_position_from_confirmed_exit(
                position_id=position_id,
                exit_order_record_id=exit_order_id,
                occurred_at=result.fill_timestamp,
                payload={
                    "exit_intent_id": exit_intent_id,
                    "reason": reason.value,
                    "repair": "exact_exchange_exit_fill",
                },
            )
            current_state = V2PositionState.CLOSED
        elif current_state is not V2PositionState.REDUCING:
            repo.transition_position(
                position_id,
                expected_current=current_state,
                next_state=V2PositionState.REDUCING,
                event_type="PositionReducing",
                payload={
                    "exit_intent_id": exit_intent_id,
                    "exchange_order_id": result.exchange_order_id,
                    "reason": reason.value,
                },
                occurred_at=result.fill_timestamp,
            )

        if result.status is ExitExecutionStatus.CLOSED:
            if current_state is not V2PositionState.CLOSED:
                repo.transition_position(
                    position_id,
                    expected_current=V2PositionState.REDUCING,
                    next_state=V2PositionState.CLOSED,
                    event_type="PositionClosed",
                    payload={
                        "exit_intent_id": exit_intent_id,
                        "exchange_order_id": result.exchange_order_id,
                        "trade_ids": list(result.trade_ids),
                        "reason": reason.value,
                    },
                    occurred_at=result.fill_timestamp,
                )
            gross_pnl = (
                (result.average_fill_price - position.entry_price) * result.reduced_quantity
                if position.direction == "long"
                else (position.entry_price - result.average_fill_price) * result.reduced_quantity
            )
            position.realized_pnl = gross_pnl - position.entry_fee - result.total_fee
            if entry_intent.decision_id:
                snapshot = session.scalar(
                    select(V2DecisionSnapshot).where(V2DecisionSnapshot.decision_id == entry_intent.decision_id)
                )
                if snapshot is not None:
                    outcome = build_shadow_outcome(
                        dict(snapshot.payload),
                        direction=position.direction,
                        entry_price=position.entry_price,
                        exit_price=result.average_fill_price,
                        quantity=result.reduced_quantity,
                        commission=position.entry_fee + result.total_fee,
                        exit_reason=reason.value,
                    )
                    for shadow in repo.list_shadow_records(snapshot_id=snapshot.snapshot_id):
                        repo.append_shadow_outcome(
                            shadow_id=shadow.shadow_id,
                            payload={**outcome, "variant": shadow.variant},
                        )
        elif result.remaining_quantity is not None and result.remaining_quantity > 0:
            position.quantity = result.remaining_quantity

        protections = tuple(
            session.scalars(select(V2ProtectionRecord).where(V2ProtectionRecord.position_id == position_id))
        )
        for protection in protections:
            matched_trigger = (protection_exchange_order_id or result.exchange_order_id) in {
                protection.stop_exchange_order_id,
                protection.tp_exchange_order_id,
            }
            current = V2ProtectionState(protection.state)
            if matched_trigger and current is V2ProtectionState.PROTECTION_ACTIVE:
                repo.transition_protection(
                    protection.protection_id,
                    expected_current=current,
                    next_state=V2ProtectionState.PROTECTION_TRIGGERED,
                    event_type="ProtectionTriggered",
                    payload={
                        "exchange_order_id": result.exchange_order_id,
                        "protection_exchange_order_id": protection_exchange_order_id,
                    },
                    occurred_at=result.fill_timestamp,
                )
                current = V2ProtectionState.PROTECTION_TRIGGERED
            if matched_trigger and current is V2ProtectionState.PROTECTION_TRIGGERED:
                repo.transition_protection(
                    protection.protection_id,
                    expected_current=current,
                    next_state=V2ProtectionState.PROTECTION_FILLED,
                    event_type="ProtectionFilled",
                    payload={"trade_ids": list(result.trade_ids)},
                    occurred_at=result.fill_timestamp,
                )
            elif result.status is ExitExecutionStatus.CLOSED and current in {
                V2ProtectionState.PROTECTION_INTENT,
                V2ProtectionState.PROTECTION_SUBMITTING,
                V2ProtectionState.PROTECTION_ACTIVE,
                V2ProtectionState.PROTECTION_FAILED,
                V2ProtectionState.PROTECTION_UNKNOWN,
            }:
                repo.transition_protection(
                    protection.protection_id,
                    expected_current=current,
                    next_state=V2ProtectionState.PROTECTION_CANCELLED,
                    event_type="ProtectionCancelledAfterConfirmedExit",
                    payload={
                        "exit_exchange_order_id": result.exchange_order_id,
                        "position_state": V2PositionState.CLOSED.value,
                    },
                    occurred_at=result.fill_timestamp,
                )

        repo.commit()
        logger.info(
            "persisted reduce-only exit cycle=%s position=%s order=%s trades=%s",
            cycle_id,
            position_id,
            result.exchange_order_id,
            list(result.trade_ids),
        )
