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
    ExitReason,
    ExitVerdict,
    evaluate_exit,
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
from services.automated_trading.application.recovery_service import recover_pending_state
from services.automated_trading.domain.candidates import CandidateLane
from services.automated_trading.domain.enums import (
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
    # Risk / position context
    risk_per_trade: Decimal = Decimal("0.01")
    max_leverage: int = 10
    account_equity: Decimal = Decimal("10000")
    open_position_symbols: frozenset[str] = frozenset()
    entry_kill_switch_active: bool = False
    daily_trade_limit_reached: bool = False
    symbol_cooldown_active: bool = False
    # Optional accounts snapshot from a prior reconcile (skip fetch if provided)
    authoritative_snapshot: AuthoritativeAccountSnapshot | None = None


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

    def record_error(self, msg: str) -> None:
        logger.error("[cycle %s %s] %s", self.cycle_id, self.symbol, msg)
        self.errors.append(msg)


def _build_local_state(adapter, execution_mode: V2ExecutionMode) -> LocalStateView:
    """Read open V2 positions for reconciliation."""
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
        return LocalStateView(positions=positions)
    except Exception as exc:  # noqa: BLE001
        logger.warning("failed to read local state for reconciliation: %s", exc)
        return LocalStateView()


def _calculate_quantity(request: CycleRequest, snapshot: AuthoritativeAccountSnapshot) -> Decimal:
    """Risk-based position sizing: risk_per_trade * equity / entry_price."""
    equity = max(snapshot.equity, Decimal("1"))
    notional = equity * request.risk_per_trade
    return notional


def run_automated_trading_cycle(request: CycleRequest, adapter: BinanceTestnetAdapter) -> CycleResult:
    """Run one V2 automated trading cycle.

    The function is safe to call in SHADOW mode: no orders will be submitted,
    but the full decision funnel runs and its record is returned.
    """
    result = CycleResult(cycle_id=request.cycle_id, symbol=request.symbol)

    # 1. Fetch authoritative snapshot
    try:
        snapshot = request.authoritative_snapshot or adapter.fetch_authoritative_snapshot()
    except Exception as exc:  # noqa: BLE001
        result.record_error(f"snapshot fetch failed: {exc}")
        result.reconciliation_status = ReconciliationStatus.UNAVAILABLE
        return result

    # 2. Reconcile
    local_state = _build_local_state(adapter, request.execution_mode)
    recon = reconcile(snapshot, local_state)
    result.reconciliation_status = recon.status

    if recon.status in (ReconciliationStatus.RECOVERY_REQUIRED, ReconciliationStatus.UNAVAILABLE):
        recovery = recover_pending_state(snapshot, recon, local_state=local_state)
        if recovery.entry_blocked:
            logger.info(
                "[cycle %s %s] reconciliation=%s; entry blocked",
                request.cycle_id,
                request.symbol,
                recon.status.value,
            )

    # 3. Evaluate exits for any open positions
    for pos in local_state.positions:
        if pos.symbol != request.symbol or pos.state in ("CLOSED", "QUARANTINED"):
            continue
        ex_pos = next(
            (p for p in snapshot.positions if p.symbol == request.symbol and p.direction == pos.direction),
            None,
        )
        exit_decision = evaluate_exit(
            position_id=pos.position_id,
            symbol=pos.symbol,
            direction=pos.direction,
            reason=ExitReason.TIME_EXIT,
            requested_quantity=pos.quantity,
            authoritative_position=ex_pos,
            step_size=Decimal("0.001"),
        )
        if exit_decision.verdict is ExitVerdict.ALREADY_FLAT:
            pass  # Will be reconciled below

    # 4. Decision funnel
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

    # 5. Entry gate
    entry_runtime = EntryRuntimeContext(
        engine_activation=request.engine_activation,
        execution_mode=request.execution_mode,
        reconciliation_status=recon.status,
        entry_blocked_symbols=recon.entry_blocked_symbols,
        entry_kill_switch_active=request.entry_kill_switch_active,
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

    # 6. Execute entry
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

    # 7. Protection
    position_id = str(uuid.uuid4())
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

    return result
