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
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from services.automated_trading.application.decision_service import (
    DecisionContext,
    TimeframeView,
    evaluate_symbol,
)
from services.automated_trading.application.entry_service import (
    EntryExecutionStatus,
    EntryRuntimeContext,
    evaluate_entry,
    execute_entry,
)
from services.automated_trading.application.exit_service import (
    ExitExecutionStatus,
    ExitReason,
    ExitVerdict,
    evaluate_exit,
    execute_reduce_only_exit,
)
from services.automated_trading.application.protection_service import (
    build_protection_plan,
    ensure_protection,
)
from services.automated_trading.application.reconciliation_service import (
    LocalPositionView,
    LocalStateView,
    ReconciliationStatus,
    reconcile,
)
from services.automated_trading.application.recovery_executor import execute_recovery_actions
from services.automated_trading.application.recovery_service import recover_pending_state
from services.automated_trading.domain.candidates import CandidateLane
from services.automated_trading.domain.enums import (
    V2CandidateType,
    V2ExecutionMode,
)
from services.automated_trading.infrastructure.runtime_lock import EngineActivation

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
        from services.automated_trading.infrastructure.repository import (
            AutomatedTradingRepository,
        )
        from services.database import get_session_factory

        with get_session_factory()() as session:
            repo = AutomatedTradingRepository(session)
            open_positions = repo.get_open_positions(execution_mode)

        positions = tuple(
            LocalPositionView(
                position_id=p.position_id,
                symbol=p.symbol,
                direction=p.direction,
                quantity=Decimal(str(p.quantity)),
                state=p.state,
                claim_keys=frozenset({p.position_id}),
                has_active_protection=p.state == "PROTECTED",
            )
            for p in open_positions
        )
        return LocalStateLoadResult(status="OK", state=LocalStateView(positions=positions))
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

    local_load = _build_local_state(
        adapter,
        request.execution_mode,
        fail_closed=request.persist_facts,
    )
    entry_blocked_local = local_load.status != "OK" or local_load.state is None
    local_state: LocalStateView
    if entry_blocked_local:
        result.record_error("LOCAL_STATE_UNAVAILABLE — entry blocked")
        result.reconciliation_status = ReconciliationStatus.UNAVAILABLE
        local_state = LocalStateView()
        recon = reconcile(snapshot, local_state)
    else:
        assert local_load.state is not None
        local_state = local_load.state
        recon = reconcile(snapshot, local_state)
        result.reconciliation_status = recon.status
        if recon.status in (ReconciliationStatus.RECOVERY_REQUIRED, ReconciliationStatus.UNAVAILABLE):
            recovery = recover_pending_state(snapshot, recon, local_state=local_state)
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

    decision_context = DecisionContext(
        cycle_id=request.cycle_id,
        symbol=request.symbol,
        lane=CandidateLane.TESTNET_SAMPLING,
        strategy_id="testnet_sampling_v2",
        strategy_version="1.0.0",
        entry_timeframe=request.entry_timeframe,
        now=request.now,
    )
    outcome = evaluate_symbol(decision_context)
    result.funnel_payload = outcome.funnel.to_payload()

    if not outcome.has_candidate:
        return result

    candidate = outcome.candidate
    assert candidate is not None

    entry_enabled = True if not request.persist_facts else _runtime_entry_enabled()
    if (request.persist_facts and not entry_enabled) or entry_blocked_local:
        result.entry_blocked_by_runtime_control = request.persist_facts and not entry_enabled
        if entry_blocked_local or not entry_enabled:
            return result

    entry_runtime = EntryRuntimeContext(
        engine_activation=request.engine_activation,
        execution_mode=request.execution_mode,
        reconciliation_status=recon.status,
        entry_blocked_symbols=recon.entry_blocked_symbols,
        entry_kill_switch_active=request.entry_kill_switch_active or not entry_enabled,
        open_position_symbols=request.open_position_symbols,
        daily_trade_limit_reached=request.daily_trade_limit_reached,
        symbol_cooldown_active=request.symbol_cooldown_active,
        now=request.now,
    )

    try:
        snapshot_market = adapter.fetch_market_snapshot(request.symbol)
    except Exception as exc:  # noqa: BLE001
        result.record_error(f"market snapshot fetch failed: {exc}")
        return result

    gate = evaluate_entry(candidate, entry_runtime, snapshot_market)
    if not gate.approved:
        return result

    intent_id = str(uuid.uuid4())
    quantity = _calculate_quantity(request, snapshot) / snapshot_market.current_price
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
    result.entry_submitted = entry_result.status not in (
        EntryExecutionStatus.NOT_ATTEMPTED,
        EntryExecutionStatus.SHADOW_REHEARSED,
    )

    if not entry_result.position_projectable:
        return result

    result.position_projected = True

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
            from services.automated_trading.application.fact_persistence import (
                persist_entry_and_protection,
            )

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

    return result
