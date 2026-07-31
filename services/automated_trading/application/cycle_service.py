"""V2 automated trading cycle service.

Orchestrates one (symbol, timeframe) evaluation cycle:
  reconcile → decision funnel → entry gate → submit entry →
  project position → submit protection → exit open positions

One cycle = one closed bar evaluation. The scheduler calls
``run_automated_trading_cycle`` for each symbol on each closed bar.
The result carries the funnel record so callers can persist it.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from services.automated_trading.application.decision_service import (
    DecisionContext,
    TimeframeView,
    evaluate_symbol,
)
from services.automated_trading.application.entry_service import (
    EntryExecutionResult,
    EntryExecutionStatus,
    EntryRuntimeContext,
    evaluate_entry,
    execute_entry,
)
from services.automated_trading.application.exit_service import (
    ExitExecutionResult,
    ExitExecutionStatus,
    ExitReason,
    ExitVerdict,
    evaluate_exit,
    execute_reduce_only_exit,
)
from services.automated_trading.application.fact_persistence import (
    persist_entry_and_protection,
    persist_entry_intent_before_submission,
    persist_entry_submission_result,
    persist_exit_intent_before_submission,
    persist_exit_result,
    persist_exit_submission_result,
    reconcile_closed_position_protections,
)
from services.automated_trading.application.protection_service import (
    build_protection_plan,
    ensure_protection,
)
from services.automated_trading.application.reconciliation_service import (
    LocalIntentView,
    LocalPositionView,
    LocalStateView,
    ReconciliationStatus,
    reconcile,
)
from services.automated_trading.application.recovery_executor import execute_recovery_actions
from services.automated_trading.application.recovery_service import (
    PendingIntentView,
    recover_pending_state,
)
from services.automated_trading.domain.candidates import CandidateLane, TradeCandidate
from services.automated_trading.domain.client_order_id import (
    entry_client_order_id,
    exit_client_order_id,
    is_v2_client_order_id,
)
from services.automated_trading.domain.enums import (
    V2CandidateType,
    V2ExecutionMode,
    V2IntentState,
    V2PositionState,
)
from services.automated_trading.infrastructure.runtime_lock import EngineActivation
from services.automated_trading.observability.decision_funnel import DecisionReasonCode

if TYPE_CHECKING:
    from services.automated_trading.infrastructure.binance_adapter import BinanceTestnetAdapter
    from services.automated_trading.infrastructure.market_snapshot_provider import (
        AuthoritativeAccountSnapshot,
    )

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CycleRequest:
    """Everything needed to run one automated trading cycle."""

    cycle_id: str
    symbol: str
    timeframe: str
    entry_timeframe: TimeframeView
    execution_mode: V2ExecutionMode
    engine_activation: EngineActivation
    fencing_token: str
    now: datetime
    risk_per_trade: Decimal = Decimal("0.01")
    max_leverage: int = 10
    account_equity: Decimal = Decimal("10000")
    open_position_symbols: frozenset[str] = frozenset()
    already_evaluated_bars: frozenset[datetime] = frozenset()
    entry_kill_switch_active: bool = False
    daily_trade_limit_reached: bool = False
    symbol_cooldown_active: bool = False
    authoritative_snapshot: AuthoritativeAccountSnapshot | None = None
    # When set, cycle may submit a reduce-only exit for this reason (no blind TIME_EXIT).
    forced_exit_reason: ExitReason | None = None
    # Persist Intent/Order/Fill/Position when True (scheduler / armed runs).
    persist_facts: bool = False
    decision_id: str | None = None


@dataclass(frozen=True)
class LocalStateLoadResult:
    """Fail-closed local projection load (Gate 2)."""

    status: str  # OK | UNAVAILABLE
    state: LocalStateView | None
    error_code: str | None = None
    exchange_position_claim_refs: dict[str, frozenset[str]] = field(default_factory=dict)


@dataclass
class CycleResult:
    """Mutable result accumulated during one cycle."""

    cycle_id: str
    symbol: str
    entry_submitted: bool = False
    position_projected: bool = False
    protection_active: bool = False
    exit_submitted: bool = False
    reconciliation_status: ReconciliationStatus = ReconciliationStatus.UNAVAILABLE
    funnel_payload: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    entry_blocked_by_runtime_control: bool = False

    def record_error(self, msg: str) -> None:
        logger.error("[cycle %s %s] %s", self.cycle_id, self.symbol, msg)
        self.errors.append(msg)


def _build_local_state(
    adapter,
    execution_mode: V2ExecutionMode,
    *,
    fail_closed: bool,
) -> LocalStateLoadResult:
    """Read open V2 positions for reconciliation.

    When ``fail_closed`` is True (armed scheduler path), DB errors block entry.
    Unit / shadow rehearsal paths pass fail_closed=False and fall back to empty.
    """
    try:
        from sqlalchemy import select

        from services.automated_trading.infrastructure.models import (
            V2ExchangeFill,
            V2ExchangeOrder,
            V2ExecutionIntent,
            V2ProtectionRecord,
        )
        from services.automated_trading.infrastructure.repository import (
            AutomatedTradingRepository,
        )
        from services.database import get_session_factory

        with get_session_factory()() as session:
            repo = AutomatedTradingRepository(session)
            open_positions = repo.get_open_positions(execution_mode)
            positions: list[LocalPositionView] = []
            exchange_position_claim_refs: dict[str, frozenset[str]] = {}
            known_client_order_ids: set[str] = set()

            for position in open_positions:
                order = session.get(V2ExchangeOrder, position.order_record_id)
                fills = tuple(
                    session.scalars(
                        select(V2ExchangeFill).where(
                            V2ExchangeFill.exchange_order_record_id == position.order_record_id
                        )
                    )
                )
                protections = tuple(
                    session.scalars(
                        select(V2ProtectionRecord).where(V2ProtectionRecord.position_id == position.position_id)
                    )
                )
                active_protections = tuple(
                    protection for protection in protections if protection.state == "PROTECTION_ACTIVE"
                )

                claim_keys = {
                    position.position_id,
                    position.intent_id,
                    position.order_record_id,
                }
                if order is not None:
                    claim_keys.add(order.client_order_id)
                    known_client_order_ids.add(order.client_order_id)
                    if order.exchange_order_id:
                        claim_keys.add(order.exchange_order_id)
                for fill in fills:
                    claim_keys.update(
                        {
                            fill.fill_id,
                            fill.exchange_order_id,
                            fill.trade_id,
                        }
                    )
                protection_exchange_order_ids: set[str] = set()
                protection_order_refs: list[tuple[str, str, str]] = []
                for protection in protections:
                    known_client_order_ids.add(protection.stop_client_order_id)
                    if protection.tp_client_order_id:
                        known_client_order_ids.add(protection.tp_client_order_id)
                    for exchange_order_id in (
                        protection.stop_exchange_order_id,
                        protection.tp_exchange_order_id,
                    ):
                        if exchange_order_id:
                            protection_exchange_order_ids.add(exchange_order_id)
                            claim_keys.add(exchange_order_id)
                    if protection.stop_exchange_order_id:
                        protection_order_refs.append(
                            (
                                protection.stop_exchange_order_id,
                                protection.stop_client_order_id,
                                ExitReason.HARD_STOP.value,
                            )
                        )
                    if protection.tp_exchange_order_id and protection.tp_client_order_id:
                        protection_order_refs.append(
                            (
                                protection.tp_exchange_order_id,
                                protection.tp_client_order_id,
                                ExitReason.TAKE_PROFIT.value,
                            )
                        )

                frozen_claim_keys = frozenset(claim_keys)
                positions.append(
                    LocalPositionView(
                        position_id=position.position_id,
                        symbol=position.symbol,
                        direction=position.direction,
                        quantity=Decimal(str(position.quantity)),
                        state=position.state,
                        claim_keys=frozen_claim_keys,
                        has_active_protection=bool(active_protections),
                        protection_exchange_order_ids=frozenset(protection_exchange_order_ids),
                        protection_order_refs=tuple(protection_order_refs),
                    )
                )
                # A Managed V2 position is created only from an acknowledged order
                # plus confirmed exchange fill rows. Those immutable receipts are
                # the persisted strategy identity used to claim the current net
                # position; symbol/quantity alone never create this mapping.
                if order is not None and order.exchange_order_id and fills:
                    exchange_position_claim_refs[f"{position.symbol}:{position.direction}"] = frozen_claim_keys

            all_orders = tuple(session.scalars(select(V2ExchangeOrder)))
            known_client_order_ids.update(order.client_order_id for order in all_orders)
            order_by_intent = {order.intent_id: order for order in all_orders}
            pending_intents = tuple(
                session.scalars(
                    select(V2ExecutionIntent).where(
                        V2ExecutionIntent.execution_mode == execution_mode.value,
                        V2ExecutionIntent.state.in_(
                            [
                                "EXCHANGE_SUBMITTING",
                                "EXCHANGE_UNKNOWN",
                                "EXCHANGE_ACKNOWLEDGED",
                            ]
                        ),
                    )
                )
            )
            intents = tuple(
                LocalIntentView(
                    intent_id=intent.intent_id,
                    symbol=intent.symbol,
                    client_order_id=order_by_intent[intent.intent_id].client_order_id,
                    state=intent.state,
                )
                for intent in pending_intents
                if intent.intent_id in order_by_intent
            )

        return LocalStateLoadResult(
            status="OK",
            state=LocalStateView(
                positions=tuple(positions),
                intents=intents,
                known_client_order_ids=frozenset(known_client_order_ids),
            ),
            exchange_position_claim_refs=exchange_position_claim_refs,
        )
    except Exception as exc:  # noqa: BLE001
        if fail_closed:
            logger.error("LOCAL_STATE_UNAVAILABLE: %s", exc)
            return LocalStateLoadResult(
                status="UNAVAILABLE",
                state=None,
                error_code="LOCAL_STATE_UNAVAILABLE",
            )
        logger.debug("local state load skipped (offline/unit): %s", exc)
        return LocalStateLoadResult(status="OK", state=LocalStateView())


def _runtime_entry_enabled() -> bool:
    """Read persisted entry kill switch; default disabled on any failure."""
    try:
        from services.automated_trading.infrastructure.repository import AutomatedTradingRepository
        from services.database import get_session_factory

        with get_session_factory()() as session:
            control = AutomatedTradingRepository(session).get_runtime_control("global")
            return bool(control.entry_enabled)
    except Exception as exc:  # noqa: BLE001
        logger.warning("runtime control read failed; treating entry as disabled: %s", exc)
        return False


def _append_funnel_stage(
    payload: dict[str, Any],
    *,
    stage: str,
    outcome: str,
    reason_code: str,
    metrics: dict[str, Any] | None = None,
    update_terminal: bool = True,
) -> None:
    payload.setdefault("stages", []).append(
        {
            "stage": stage,
            "outcome": outcome,
            "reason_code": reason_code,
            "detail": "",
            "metrics": {key: str(value) for key, value in (metrics or {}).items()},
            "recorded_at": datetime.now(UTC).isoformat(),
        }
    )
    if update_terminal:
        payload["terminal_stage"] = stage
        payload["terminal_outcome"] = outcome
        payload["reason_code"] = reason_code


def _record_trade_review_skip(request: CycleRequest, reason: str) -> None:
    from services.database import get_session_factory
    from services.strategy_library import LlmInvocationRepository
    from shared.models import LlmInvocation, LlmInvocationStage

    with get_session_factory()() as session:
        LlmInvocationRepository(session).create_invocation(
            LlmInvocation(
                cycle_id=request.cycle_id,
                decision_id=request.decision_id,
                symbol=request.symbol,
                called=False,
                skip_reason=reason,
                stage=LlmInvocationStage.TRADE_REVIEW,
                status="skipped",
            )
        )


def _run_trade_review(request: CycleRequest, candidate) -> dict[str, Any]:
    """Run the configured structured provider chain and persist its invocation."""
    from services.agents import AgentTaskService, build_configured_llm_runtime
    from services.database import get_session_factory
    from services.strategy_library import (
        AgentTaskRepository,
        ReviewRepository,
        StrategyRepository,
    )
    from shared.models import AgentTaskRequest

    payload = {
        "cycle_id": request.cycle_id,
        "decision_id": request.decision_id,
        "symbol": request.symbol,
        "timeframe": request.timeframe,
        "candidate": {
            "candidate_id": candidate.candidate_id,
            "strategy_id": candidate.strategy_id,
            "strategy_version": candidate.strategy_version,
            "lane": candidate.lane,
            "side": candidate.side,
            "confidence": str(candidate.confidence),
            "signal_reference_price": str(candidate.signal_reference_price),
            "stop_distance": str(candidate.stop_distance),
            "take_profit_distance": (
                str(candidate.take_profit_distance) if candidate.take_profit_distance is not None else None
            ),
        },
    }
    with get_session_factory()() as session:
        task = AgentTaskService(
            agent_repo=AgentTaskRepository(session),
            strategy_repo=StrategyRepository(session),
            review_repo=ReviewRepository(session),
            llm_runtime=build_configured_llm_runtime(),
        ).submit_task(
            AgentTaskRequest(
                agent_type="review_agent",
                task_type="trade_review_llm",
                input_ref=f"v2_candidate:{candidate.candidate_id}",
                input_payload=payload,
                priority=3,
            )
        )
    return {
        "status": task.schema_validation_status or task.task_status,
        "provider": task.provider_trace.get("provider"),
        "model": task.provider_trace.get("model"),
        "result": task.output_payload.get("trade_review"),
        "error": task.error_summary or task.output_payload.get("message"),
    }


def _fetch_step_size(adapter, symbol: str) -> Decimal:
    """Return market step size when available; fallback for offline/test paths."""
    default = Decimal("0.001")
    try:
        market = adapter.fetch_market_snapshot(symbol)
        return market.step_size
    except Exception as exc:  # noqa: BLE001
        logger.debug("market snapshot unavailable for step_size (%s); using %s", exc, default)
        return default


def _calculate_quantity(request: CycleRequest, snapshot: AuthoritativeAccountSnapshot) -> Decimal:
    """Risk-based position sizing: risk_per_trade * equity / entry_price."""
    equity = max(snapshot.equity, Decimal("1"))
    notional = equity * request.risk_per_trade
    return notional


def _persist_reconciliation_fact(
    request: CycleRequest,
    snapshot: AuthoritativeAccountSnapshot,
    local_state: LocalStateView,
    reconciliation,
) -> None:
    """Persist the exchange/local comparison consumed by Runtime API and recovery."""
    from services.automated_trading.infrastructure.repository import AutomatedTradingRepository
    from services.database import get_session_factory

    quarantine_keys = set(reconciliation.quarantine_candidates)
    exchange_positions = {
        "positions": [
            {
                "symbol": position.symbol,
                "direction": position.direction,
                "quantity": str(position.quantity),
                "entry_price": str(position.entry_price),
                "mark_price": str(position.mark_price),
                "unrealized_pnl": str(position.unrealized_pnl),
                "leverage": position.leverage,
                "ownership": (
                    "EXTERNAL_QUARANTINED"
                    if f"{position.symbol}:{position.direction}" in quarantine_keys
                    else "MANAGED_V2"
                ),
            }
            for position in snapshot.positions
        ]
    }
    exchange_open_orders = {
        "open_orders": [
            {
                "exchange_order_id": order.exchange_order_id,
                "client_order_id": order.client_order_id,
                "symbol": order.symbol,
                "side": order.side,
                "order_type": order.order_type,
                "quantity": str(order.quantity),
                "price": str(order.price) if order.price is not None else None,
                "status": order.status,
                "reduce_only": order.reduce_only,
            }
            for order in snapshot.pending_orders
        ]
    }
    local_positions = {
        "positions": [
            {
                "position_id": position.position_id,
                "symbol": position.symbol,
                "direction": position.direction,
                "quantity": str(position.quantity),
                "state": position.state,
                "has_active_protection": position.has_active_protection,
            }
            for position in local_state.positions
        ]
    }
    discrepancies = {
        "mismatches": [
            {
                "code": discrepancy.code.value,
                "symbol": discrepancy.symbol,
                "detail": discrepancy.detail,
                "local_position_id": discrepancy.local_position_id,
                "exchange_ref": discrepancy.exchange_ref,
            }
            for discrepancy in reconciliation.discrepancies
        ],
        "entry_blocked_symbols": sorted(reconciliation.entry_blocked_symbols),
    }

    with get_session_factory()() as session:
        repo = AutomatedTradingRepository(session)
        repo.record_reconciliation(
            execution_mode=request.execution_mode,
            exchange_positions=exchange_positions,
            exchange_open_orders=exchange_open_orders,
            local_positions=local_positions,
            discrepancies=discrepancies,
            status=reconciliation.status.value,
            captured_at=reconciliation.reconciled_at or request.now,
            cycle_id=request.cycle_id,
        )
        if reconciliation.status is ReconciliationStatus.HEALTHY:
            repo.resolve_reconciliation_incidents(resolved_at=reconciliation.reconciled_at or request.now)
        repo.commit()


def _project_confirmed_protection_exits(
    request: CycleRequest,
    adapter,
    snapshot: AuthoritativeAccountSnapshot,
    local_state: LocalStateView,
    result: CycleResult,
) -> bool:
    """Project a known V2 stop/TP fill before ghost-position recovery runs."""
    from sqlalchemy import select

    from services.automated_trading.infrastructure.models import (
        V2ManagedPosition,
        V2ProtectionRecord,
    )
    from services.database import get_session_factory

    changed = False
    exchange_position_keys = {f"{position.symbol}:{position.direction}" for position in snapshot.positions}
    positions = list(local_state.positions)
    known_position_ids = {position.position_id for position in positions}
    with get_session_factory()() as session:
        quarantined_rows = tuple(
            session.execute(
                select(V2ManagedPosition, V2ProtectionRecord)
                .join(
                    V2ProtectionRecord,
                    V2ProtectionRecord.position_id == V2ManagedPosition.position_id,
                )
                .where(
                    V2ManagedPosition.execution_mode == request.execution_mode.value,
                    V2ManagedPosition.state == V2PositionState.QUARANTINED.value,
                    V2ProtectionRecord.state.in_(
                        [
                            "PROTECTION_ACTIVE",
                            "PROTECTION_TRIGGERED",
                            "PROTECTION_UNKNOWN",
                        ]
                    ),
                )
            )
        )
    for persisted_position, protection in quarantined_rows:
        if persisted_position.position_id in known_position_ids:
            continue
        protection_order_refs: list[tuple[str, str, str]] = []
        protection_exchange_order_ids: set[str] = set()
        if protection.stop_exchange_order_id:
            protection_order_refs.append(
                (
                    protection.stop_exchange_order_id,
                    protection.stop_client_order_id,
                    ExitReason.HARD_STOP.value,
                )
            )
            protection_exchange_order_ids.add(protection.stop_exchange_order_id)
        if protection.tp_exchange_order_id and protection.tp_client_order_id:
            protection_order_refs.append(
                (
                    protection.tp_exchange_order_id,
                    protection.tp_client_order_id,
                    ExitReason.TAKE_PROFIT.value,
                )
            )
            protection_exchange_order_ids.add(protection.tp_exchange_order_id)
        if not protection_order_refs:
            continue
        positions.append(
            LocalPositionView(
                position_id=persisted_position.position_id,
                symbol=persisted_position.symbol,
                direction=persisted_position.direction,
                quantity=Decimal(str(persisted_position.quantity)),
                state=persisted_position.state,
                claim_keys=frozenset(),
                has_active_protection=True,
                protection_exchange_order_ids=frozenset(protection_exchange_order_ids),
                protection_order_refs=tuple(protection_order_refs),
            )
        )
        known_position_ids.add(persisted_position.position_id)

    for position in positions:
        if position.state == "CLOSED":
            continue
        if (
            position.state != V2PositionState.QUARANTINED.value
            and f"{position.symbol}:{position.direction}" in exchange_position_keys
        ):
            continue
        confirmed: tuple[str, str, ExitReason, tuple[Any, ...]] | None = None
        for exchange_order_id, client_order_id, reason_value in position.protection_order_refs:
            try:
                fills = tuple(adapter.fetch_fills(position.symbol, exchange_order_id))
            except Exception as exc:  # noqa: BLE001
                result.record_error(
                    f"protection fill lookup failed for {position.position_id}/{exchange_order_id}: {exc}"
                )
                return changed
            if fills:
                filled_quantity = sum(
                    (fill.filled_quantity for fill in fills),
                    Decimal("0"),
                )
                if filled_quantity < position.quantity:
                    continue
                confirmed = (
                    exchange_order_id,
                    client_order_id,
                    ExitReason(reason_value),
                    fills,
                )
                break
        if confirmed is None:
            continue

        protection_exchange_order_id, client_order_id, reason, fills = confirmed
        actual_exchange_order_ids = {fill.exchange_order_id for fill in fills}
        if len(actual_exchange_order_ids) != 1:
            result.record_error(f"protection fills span multiple exchange orders for {position.position_id}")
            continue
        actual_exchange_order_id = next(iter(actual_exchange_order_ids))
        reduced_quantity = sum((fill.filled_quantity for fill in fills), Decimal("0"))
        notional = sum(
            (fill.filled_quantity * fill.fill_price for fill in fills),
            Decimal("0"),
        )
        total_fee = sum((fill.fee for fill in fills), Decimal("0"))
        average_fill_price = notional / reduced_quantity if reduced_quantity > 0 else None
        fill_timestamp = max((fill.fill_timestamp for fill in fills), default=None)
        exit_result = ExitExecutionResult(
            status=ExitExecutionStatus.CLOSED,
            position_state=V2PositionState.CLOSED,
            client_order_id=client_order_id,
            exchange_order_id=actual_exchange_order_id,
            trade_ids=tuple(fill.trade_id for fill in fills),
            reduced_quantity=reduced_quantity,
            average_fill_price=average_fill_price,
            total_fee=total_fee,
            remaining_quantity=Decimal("0"),
            fill_timestamp=fill_timestamp,
            detail=f"exchange protection filled: {reason.value}",
        )
        try:
            persist_exit_result(
                cycle_id=request.cycle_id,
                position_id=position.position_id,
                execution_mode=request.execution_mode,
                reason=reason,
                result=exit_result,
                fencing_token=request.fencing_token,
                protection_exchange_order_id=protection_exchange_order_id,
            )
        except Exception as exc:  # noqa: BLE001
            result.record_error(f"protection exit persistence failed: {exc}")
            return changed

        for sibling_order_id in position.protection_exchange_order_ids - {protection_exchange_order_id}:
            try:
                adapter.cancel_order(position.symbol, sibling_order_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "residual protection cancellation failed for %s/%s: %s",
                    position.position_id,
                    sibling_order_id,
                    exc,
                )
        result.exit_submitted = True
        changed = True
    return changed


def _recover_confirmed_v2_entry_gap(
    request: CycleRequest,
    adapter,
    snapshot: AuthoritativeAccountSnapshot,
    result: CycleResult,
) -> bool:
    """Recover a confirmed V2 fill lost in the submit→fill persistence race.

    Recovery requires exact identity from a persisted V2 decision: intent-derived
    client order id, exchange order id, and exchange trade receipts. Symbol or
    quantity proximity alone is never sufficient.
    """
    if not request.persist_facts:
        return False

    from sqlalchemy import select

    from services.automated_trading.infrastructure.models import (
        V2ExecutionCycle,
        V2ExecutionDecision,
        V2ManagedPosition,
    )
    from services.database import get_session_factory

    with get_session_factory()() as session:
        rows = tuple(
            session.execute(
                select(V2ExecutionDecision, V2ExecutionCycle)
                .join(
                    V2ExecutionCycle,
                    V2ExecutionDecision.cycle_id == V2ExecutionCycle.cycle_id,
                )
                .where(
                    V2ExecutionCycle.symbol == request.symbol,
                    V2ExecutionCycle.execution_mode == request.execution_mode.value,
                    V2ExecutionDecision.terminal_reason == "OK",
                )
                .order_by(V2ExecutionDecision.created_at.desc())
                .limit(20)
            ).all()
        )

    for decision, decision_cycle in rows:
        payload = decision.payload or {}
        stages = {str(item.get("stage")): item for item in payload.get("stages", []) if isinstance(item, dict)}
        intent_metrics = (stages.get("INTENT_CREATED") or {}).get("metrics") or {}
        submit_metrics = (stages.get("EXCHANGE_SUBMITTED") or {}).get("metrics") or {}
        candidate_metrics = (stages.get("CANDIDATE_CREATED") or {}).get("metrics") or {}
        signal_metrics = (stages.get("ENTRY_SIGNAL_EVALUATED") or {}).get("metrics") or {}
        drift_metrics = (stages.get("PRICE_DRIFT_APPROVED") or {}).get("metrics") or {}

        intent_id = str(intent_metrics.get("intent_id") or "")
        client_order_id = str(submit_metrics.get("client_order_id") or "")
        exchange_order_id = str(submit_metrics.get("exchange_order_id") or "")
        if not intent_id or not client_order_id or not exchange_order_id:
            continue
        if not is_v2_client_order_id(client_order_id):
            continue
        if entry_client_order_id(intent_id) != client_order_id:
            continue

        with get_session_factory()() as session:
            existing_position = session.scalar(
                select(V2ManagedPosition).where(V2ManagedPosition.intent_id == intent_id)
            )
            if existing_position is not None:
                continue

        side = str(candidate_metrics.get("side") or signal_metrics.get("side") or "")
        direction = "long" if side == "LONG" else "short" if side == "SHORT" else ""
        exchange_position = next(
            (
                position
                for position in snapshot.positions
                if position.symbol == request.symbol and position.direction == direction
            ),
            None,
        )
        if exchange_position is None:
            continue

        fills = adapter.fetch_fills(request.symbol, exchange_order_id)
        if not fills:
            continue
        filled_quantity = sum((fill.filled_quantity for fill in fills), Decimal("0"))
        if filled_quantity <= 0 or filled_quantity != exchange_position.quantity:
            continue
        notional = sum(
            (fill.filled_quantity * fill.fill_price for fill in fills),
            Decimal("0"),
        )
        average_fill_price = notional / filled_quantity
        total_fee = sum((fill.fee for fill in fills), Decimal("0"))
        fill_timestamp = max(fill.fill_timestamp for fill in fills)

        bar_timestamp = datetime.fromisoformat(str(payload["bar_timestamp"]).replace("Z", "+00:00"))
        if bar_timestamp.tzinfo is None:
            bar_timestamp = bar_timestamp.replace(tzinfo=UTC)
        candidate = TradeCandidate(
            candidate_id=str(payload.get("candidate_id") or candidate_metrics.get("candidate_id")),
            cycle_id=decision_cycle.cycle_id,
            strategy_id=str(payload.get("strategy_id") or "testnet_sampling_v2"),
            strategy_version=str(payload.get("strategy_version") or "1.0.0"),
            lane=str(payload.get("lane") or CandidateLane.TESTNET_SAMPLING.value),
            candidate_type=V2CandidateType.SAMPLING,
            symbol=request.symbol,
            side=side,
            signal_candle_close_time=bar_timestamp,
            signal_reference_price=Decimal(str(signal_metrics["close"])),
            confidence=Decimal("0.5"),
            stop_distance=Decimal(str(candidate_metrics["stop_distance"])),
            take_profit_distance=(
                Decimal(str(candidate_metrics["take_profit_distance"]))
                if candidate_metrics.get("take_profit_distance") is not None
                else None
            ),
            max_entry_drift_bps=Decimal(str(drift_metrics.get("drift_ceiling_bps") or "20")),
            expires_at=bar_timestamp + timedelta(days=1),
            non_promotable=True,
        )
        entry_result = EntryExecutionResult(
            status=EntryExecutionStatus.FILLED,
            intent_state=V2IntentState.FILLED,
            client_order_id=client_order_id,
            reason_code=DecisionReasonCode.OK,
            exchange_order_id=exchange_order_id,
            trade_ids=tuple(fill.trade_id for fill in fills),
            requested_quantity=filled_quantity,
            filled_quantity=filled_quantity,
            average_fill_price=average_fill_price,
            total_fee=total_fee,
            fill_timestamp=fill_timestamp,
            detail="recovered from exact persisted V2 order identity and Binance fills",
        )
        market = adapter.fetch_market_snapshot(request.symbol)
        position_id = str(uuid.uuid4())
        plan = build_protection_plan(
            position_id=position_id,
            candidate=candidate,
            average_fill_price=average_fill_price,
            filled_quantity=filled_quantity,
            tick_size=market.tick_size,
        )
        protection = ensure_protection(plan, adapter=adapter)
        if not protection.is_active:
            result.record_error(f"confirmed V2 entry recovery could not activate protection: {protection.detail}")
            return False

        persist_entry_and_protection(
            cycle_id=decision_cycle.cycle_id,
            decision_id=decision.decision_id,
            intent_id=intent_id,
            symbol=request.symbol,
            direction=direction,
            candidate_key=candidate.strategy_id,
            candidate_type=V2CandidateType.SAMPLING,
            execution_mode=request.execution_mode,
            decision_bar_timestamp=bar_timestamp,
            fencing_token=request.fencing_token,
            leverage=request.max_leverage,
            entry_result=entry_result,
            position_id=position_id,
            protection_result=protection,
            stop_loss_price=plan.stop_price,
            take_profit_price=plan.take_profit_price,
            stop_client_order_id=plan.stop_client_order_id,
            tp_client_order_id=plan.tp_client_order_id,
        )
        result.position_projected = True
        result.protection_active = True
        logger.warning(
            "recovered confirmed V2 entry gap cycle=%s intent=%s order=%s trades=%s",
            decision_cycle.cycle_id,
            intent_id,
            exchange_order_id,
            [fill.trade_id for fill in fills],
        )
        return True
    return False


def _recover_confirmed_v2_exit_gaps(
    request: CycleRequest,
    adapter,
    snapshot: AuthoritativeAccountSnapshot,
    result: CycleResult,
) -> bool:
    """Project delayed reduce-only fills by exact V2 order identity.

    This runs before ordinary ghost-position recovery. It never infers a close
    from exchange-flat state alone: the deterministic A2X client order id, an
    exchange order id, and real Binance fills covering the local quantity are
    all mandatory.
    """
    if not request.persist_facts:
        return False

    from sqlalchemy import select

    from services.automated_trading.infrastructure.models import (
        V2ExchangeOrder,
        V2ExecutionIntent,
        V2ManagedPosition,
    )
    from services.database import get_session_factory

    exchange_position_keys = {f"{position.symbol}:{position.direction}" for position in snapshot.positions}
    with get_session_factory()() as session:
        positions = tuple(
            session.scalars(
                select(V2ManagedPosition)
                .where(
                    V2ManagedPosition.symbol == request.symbol,
                    V2ManagedPosition.execution_mode == request.execution_mode.value,
                    V2ManagedPosition.state.in_(["REDUCING", "QUARANTINED"]),
                )
                .order_by(V2ManagedPosition.projected_at.desc())
                .limit(20)
            )
        )
        persisted_attempts: dict[
            str,
            tuple[tuple[str, str | None, ExitReason], ...],
        ] = {}
        for position in positions:
            rows = tuple(
                session.execute(
                    select(V2ExecutionIntent, V2ExchangeOrder)
                    .join(
                        V2ExchangeOrder,
                        V2ExchangeOrder.intent_id == V2ExecutionIntent.intent_id,
                    )
                    .where(V2ExecutionIntent.candidate_key.like(f"exit:{position.position_id}:%"))
                    .order_by(V2ExchangeOrder.created_at.desc())
                ).all()
            )
            refs: list[tuple[str, str | None, ExitReason]] = []
            for intent, order in rows:
                reason_value = intent.candidate_key.rsplit(":", 1)[-1]
                try:
                    reason = ExitReason(reason_value)
                except ValueError:
                    reason = ExitReason.MANUAL_REDUCE_ONLY
                refs.append(
                    (
                        order.client_order_id,
                        order.exchange_order_id,
                        reason,
                    )
                )
            persisted_attempts[position.position_id] = tuple(refs)

    changed = False
    for position in positions:
        if f"{position.symbol}:{position.direction}" in exchange_position_keys:
            continue
        refs = list(persisted_attempts[position.position_id])
        known_client_ids = {client_order_id for client_order_id, _, _ in refs}
        for attempt in range(1, 4):
            client_order_id = exit_client_order_id(
                position.position_id,
                attempt=attempt,
            )
            if client_order_id in known_client_ids:
                continue
            reason = ExitReason.PROTECTION_FAILURE_EMERGENCY if attempt > 1 else ExitReason.MANUAL_REDUCE_ONLY
            refs.append((client_order_id, None, reason))

        expected_side = "sell" if position.direction == "long" else "buy"
        for client_order_id, known_exchange_order_id, reason in refs:
            exchange_order_id = known_exchange_order_id
            if not exchange_order_id:
                receipt = adapter.query_order_by_client_id(
                    position.symbol,
                    client_order_id,
                )
                if receipt is None:
                    continue
                if receipt.client_order_id != client_order_id:
                    continue
                if str(receipt.side).lower() != expected_side:
                    continue
                if receipt.quantity < position.quantity:
                    continue
                exchange_order_id = receipt.exchange_order_id

            fills = tuple(adapter.fetch_fills(position.symbol, exchange_order_id))
            if not fills:
                continue
            reduced_quantity = sum(
                (fill.filled_quantity for fill in fills),
                Decimal("0"),
            )
            if reduced_quantity < position.quantity:
                continue
            notional = sum(
                (fill.filled_quantity * fill.fill_price for fill in fills),
                Decimal("0"),
            )
            total_fee = sum((fill.fee for fill in fills), Decimal("0"))
            fill_timestamp = max(fill.fill_timestamp for fill in fills)
            exit_result = ExitExecutionResult(
                status=ExitExecutionStatus.CLOSED,
                position_state=V2PositionState.CLOSED,
                client_order_id=client_order_id,
                exchange_order_id=exchange_order_id,
                trade_ids=tuple(fill.trade_id for fill in fills),
                reduced_quantity=reduced_quantity,
                average_fill_price=notional / reduced_quantity,
                total_fee=total_fee,
                remaining_quantity=Decimal("0"),
                fill_timestamp=fill_timestamp,
                detail=("recovered delayed reduce-only exit from exact V2 client order identity and Binance fills"),
            )
            persist_exit_result(
                cycle_id=request.cycle_id,
                position_id=position.position_id,
                execution_mode=request.execution_mode,
                reason=reason,
                result=exit_result,
                fencing_token=request.fencing_token,
            )
            result.exit_submitted = True
            changed = True
            logger.warning(
                "recovered confirmed V2 exit gap cycle=%s position=%s "
                "client_order_id=%s exchange_order_id=%s trades=%s",
                request.cycle_id,
                position.position_id,
                client_order_id,
                exchange_order_id,
                [fill.trade_id for fill in fills],
            )
            break
    return changed


def run_automated_trading_cycle(request: CycleRequest, adapter: BinanceTestnetAdapter) -> CycleResult:
    """Run one V2 automated trading cycle.

    Safe in SHADOW mode: no orders submitted, full decision funnel still runs.
    """
    result = CycleResult(cycle_id=request.cycle_id, symbol=request.symbol)

    try:
        snapshot = request.authoritative_snapshot or adapter.fetch_authoritative_snapshot()
    except Exception as exc:  # noqa: BLE001
        result.record_error(f"snapshot fetch failed: {exc}")
        result.reconciliation_status = ReconciliationStatus.UNAVAILABLE
        return result

    if request.persist_facts:
        try:
            cleaned_protections = reconcile_closed_position_protections(
                execution_mode=request.execution_mode,
                symbol=request.symbol,
                exchange_open_order_ids=frozenset(order.exchange_order_id for order in snapshot.pending_orders),
                observed_at=snapshot.snapshot_timestamp,
            )
            if cleaned_protections:
                logger.warning(
                    "reconciled %s stale protection projection(s) for closed %s positions",
                    cleaned_protections,
                    request.symbol,
                )
        except Exception as exc:  # noqa: BLE001
            result.record_error(f"closed protection reconciliation failed: {exc}")

    try:
        _recover_confirmed_v2_exit_gaps(
            request,
            adapter,
            snapshot,
            result,
        )
    except Exception as exc:  # noqa: BLE001
        result.record_error(f"confirmed V2 exit recovery failed: {exc}")

    local_load = _build_local_state(
        adapter,
        request.execution_mode,
        fail_closed=request.persist_facts,
    )
    if request.persist_facts and local_load.status == "OK" and local_load.state is not None and snapshot.positions:
        try:
            recovered_entry = _recover_confirmed_v2_entry_gap(
                request,
                adapter,
                snapshot,
                result,
            )
        except Exception as exc:  # noqa: BLE001
            result.record_error(f"confirmed V2 entry recovery failed: {exc}")
            recovered_entry = False
        if recovered_entry:
            snapshot = adapter.fetch_authoritative_snapshot()
            local_load = _build_local_state(
                adapter,
                request.execution_mode,
                fail_closed=True,
            )
    if (
        request.persist_facts
        and local_load.status == "OK"
        and local_load.state is not None
        and _project_confirmed_protection_exits(
            request,
            adapter,
            snapshot,
            local_load.state,
            result,
        )
    ):
        try:
            snapshot = adapter.fetch_authoritative_snapshot()
        except Exception as exc:  # noqa: BLE001
            result.record_error(f"post-exit snapshot fetch failed: {exc}")
            result.reconciliation_status = ReconciliationStatus.UNAVAILABLE
            return result
        local_load = _build_local_state(
            adapter,
            request.execution_mode,
            fail_closed=True,
        )
    entry_blocked_local = local_load.status != "OK" or local_load.state is None
    local_state: LocalStateView
    if entry_blocked_local:
        result.record_error("LOCAL_STATE_UNAVAILABLE — entry blocked")
        result.reconciliation_status = ReconciliationStatus.UNAVAILABLE
        local_state = LocalStateView()
        recon = reconcile(snapshot, local_state, reconciled_at=request.now)
    else:
        assert local_load.state is not None
        local_state = local_load.state
        recon = reconcile(
            snapshot,
            local_state,
            reconciled_at=request.now,
            exchange_position_claim_refs=local_load.exchange_position_claim_refs,
        )
        result.reconciliation_status = recon.status
        if request.persist_facts:
            try:
                _persist_reconciliation_fact(request, snapshot, local_state, recon)
            except Exception as exc:  # noqa: BLE001
                result.record_error(f"reconciliation persistence failed: {exc}")
                result.reconciliation_status = ReconciliationStatus.UNAVAILABLE
                entry_blocked_local = True
        if recon.status is not ReconciliationStatus.HEALTHY:
            recovery = recover_pending_state(
                snapshot,
                recon,
                local_state=local_state,
                pending_intents=tuple(
                    PendingIntentView(
                        intent_id=intent.intent_id,
                        symbol=intent.symbol,
                        client_order_id=intent.client_order_id,
                        state=intent.state,
                    )
                    for intent in local_state.intents
                ),
            )
            if recovery.actions:
                step_size = _fetch_step_size(adapter, request.symbol)
                recovery_exec = execute_recovery_actions(
                    recovery.actions,
                    adapter=adapter,
                    snapshot=snapshot,
                    local_state=local_state,
                    step_size=step_size,
                )
                if recovery_exec.errors:
                    result.record_error(f"recovery execution errors: {recovery_exec.errors}")
            if recovery.entry_blocked:
                logger.info(
                    "[cycle %s %s] reconciliation=%s; entry blocked",
                    request.cycle_id,
                    request.symbol,
                    recon.status.value,
                )

    # Exits: only ALREADY_FLAT handling, forced_exit_reason, or emergency — never blind TIME_EXIT.
    step_size = _fetch_step_size(adapter, request.symbol)
    for pos in local_state.positions:
        if pos.symbol != request.symbol or pos.state in ("CLOSED", "QUARANTINED"):
            continue
        ex_pos = next(
            (p for p in snapshot.positions if p.symbol == request.symbol and p.direction == pos.direction),
            None,
        )
        already_flat = ex_pos is None or ex_pos.quantity <= 0
        if not already_flat and request.forced_exit_reason is None:
            continue
        reason = request.forced_exit_reason or ExitReason.HARD_STOP
        exit_decision = evaluate_exit(
            position_id=pos.position_id,
            symbol=pos.symbol,
            direction=pos.direction,
            reason=reason,
            requested_quantity=pos.quantity,
            authoritative_position=ex_pos,
            step_size=step_size,
        )
        if exit_decision.verdict is ExitVerdict.ALREADY_FLAT:
            continue
        if exit_decision.verdict is ExitVerdict.APPROVED:
            if request.persist_facts:
                try:
                    persist_exit_intent_before_submission(
                        cycle_id=request.cycle_id,
                        position_id=pos.position_id,
                        execution_mode=request.execution_mode,
                        reason=reason,
                        client_order_id=exit_decision.client_order_id,
                        requested_quantity=exit_decision.quantity,
                        fencing_token=request.fencing_token,
                        submitted_at=request.now,
                    )
                except Exception as exc:  # noqa: BLE001
                    result.record_error(f"exit pre-submission persistence failed; exchange submit blocked: {exc}")
                    continue
            exec_result = execute_reduce_only_exit(
                exit_decision,
                adapter=adapter,
                authoritative_quantity=ex_pos.quantity if ex_pos is not None else pos.quantity,
                step_size=step_size,
            )
            if exec_result.status not in (
                ExitExecutionStatus.NOT_ATTEMPTED,
                ExitExecutionStatus.ALREADY_FLAT_RECONCILED,
            ):
                result.exit_submitted = True
            if request.persist_facts:
                try:
                    persist_exit_submission_result(
                        position_id=pos.position_id,
                        result=exec_result,
                    )
                except Exception as exc:  # noqa: BLE001
                    result.record_error(f"exit submission persistence failed: {exc}")
                if exec_result.status in {
                    ExitExecutionStatus.CLOSED,
                    ExitExecutionStatus.PARTIALLY_REDUCED,
                }:
                    try:
                        persist_exit_result(
                            cycle_id=request.cycle_id,
                            position_id=pos.position_id,
                            execution_mode=request.execution_mode,
                            reason=reason,
                            result=exec_result,
                            fencing_token=request.fencing_token,
                        )
                    except Exception as exc:  # noqa: BLE001
                        result.record_error(f"exit fill persistence failed: {exc}")

    decision_context = DecisionContext(
        cycle_id=request.cycle_id,
        symbol=request.symbol,
        lane=CandidateLane.TESTNET_SAMPLING,
        strategy_id="testnet_sampling_v2",
        strategy_version="1.0.0",
        entry_timeframe=request.entry_timeframe,
        now=request.now,
        already_evaluated_bars=request.already_evaluated_bars,
    )
    outcome = evaluate_symbol(decision_context)
    result.funnel_payload = outcome.funnel.to_payload()

    if not outcome.has_candidate:
        if request.persist_facts:
            try:
                _record_trade_review_skip(request, "NO_CANDIDATE")
            except Exception as exc:  # noqa: BLE001
                result.record_error(f"AI skip persistence failed: {exc}")
        return result

    candidate = outcome.candidate
    assert candidate is not None

    entry_enabled = True if not request.persist_facts else _runtime_entry_enabled()
    result.entry_blocked_by_runtime_control = request.persist_facts and not entry_enabled
    if recon.status is ReconciliationStatus.HEALTHY and not entry_blocked_local:
        _append_funnel_stage(
            result.funnel_payload,
            stage="RECONCILIATION_HEALTHY",
            outcome="PASSED",
            reason_code="OK",
        )
    else:
        reconciliation_reason = (
            "RECONCILIATION_UNAVAILABLE" if recon.status is ReconciliationStatus.UNAVAILABLE else recon.status.value
        )
        _append_funnel_stage(
            result.funnel_payload,
            stage="RECONCILIATION_HEALTHY",
            outcome="REJECTED",
            reason_code=reconciliation_reason,
        )

    entry_runtime = EntryRuntimeContext(
        engine_activation=request.engine_activation,
        execution_mode=request.execution_mode,
        reconciliation_status=recon.status,
        entry_blocked_symbols=recon.entry_blocked_symbols,
        entry_kill_switch_active=request.entry_kill_switch_active or not entry_enabled,
        open_position_symbols=(
            request.open_position_symbols
            | frozenset(
                position.symbol for position in local_state.positions if position.state not in ("CLOSED", "QUARANTINED")
            )
        ),
        daily_trade_limit_reached=request.daily_trade_limit_reached,
        symbol_cooldown_active=request.symbol_cooldown_active,
        now=request.now,
    )

    risk_gate = evaluate_entry(candidate, entry_runtime, None)
    if not risk_gate.approved:
        _append_funnel_stage(
            result.funnel_payload,
            stage="RISK_APPROVED",
            outcome="REJECTED",
            reason_code=risk_gate.reason_code.value,
            metrics={"blocks": "|".join(reason.value for reason, _detail in risk_gate.blocks)},
        )
        if request.persist_facts:
            try:
                _record_trade_review_skip(
                    request,
                    f"RISK_BLOCKED:{risk_gate.reason_code.value}",
                )
            except Exception as exc:  # noqa: BLE001
                result.record_error(f"AI skip persistence failed: {exc}")
        _append_funnel_stage(
            result.funnel_payload,
            stage="AI_REVIEWED",
            outcome="SKIPPED",
            reason_code="AI_REVIEW_DISABLED",
            metrics={"skip_reason": f"RISK_BLOCKED:{risk_gate.reason_code.value}"},
            update_terminal=False,
        )
        return result

    _append_funnel_stage(
        result.funnel_payload,
        stage="RISK_APPROVED",
        outcome="PASSED",
        reason_code="OK",
    )

    if request.persist_facts:
        try:
            ai_review = _run_trade_review(request, candidate)
        except Exception as exc:  # noqa: BLE001
            ai_review = {
                "status": "error",
                "provider": None,
                "model": None,
                "result": None,
                "error": str(exc),
            }
            result.record_error(f"AI trade review failed: {exc}")
    else:
        ai_review = {
            "status": "skipped",
            "provider": None,
            "model": None,
            "result": None,
            "error": None,
        }
    ai_status = str(ai_review.get("status") or "unknown")
    _append_funnel_stage(
        result.funnel_payload,
        stage="AI_REVIEWED",
        outcome="PASSED" if ai_status == "passed" else "SKIPPED",
        reason_code="OK" if ai_status == "passed" else "AI_PROVIDER_UNAVAILABLE",
        metrics={
            "status": ai_status,
            "provider": ai_review.get("provider"),
            "model": ai_review.get("model"),
            "error": ai_review.get("error"),
        },
    )

    try:
        snapshot_market = adapter.fetch_market_snapshot(request.symbol)
    except Exception as exc:  # noqa: BLE001
        result.record_error(f"market snapshot fetch failed: {exc}")
        _append_funnel_stage(
            result.funnel_payload,
            stage="PRICE_DRIFT_APPROVED",
            outcome="ERROR",
            reason_code="EXCHANGE_UNAVAILABLE",
        )
        return result

    gate = evaluate_entry(candidate, entry_runtime, snapshot_market)
    if not gate.approved:
        _append_funnel_stage(
            result.funnel_payload,
            stage="PRICE_DRIFT_APPROVED",
            outcome="REJECTED",
            reason_code=gate.reason_code.value,
            metrics={
                "drift_bps": gate.drift_bps,
                "drift_ceiling_bps": gate.drift_ceiling_bps,
            },
        )
        return result
    _append_funnel_stage(
        result.funnel_payload,
        stage="PRICE_DRIFT_APPROVED",
        outcome="PASSED",
        reason_code="OK",
        metrics={
            "drift_bps": gate.drift_bps,
            "drift_ceiling_bps": gate.drift_ceiling_bps,
        },
    )

    intent_id = str(uuid.uuid4())
    _append_funnel_stage(
        result.funnel_payload,
        stage="INTENT_CREATED",
        outcome="PASSED",
        reason_code="ENTRY_INTENT_CREATED",
        metrics={"intent_id": intent_id},
    )
    quantity = _calculate_quantity(request, snapshot) / snapshot_market.current_price
    if request.persist_facts:
        try:
            persist_entry_intent_before_submission(
                cycle_id=request.cycle_id,
                decision_id=request.decision_id,
                intent_id=intent_id,
                symbol=request.symbol,
                direction=candidate.direction,
                candidate_key=getattr(candidate, "candidate_key", candidate.strategy_id),
                candidate_type=V2CandidateType.SAMPLING,
                execution_mode=request.execution_mode,
                decision_bar_timestamp=(
                    request.entry_timeframe.last_closed.timestamp
                    if request.entry_timeframe.last_closed
                    else request.now
                ),
                fencing_token=request.fencing_token,
            )
        except Exception as exc:  # noqa: BLE001
            result.record_error(f"intent persistence failed before exchange submission: {exc}")
            _append_funnel_stage(
                result.funnel_payload,
                stage="INTENT_DURABLE",
                outcome="ERROR",
                reason_code="LOCAL_STATE_UNAVAILABLE",
            )
            return result

    entry_result = execute_entry(
        candidate,
        gate,
        snapshot_market,
        adapter=adapter,
        intent_id=intent_id,
        quantity=quantity,
        leverage=request.max_leverage,
        engine_activation=request.engine_activation,
    )
    if request.persist_facts:
        try:
            persist_entry_submission_result(
                intent_id=intent_id,
                leverage=request.max_leverage,
                result=entry_result,
            )
        except Exception as exc:  # noqa: BLE001
            result.record_error(f"exchange acknowledgement persistence failed: {exc}")
    result.entry_submitted = entry_result.status not in (
        EntryExecutionStatus.NOT_ATTEMPTED,
        EntryExecutionStatus.SHADOW_REHEARSED,
    )
    _append_funnel_stage(
        result.funnel_payload,
        stage="EXCHANGE_SUBMITTED",
        outcome="PASSED" if entry_result.exchange_order_id else "REJECTED",
        reason_code=entry_result.reason_code.value,
        metrics={
            "client_order_id": entry_result.client_order_id,
            "exchange_order_id": entry_result.exchange_order_id,
            "status": entry_result.status.value,
        },
    )

    if not entry_result.position_projectable:
        return result

    _append_funnel_stage(
        result.funnel_payload,
        stage="EXCHANGE_FILLED",
        outcome="PASSED",
        reason_code="OK",
        metrics={
            "exchange_order_id": entry_result.exchange_order_id,
            "trade_ids": ",".join(entry_result.trade_ids),
            "filled_quantity": entry_result.filled_quantity,
        },
    )
    result.position_projected = True
    _append_funnel_stage(
        result.funnel_payload,
        stage="POSITION_PROJECTED",
        outcome="PASSED",
        reason_code="OK",
    )

    position_id = str(uuid.uuid4())
    plan = None
    protection = None
    try:
        plan = build_protection_plan(
            position_id=position_id,
            candidate=candidate,
            average_fill_price=entry_result.average_fill_price or Decimal("0"),
            filled_quantity=entry_result.filled_quantity,
            tick_size=snapshot_market.tick_size,
        )
        protection = ensure_protection(plan, adapter=adapter)
        result.protection_active = protection.is_active
        if not protection.is_active:
            result.record_error(f"protection failed: {protection.detail}")
    except Exception as exc:  # noqa: BLE001
        result.record_error(f"protection error: {exc}")

    if request.persist_facts:
        try:
            persist_entry_and_protection(
                cycle_id=request.cycle_id,
                decision_id=request.decision_id,
                intent_id=intent_id,
                symbol=request.symbol,
                direction=candidate.direction,
                candidate_key=getattr(candidate, "candidate_key", candidate.strategy_id),
                candidate_type=V2CandidateType.SAMPLING,
                execution_mode=request.execution_mode,
                decision_bar_timestamp=(
                    request.entry_timeframe.last_closed.timestamp
                    if request.entry_timeframe.last_closed
                    else request.now
                ),
                fencing_token=request.fencing_token,
                leverage=request.max_leverage,
                entry_result=entry_result,
                position_id=position_id,
                protection_result=protection,
                stop_loss_price=plan.stop_price if plan else None,
                take_profit_price=plan.take_profit_price if plan else None,
                stop_client_order_id=plan.stop_client_order_id if plan else None,
                tp_client_order_id=plan.tp_client_order_id if plan else None,
            )
        except Exception as exc:  # noqa: BLE001
            result.record_error(f"fact persistence failed: {exc}")

    _append_funnel_stage(
        result.funnel_payload,
        stage="PROTECTION_CONFIRMED",
        outcome="PASSED" if result.protection_active else "ERROR",
        reason_code="OK" if result.protection_active else "PROTECTION_FAILED",
        metrics={
            "stop_exchange_order_id": (protection.stop_exchange_order_id if protection is not None else None),
            "tp_exchange_order_id": (protection.tp_exchange_order_id if protection is not None else None),
        },
    )

    return result
