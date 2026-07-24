"""Local paper-order and position lifecycle operations.

This module deliberately has no gateway dependency.  It owns only the local
execution facts that must remain stable while the runtime orchestrator decides
when an order should be submitted or a position should be closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

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

    def fill_order(self, *, order: OrderExecution, cycle_time: datetime) -> OrderExecution:
        gateway_name = order.gateway_name or "paper_runtime"
        return (
            self.execution_repo.update_order(
                order.order_execution_id or "",
                execution_status="filled",
                gateway_name=gateway_name,
                gateway_status=order.gateway_status or "filled",
                lifecycle_history=[
                    *order.lifecycle_history,
                    {
                        "at": cycle_time.isoformat(),
                        "status": "filled",
                        "event": "paper_runtime_fill",
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
    ) -> PositionSnapshot:
        exchange_fill_confirmed = bool(order.entry_context.get("exchange_fill_confirmed"))
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
                opened_at=cycle_time,
                quantity=abs(quantity),
                order_origin=order.order_origin,
                strategy_id=order.strategy_id,
                run_id=paper_run_id,
                management_status=PositionManagementStatus.MANAGED_STRATEGY,
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
        self.execution_repo.create_protection_record(
            ProtectionRecord(
                position_record_id=record_id,
                stop_price=stop_price,
                take_profit_price=take_price,
                protection_source="strategy_entry",
                status=(
                    ProtectionRecordStatus.ACTIVE
                    if geometry_valid
                    else ProtectionRecordStatus.INVALID_PROTECTION_GEOMETRY
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
