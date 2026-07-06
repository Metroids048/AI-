"""Manual Paper/Testnet trading application service.

Manual orders are still Execution Layer requests: they are persisted through
OrderExecution, pass Gatekeeper checks, and never bypass stoploss/risk events.
"""

from __future__ import annotations

from datetime import UTC, datetime

from services.execution.gatekeeper import ExecutionGatekeeperService
from services.execution.gateway import ExchangeGateway, NullExchangeGateway
from services.strategy_library import ExecutionRepository
from shared.models import (
    AdjustLeverageRequest,
    CancelOrderRequest,
    ClosePositionRequest,
    ExecutionOrderRequest,
    ExecutionRiskState,
    LeverageAdjustmentResult,
    ManualOrderRequest,
    OrderExecution,
    PositionSnapshot,
    TradeSide,
)


class ManualTradingService:
    def __init__(
        self,
        *,
        execution_repo: ExecutionRepository,
        gatekeeper: ExecutionGatekeeperService,
        gateway: ExchangeGateway | None = None,
    ) -> None:
        self.execution_repo = execution_repo
        self.gatekeeper = gatekeeper
        self.gateway = gateway or NullExchangeGateway()

    def submit_manual_order(self, request: ManualOrderRequest) -> OrderExecution:
        order_request = self._order_request_from_manual(request)
        order = self.gatekeeper.submit_order(order_request)
        if order.execution_status != "accepted":
            return order
        if request.mode == "testnet":
            gateway_result = self.gateway.submit_order(
                live_run_id=request.live_run_id or "manual-testnet",
                order_request=order_request,
            )
            return self._mark_gateway_submitted(order=order, gateway_result=gateway_result)
        filled = self._fill_order(order=order, gateway_name="paper_manual")
        self._open_or_replace_position(request=request, order=filled)
        return filled

    def close_position(self, request: ClosePositionRequest) -> OrderExecution:
        run_type = "paper" if request.mode == "paper" else "live"
        run_id = request.paper_run_id or request.live_run_id or "manual"
        position = self._latest_position(symbol=request.symbol, run_type=run_type, run_id=run_id)
        if position is None and request.mode == "paper":
            position = self._latest_position(symbol=request.symbol, run_type="paper", run_id="manual")
        if position is None:
            raise ValueError("position not found")
        order_request = ExecutionOrderRequest(
            strategy_id=request.strategy_id,
            version_id=request.version_id,
            symbol=request.symbol,
            direction=position.side,
            validation_backtest_run_id=request.validation_backtest_run_id,
            risk_profile_id=request.risk_profile_id,
            paper_run_id=request.paper_run_id if request.mode == "paper" else None,
            live_run_id=request.live_run_id if request.mode == "testnet" else None,
            entry_context={
                "order_type": "market",
                "quantity": abs(position.quantity),
                "reference_price": request.reference_price,
                "requested_notional": abs(position.quantity) * request.reference_price,
                "close_only_mode": True,
                "manual_order_mode": request.mode,
                "timeframe": "1h",
            },
            stoploss_plan={},
            risk_state=ExecutionRiskState(
                account_equity=request.account_equity,
                equity_peak=request.account_equity,
                open_positions=max(1, len(self.execution_repo.list_positions())),
                requested_notional=0.0,
                requested_leverage=1.0,
            ),
            idempotency_key=request.idempotency_key,
        )
        order = self.gatekeeper.submit_order(order_request)
        if order.execution_status != "accepted":
            return order
        if request.mode == "testnet":
            gateway_result = self.gateway.submit_order(
                live_run_id=request.live_run_id or "manual-testnet",
                order_request=order_request,
            )
            return self._mark_gateway_submitted(order=order, gateway_result=gateway_result)
        filled = self._fill_order(order=order, gateway_name="paper_manual")
        self.execution_repo.create_position_snapshot(
            PositionSnapshot(
                run_type="paper",
                run_id=request.paper_run_id or "manual",
                symbol=request.symbol,
                side=position.side,
                quantity=0.0,
                entry_price=position.entry_price,
                mark_price=request.reference_price,
                unrealized_pnl=0.0,
                snapshot_time=datetime.now(UTC),
            )
        )
        return filled

    def adjust_leverage(self, request: AdjustLeverageRequest) -> LeverageAdjustmentResult:
        if request.mode == "testnet":
            result = self.gateway.set_leverage(symbol=request.symbol, leverage=request.leverage)
            return LeverageAdjustmentResult(
                mode=request.mode,
                symbol=request.symbol,
                leverage=request.leverage,
                gateway_name=self.gateway.capability.gateway_name,
                gateway_status=str(result.get("gateway_status", "acknowledged")),
                detail=result,
            )
        return LeverageAdjustmentResult(
            mode=request.mode,
            symbol=request.symbol,
            leverage=request.leverage,
            gateway_name="paper_manual",
            gateway_status="paper_leverage_updated",
            detail={"strategy_id": request.strategy_id},
        )

    def cancel_order(self, request: CancelOrderRequest) -> OrderExecution:
        order = self.execution_repo.get_order(request.order_execution_id)
        if order is None:
            raise ValueError("order not found")
        if order.execution_status in {"filled", "cancelled"}:
            raise ValueError(f"order cannot be cancelled from status {order.execution_status}")
        if request.mode == "testnet":
            if not order.gateway_order_id:
                raise ValueError("testnet order has no gateway_order_id")
            gateway_result = self.gateway.cancel_order(gateway_order_id=order.gateway_order_id)
            return self.execution_repo.update_order(
                order.order_execution_id or "",
                execution_status="cancelled",
                gateway_status=str(gateway_result.get("gateway_status", "cancelled")),
                lifecycle_history=[
                    *order.lifecycle_history,
                    {
                        "at": datetime.now(UTC).isoformat(),
                        "status": gateway_result.get("gateway_status", "cancelled"),
                        "event": "manual_cancel",
                    },
                ],
                last_gateway_update_at=datetime.now(UTC),
            ) or order
        return self.execution_repo.update_order(
            order.order_execution_id or "",
            execution_status="cancelled",
            gateway_name=order.gateway_name or "paper_manual",
            gateway_status="cancelled",
            lifecycle_history=[
                *order.lifecycle_history,
                {"at": datetime.now(UTC).isoformat(), "status": "cancelled", "event": "manual_cancel"},
            ],
            last_gateway_update_at=datetime.now(UTC),
        ) or order

    def _order_request_from_manual(self, request: ManualOrderRequest) -> ExecutionOrderRequest:
        requested_notional = request.quantity * request.reference_price
        return ExecutionOrderRequest(
            strategy_id=request.strategy_id,
            version_id=request.version_id,
            symbol=request.symbol,
            direction=request.direction,
            validation_backtest_run_id=request.validation_backtest_run_id,
            risk_profile_id=request.risk_profile_id,
            paper_run_id=request.paper_run_id if request.mode == "paper" else None,
            live_run_id=request.live_run_id if request.mode == "testnet" else None,
            entry_context={
                "order_type": request.order_type,
                "quantity": request.quantity,
                "limit_price": request.limit_price,
                "reference_price": request.reference_price,
                "requested_notional": requested_notional,
                "requested_leverage": request.leverage,
                "manual_order_mode": request.mode,
                "timeframe": "1h",
            },
            stoploss_plan=({"price": request.stoploss_price} if request.stoploss_price is not None else {}),
            takeprofit_plan=({"price": request.takeprofit_price} if request.takeprofit_price is not None else {}),
            risk_state=ExecutionRiskState(
                account_equity=request.account_equity,
                equity_peak=request.account_equity,
                open_positions=len(self.execution_repo.list_positions()),
                requested_notional=requested_notional,
                requested_leverage=request.leverage,
            ),
            idempotency_key=request.idempotency_key,
        )

    def _fill_order(self, *, order: OrderExecution, gateway_name: str) -> OrderExecution:
        return self.execution_repo.update_order(
            order.order_execution_id or "",
            execution_status="filled",
            gateway_name=gateway_name,
            gateway_status="filled",
            lifecycle_history=[
                *order.lifecycle_history,
                {"at": datetime.now(UTC).isoformat(), "status": "filled", "event": "manual_fill"},
            ],
            last_gateway_update_at=datetime.now(UTC),
        ) or order

    def _mark_gateway_submitted(self, *, order: OrderExecution, gateway_result: dict) -> OrderExecution:
        return self.execution_repo.update_order(
            order.order_execution_id or "",
            gateway_name=self.gateway.capability.gateway_name,
            gateway_order_id=gateway_result.get("gateway_order_id"),
            gateway_status=gateway_result.get("gateway_status"),
            lifecycle_history=[
                *order.lifecycle_history,
                {
                    "at": datetime.now(UTC).isoformat(),
                    "status": gateway_result.get("gateway_status"),
                    "event": "manual_submit",
                },
            ],
            last_gateway_update_at=datetime.now(UTC),
        ) or order

    def _open_or_replace_position(self, *, request: ManualOrderRequest, order: OrderExecution) -> PositionSnapshot:
        quantity = request.quantity if request.direction == TradeSide.LONG else -request.quantity
        return self.execution_repo.create_position_snapshot(
            PositionSnapshot(
                run_type="paper",
                run_id=request.paper_run_id or "manual",
                symbol=request.symbol,
                side=request.direction,
                quantity=quantity,
                entry_price=request.reference_price,
                mark_price=request.reference_price,
                unrealized_pnl=0.0,
                snapshot_time=datetime.now(UTC),
            )
        )

    def _latest_position(self, *, symbol: str, run_type: str, run_id: str) -> PositionSnapshot | None:
        positions = self.execution_repo.list_latest_positions_for_run(run_type=run_type, run_id=run_id)
        for position in positions:
            if position.symbol == symbol and abs(position.quantity) > 0:
                return position
        return None
