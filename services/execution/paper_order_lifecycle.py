"""Local paper-order and position lifecycle operations.

This module deliberately has no gateway dependency.  It owns only the local
execution facts that must remain stable while the runtime orchestrator decides
when an order should be submitted or a position should be closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from services.execution.execution_truth import ExecutionMode, SimulatedFill, resolve_execution_mode
from services.strategy_library import ExecutionRepository
from shared.models import (
    OrderExecution,
    PositionManagementStatus,
    PositionRecord,
    PositionSnapshot,
    ProtectionRecord,
    ProtectionRecordStatus,
    StrategyContract,
    TradeSide,
)


@dataclass(frozen=True)
class EstimatedTransactionCost:
    fee_cost: float
    slippage_cost: float
    fee_bps: float
    slippage_bps: float

    @property
    def total_cost(self) -> float:
        return self.fee_cost + self.slippage_cost

    def as_dict(self) -> dict[str, float]:
        return {
            "fee_cost": self.fee_cost,
            "slippage_cost": self.slippage_cost,
            "total_cost": self.total_cost,
            "fee_bps": self.fee_bps,
            "slippage_bps": self.slippage_bps,
        }


@dataclass(frozen=True)
class RealizedOutcome:
    gross_pnl: float
    fee_cost: float
    slippage_cost: float

    @property
    def net_pnl(self) -> float:
        return self.gross_pnl - self.fee_cost - self.slippage_cost


class PaperOrderLifecycleService:
    """Persist local paper fills, positions, and estimated execution costs."""

    def __init__(self, *, execution_repo: ExecutionRepository) -> None:
        self.execution_repo = execution_repo

    def fill_order(
        self,
        *,
        order: OrderExecution,
        cycle_time: datetime,
        simulated_fill: SimulatedFill | None = None,
    ) -> OrderExecution:
        requested_mode = order.entry_context.get("execution_mode")
        if requested_mode == ExecutionMode.BINANCE_TESTNET.value and not bool(
            order.entry_context.get("exchange_fill_confirmed")
        ):
            raise ValueError("Binance Testnet order cannot be filled without authoritative exchange fill receipt")
        if requested_mode == ExecutionMode.LOCAL_PAPER.value and simulated_fill is None:
            raise ValueError("LOCAL_PAPER requires an explicit SimulatedFill")
        gateway_name = order.gateway_name or "paper_runtime"
        exchange_confirmed = bool(order.entry_context.get("exchange_fill_confirmed"))
        entry_context = dict(order.entry_context)
        if simulated_fill is not None:
            entry_context.update(
                {
                    "simulated_fill_id": simulated_fill.simulated_fill_id,
                    "quantity": str(simulated_fill.filled_quantity),
                    "reference_price": str(simulated_fill.average_fill_price),
                    "fill_source": "SimulatedFill",
                }
            )
        return (
            self.execution_repo.update_order(
                order.order_execution_id or "",
                execution_status="FILLED" if exchange_confirmed else "filled",
                gateway_name=gateway_name,
                entry_context=entry_context,
                gateway_status=order.gateway_status or "filled",
                lifecycle_history=[
                    *order.lifecycle_history,
                    {
                        "at": cycle_time.isoformat(),
                        "status": "FILLED" if exchange_confirmed else "filled",
                        "event": "exchange_fill_confirmed" if exchange_confirmed else "paper_runtime_fill",
                    },
                ],
                last_gateway_update_at=cycle_time,
            )
            or order
        )

    def open_position(
        self,
        *,
        paper_run_id: str,
        order: OrderExecution,
        cycle_time: datetime,
        execution_mode: str = "local_paper",
    ) -> PositionSnapshot:
        # Paper-only local fills must NOT be tracked as MANAGED_STRATEGY — they are
        # never submitted to the exchange so reconciliation would always flag them as
        # ghost positions.  Use PAPER_SIMULATION_ONLY so the exchange-reconcile loop
        # skips them entirely while still allowing the paper P&L tracker to work.
        strict_mode = execution_mode in {mode.value for mode in ExecutionMode}
        resolved_mode = resolve_execution_mode(execution_mode, migration=not strict_mode)
        is_paper_only = resolved_mode is ExecutionMode.LOCAL_PAPER
        exchange_fill_confirmed = bool(order.entry_context.get("exchange_fill_confirmed"))
        receipt_id = _non_empty_str(order.entry_context.get("entry_fill_receipt_id"))
        position_group_id = _non_empty_str(order.entry_context.get("position_group_id"))
        if (
            resolved_mode is ExecutionMode.BINANCE_TESTNET
            and strict_mode
            and (
                not exchange_fill_confirmed
                or not order.gateway_order_id
                or not receipt_id
                or not position_group_id
                or self.execution_repo.get_exchange_fill_receipt(receipt_id) is None
            )
        ):
            raise ValueError("Binance Testnet position projection requires authoritative exchange fill receipt")
        reference_price = Decimal(
            str(
                order.entry_context.get("exchange_average_fill_price")
                if exchange_fill_confirmed
                else order.entry_context.get("reference_price", "0")
            )
        )
        requested_notional = Decimal(str(order.entry_context.get("requested_notional", "0")))
        quantity_value = (
            order.entry_context.get("exchange_filled_quantity")
            if exchange_fill_confirmed
            else order.entry_context.get("quantity")
        )
        quantity = float(quantity_value or 0.0)
        if quantity <= 0:
            quantity = float(requested_notional / reference_price) if reference_price > 0 else 0.0
        min_notional = float(order.entry_context.get("min_notional_usdt", 50.0))
        if not exchange_fill_confirmed and reference_price > 0 and quantity * float(reference_price) < min_notional:
            quantity = min_notional / float(reference_price)
        position_record = self.execution_repo.create_position_record(
            PositionRecord(
                exchange_account=str(order.entry_context.get("exchange_account") or "paper:paper:local"),
                symbol=order.symbol,
                position_side=order.direction,
                entry_order_id=order.order_execution_id,
                entry_fill_id=order.gateway_order_id,
                entry_fill_receipt_id=receipt_id,
                position_group_id=position_group_id,
                execution_mode=resolved_mode if strict_mode else None,
                opened_at=cycle_time,
                quantity=abs(quantity),
                order_origin=order.order_origin,
                strategy_id=order.strategy_id,
                run_id=paper_run_id,
                management_status=(
                    PositionManagementStatus.PAPER_SIMULATION_ONLY
                    if is_paper_only
                    else PositionManagementStatus.MANAGED_STRATEGY
                ),
            )
        )
        record_id = position_record.position_record_id
        if record_id is None:
            raise ValueError("position identity was not persisted")
        self.execution_repo.update_order(order.order_execution_id or "", position_record_id=record_id)
        stop_price = _positive_float(order.stoploss_plan.get("price"))
        take_price = _positive_float(order.takeprofit_plan.get("price"))
        geometry_valid = protection_geometry_valid(
            side=order.direction,
            reference_price=float(reference_price),
            stop_price=stop_price,
            take_price=take_price,
        )
        stop_exchange_order_id, take_profit_exchange_order_id = _protection_exchange_ids(
            order.entry_context.get("protection_order_refs")
        )
        exchange_protection_confirmed = bool(stop_exchange_order_id and take_profit_exchange_order_id)
        self.execution_repo.create_protection_record(
            ProtectionRecord(
                position_record_id=record_id,
                stop_price=stop_price,
                take_profit_price=take_price,
                stop_exchange_order_id=stop_exchange_order_id,
                take_profit_exchange_order_id=take_profit_exchange_order_id,
                protection_source="strategy_entry",
                status=(
                    ProtectionRecordStatus.ACTIVE
                    if geometry_valid
                    and (resolved_mode is ExecutionMode.LOCAL_PAPER or not strict_mode or exchange_protection_confirmed)
                    else (
                        ProtectionRecordStatus.PENDING_EXCHANGE_CONFIRMATION
                        if geometry_valid and resolved_mode is ExecutionMode.BINANCE_TESTNET
                        else ProtectionRecordStatus.INVALID_PROTECTION_GEOMETRY
                    )
                ),
            )
        )
        return self.execution_repo.create_position_snapshot(
            PositionSnapshot(
                run_type="paper",
                run_id=paper_run_id,
                symbol=order.symbol,
                side=order.direction,
                quantity=quantity,
                entry_price=float(reference_price),
                mark_price=float(reference_price),
                unrealized_pnl=0.0,
                snapshot_time=cycle_time,
                position_record_id=record_id,
            )
        )

    def close_position(
        self,
        *,
        paper_run_id: str,
        position: PositionSnapshot,
        mark_price: float,
        cycle_time: datetime,
        strategy: StrategyContract,
        remaining_quantity: float = 0.0,
    ) -> RealizedOutcome:
        gross_pnl = realized_pnl(position=position, mark_price=mark_price)
        entry_cost = estimated_transaction_cost(
            price=position.entry_price,
            quantity=abs(position.quantity),
            strategy=strategy,
            symbol=position.symbol,
        )
        exit_cost = estimated_transaction_cost(
            price=mark_price,
            quantity=abs(position.quantity),
            strategy=strategy,
            symbol=position.symbol,
        )
        self.execution_repo.create_position_snapshot(
            PositionSnapshot(
                run_type="paper",
                run_id=paper_run_id,
                symbol=position.symbol,
                side=position.side,
                quantity=remaining_quantity,
                entry_price=position.entry_price,
                mark_price=mark_price,
                unrealized_pnl=0.0,
                snapshot_time=cycle_time,
                position_record_id=position.position_record_id,
            )
        )
        if abs(remaining_quantity) <= 1e-12 and position.position_record_id is not None:
            self.execution_repo.update_position_record(
                position.position_record_id,
                management_status=PositionManagementStatus.CLOSED,
            )
            protection = self.execution_repo.get_latest_protection_record(position.position_record_id)
            if protection is not None and protection.protection_record_id is not None:
                self.execution_repo.update_protection_record(
                    protection.protection_record_id,
                    status=ProtectionRecordStatus.INACTIVE,
                )
        return RealizedOutcome(
            gross_pnl=gross_pnl,
            fee_cost=entry_cost.fee_cost + exit_cost.fee_cost,
            slippage_cost=entry_cost.slippage_cost + exit_cost.slippage_cost,
        )

    def record_estimated_order_cost(
        self,
        *,
        order: OrderExecution,
        strategy: StrategyContract,
        price: float,
    ) -> OrderExecution:
        quantity = abs(float(order.entry_context.get("quantity") or 0.0))
        if quantity <= 0:
            requested_notional = abs(float(order.entry_context.get("requested_notional") or 0.0))
            quantity = requested_notional / price if price > 0 else 0.0
        cost = estimated_transaction_cost(price=price, quantity=quantity, strategy=strategy, symbol=order.symbol)
        return (
            self.execution_repo.update_order(
                order.order_execution_id or "",
                entry_context={
                    **order.entry_context,
                    "execution_kind": "strategy_trade",
                    "estimated_cost": cost.as_dict(),
                },
            )
            or order
        )

    def mark_position(
        self,
        *,
        paper_run_id: str,
        position: PositionSnapshot,
        mark_price: float,
        cycle_time: datetime,
    ) -> PositionSnapshot:
        return self.execution_repo.create_position_snapshot(
            PositionSnapshot(
                run_type="paper",
                run_id=paper_run_id,
                symbol=position.symbol,
                side=position.side,
                quantity=position.quantity,
                entry_price=position.entry_price,
                mark_price=mark_price,
                unrealized_pnl=realized_pnl(position=position, mark_price=mark_price),
                snapshot_time=cycle_time,
                position_record_id=position.position_record_id,
            )
        )


def realized_pnl(*, position: PositionSnapshot, mark_price: float) -> float:
    if position.side == TradeSide.LONG:
        return (mark_price - position.entry_price) * position.quantity
    return (position.entry_price - mark_price) * position.quantity


def _positive_float(value: object) -> float | None:
    if value is not None and not isinstance(value, str | int | float | Decimal):
        return None
    try:
        parsed = float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
    return parsed if parsed is not None and parsed > 0 else None


def _non_empty_str(value: object) -> str | None:
    parsed = str(value).strip() if value is not None else ""
    return parsed or None


def _protection_exchange_ids(value: object) -> tuple[str | None, str | None]:
    if not isinstance(value, list):
        return None, None
    stop_id: str | None = None
    take_id: str | None = None
    for item in value:
        if not isinstance(item, dict):
            continue
        order_id = _non_empty_str(item.get("gateway_order_id") or item.get("algoId") or item.get("id"))
        kind = str(
            item.get("kind") or item.get("type") or item.get("orderType") or item.get("protection_order_kind") or ""
        ).lower()
        if not order_id:
            continue
        if "stop" in kind or "algo" in kind:
            stop_id = stop_id or order_id
        elif "take" in kind or "limit" in kind:
            take_id = take_id or order_id
    return stop_id, take_id


def protection_geometry_valid(
    *,
    side: TradeSide,
    reference_price: float,
    stop_price: float | None,
    take_price: float | None,
) -> bool:
    if reference_price <= 0 or stop_price is None:
        return False
    if side is TradeSide.LONG:
        return stop_price < reference_price and (take_price is None or take_price > reference_price)
    return stop_price > reference_price and (take_price is None or take_price < reference_price)


def estimated_transaction_cost(
    *,
    price: float,
    quantity: float,
    strategy: StrategyContract,
    symbol: str,
) -> EstimatedTransactionCost:
    entry_rules = strategy.rules.entry_rules
    core_symbols = {"BTC/USDT", "ETH/USDT", "SOL/USDT"}
    is_core = symbol.replace(":USDT", "") in core_symbols
    fee_bps = float(
        entry_rules.get(
            "core_fee_bps" if is_core else "standard_fee_bps",
            entry_rules.get("fee_bps", 8.0),
        )
    )
    slippage_bps = float(
        entry_rules.get(
            "core_slippage_bps" if is_core else "standard_slippage_bps",
            entry_rules.get("slippage_bps", 6.0),
        )
    )
    notional = abs(price * quantity)
    return EstimatedTransactionCost(
        fee_cost=notional * fee_bps / 10_000,
        slippage_cost=notional * slippage_bps / 10_000,
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
    )
