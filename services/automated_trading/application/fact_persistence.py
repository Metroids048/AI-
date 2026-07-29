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

from services.automated_trading.domain.enums import (
    V2CandidateType,
    V2ExecutionMode,
    V2IntentState,
    V2PositionState,
    V2ProtectionState,
)
from services.automated_trading.domain.receipts import ProtectionReceipt
from services.automated_trading.infrastructure.models import V2ExecutionCycle, V2ExecutionDecision
from services.automated_trading.infrastructure.repository import AutomatedTradingRepository
from services.database import get_session_factory

logger = logging.getLogger(__name__)


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
) -> None:
    """Write intent → order → fills → position → protection after a real fill."""
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
        )
        for expected, nxt, event in (
            (V2IntentState.INTENT_CREATED, V2IntentState.EXCHANGE_SUBMITTING, "Submitting"),
            (V2IntentState.EXCHANGE_SUBMITTING, V2IntentState.EXCHANGE_ACKNOWLEDGED, "Acked"),
            (V2IntentState.EXCHANGE_ACKNOWLEDGED, V2IntentState.FILLED, "Filled"),
        ):
            repo.transition_intent(
                intent_id=intent_id,
                expected_current=expected,
                next_state=nxt,
                event_type=event,
                payload={},
            )

        order_id = repo.save_order_submission(
            intent_id=intent_id,
            client_order_id=entry_result.client_order_id,
            quantity=float(entry_result.filled_quantity),
            leverage=leverage,
            submitted_at=entry_result.fill_timestamp or datetime.now(UTC),
        )
        repo.save_exchange_order_receipt(
            order_record_id=order_id,
            exchange_order_id=str(entry_result.exchange_order_id),
            acknowledged_at=entry_result.fill_timestamp or datetime.now(UTC),
        )
        n = max(len(entry_result.trade_ids), 1)
        per_qty = (entry_result.filled_quantity / n).quantize(Decimal("0.00000001"))
        allocated = Decimal("0")
        for idx, trade_id in enumerate(entry_result.trade_ids):
            qty = entry_result.filled_quantity - allocated if idx == n - 1 else per_qty
            allocated += qty
            fee = (entry_result.total_fee / n) if n else entry_result.total_fee
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

        repo.project_position_from_confirmed_fills(
            position_id=position_id,
            intent_id=intent_id,
            order_record_id=order_id,
            symbol=symbol,
            direction=direction,
            execution_mode=execution_mode,
            projected_at=datetime.now(UTC),
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
