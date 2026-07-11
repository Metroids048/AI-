"""Delta-neutral Spot + USDT perpetual funding carry execution."""

from __future__ import annotations

import uuid
from typing import Protocol

from shared.models import (
    CarryExecutionLegResult,
    CarryExecutionRequest,
    CarryExecutionStatus,
    FundingArbitrageSignal,
)


class SpotCarryGateway(Protocol):
    def preflight(self) -> dict: ...

    def submit_market_order(
        self,
        *,
        symbol: str,
        side: str,
        notional_usdt: float,
        quantity: float | None,
        idempotency_key: str,
    ) -> dict: ...

    def final_state(self) -> dict: ...


class PerpCarryGateway(Protocol):
    def preflight(self) -> dict: ...

    def submit_carry_order(
        self,
        *,
        symbol: str,
        side: str,
        notional_usdt: float,
        quantity: float,
        reduce_only: bool,
        idempotency_key: str,
    ) -> dict: ...

    def final_state(self) -> dict: ...


class CarryExecutionService:
    def __init__(self, *, spot_gateway: SpotCarryGateway, perp_gateway: PerpCarryGateway) -> None:
        self.spot_gateway = spot_gateway
        self.perp_gateway = perp_gateway

    def run(
        self,
        request: CarryExecutionRequest,
        *,
        signal: FundingArbitrageSignal,
    ) -> CarryExecutionStatus:
        run_id = request.idempotency_key or str(uuid.uuid4())
        states = ["planned"]
        legs: list[CarryExecutionLegResult] = []
        if not self._admitted(request=request, signal=signal):
            return CarryExecutionStatus(
                run_id=run_id,
                run_status="rejected",
                carry_state="planned",
                state_history=states,
                signal=signal,
                error_summary="; ".join(signal.rejection_reasons) or "funding signal did not pass carry admission",
            )
        spot_preflight = self.spot_gateway.preflight()
        perp_preflight = self.perp_gateway.preflight()
        if any(
            (
                spot_preflight.get("open_orders"),
                spot_preflight.get("open_positions"),
                perp_preflight.get("open_orders"),
                perp_preflight.get("open_positions"),
            )
        ):
            raise ValueError("carry acceptance requires clean Spot and Futures testnet accounts")

        spot_quantity = 0.0
        try:
            spot_entry = self.spot_gateway.submit_market_order(
                symbol=request.symbol,
                side="buy",
                notional_usdt=request.notional_usdt,
                quantity=None,
                idempotency_key=f"{run_id}-spot-entry",
            )
            spot_quantity = float(spot_entry.get("quantity") or 0.0)
            if spot_quantity <= 0:
                raise ValueError("spot entry returned no filled quantity")
            legs.append(self._leg(spot_entry, venue="spot", reduce_only=False))
            states.append("first_leg_filled")
            perp_entry = self.perp_gateway.submit_carry_order(
                symbol=request.perp_symbol,
                side="sell",
                notional_usdt=request.notional_usdt,
                quantity=spot_quantity,
                reduce_only=False,
                idempotency_key=f"{run_id}-perp-entry",
            )
            legs.append(self._leg(perp_entry, venue="futures", reduce_only=False))
            states.append("hedged")
        except Exception as exc:  # noqa: BLE001 - compensate the first leg immediately
            if spot_quantity > 0:
                compensated = self.spot_gateway.submit_market_order(
                    symbol=request.symbol,
                    side="sell",
                    notional_usdt=request.notional_usdt,
                    quantity=spot_quantity,
                    idempotency_key=f"{run_id}-spot-compensate",
                )
                legs.append(self._leg(compensated, venue="spot", reduce_only=True))
                states.append("compensated")
            return CarryExecutionStatus(
                run_id=run_id,
                run_status="failed",
                carry_state=states[-1],
                state_history=states,
                signal=signal,
                legs=legs,
                final_net_exposure_usdt=self._net_exposure(request.notional_usdt),
                error_summary=str(exc),
            )

        if not request.close_immediately:
            return CarryExecutionStatus(
                run_id=run_id,
                run_status="running",
                carry_state="hedged",
                state_history=states,
                signal=signal,
                legs=legs,
                final_net_exposure_usdt=0.0,
            )

        states.append("closing")
        try:
            perp_exit = self.perp_gateway.submit_carry_order(
                symbol=request.perp_symbol,
                side="buy",
                notional_usdt=request.notional_usdt,
                quantity=spot_quantity,
                reduce_only=True,
                idempotency_key=f"{run_id}-perp-exit",
            )
            legs.append(self._leg(perp_exit, venue="futures", reduce_only=True))
            spot_exit = self.spot_gateway.submit_market_order(
                symbol=request.symbol,
                side="sell",
                notional_usdt=request.notional_usdt,
                quantity=spot_quantity,
                idempotency_key=f"{run_id}-spot-exit",
            )
            legs.append(self._leg(spot_exit, venue="spot", reduce_only=True))
        except Exception as exc:  # noqa: BLE001
            return CarryExecutionStatus(
                run_id=run_id,
                run_status="failed",
                carry_state="closing",
                state_history=states,
                signal=signal,
                legs=legs,
                final_net_exposure_usdt=self._net_exposure(request.notional_usdt),
                error_summary=f"carry exit failed: {exc}",
            )
        spot_final = self.spot_gateway.final_state()
        perp_final = self.perp_gateway.final_state()
        if any(
            (
                spot_final.get("open_orders"),
                spot_final.get("open_positions"),
                perp_final.get("open_orders"),
                perp_final.get("open_positions"),
            )
        ):
            return CarryExecutionStatus(
                run_id=run_id,
                run_status="failed",
                carry_state="closing",
                state_history=states,
                signal=signal,
                legs=legs,
                final_net_exposure_usdt=self._net_exposure(request.notional_usdt),
                error_summary="carry exit left residual orders or positions",
            )
        states.append("closed")
        return CarryExecutionStatus(
            run_id=run_id,
            run_status="completed",
            carry_state="closed",
            state_history=states,
            signal=signal,
            legs=legs,
            final_net_exposure_usdt=0.0,
        )

    @staticmethod
    def _admitted(*, request: CarryExecutionRequest, signal: FundingArbitrageSignal) -> bool:
        return bool(
            signal.should_enter_paper
            and (signal.funding_bps or 0) > 0
            and (signal.estimated_net_edge_bps or 0) >= request.min_net_edge_bps
            and signal.basis_bps is not None
        )

    @staticmethod
    def _leg(raw: dict, *, venue: str, reduce_only: bool) -> CarryExecutionLegResult:
        return CarryExecutionLegResult(
            venue=venue,
            gateway_order_id=str(raw.get("gateway_order_id", "")),
            gateway_status=str(raw.get("gateway_status", "unknown")),
            symbol=str(raw.get("symbol", "")),
            side=str(raw.get("side", "")),
            quantity=float(raw.get("quantity") or 0.0),
            notional_usdt=float(raw.get("notional_usdt") or raw.get("requested_notional") or 0.0),
            reduce_only=reduce_only,
        )

    def _net_exposure(self, notional: float) -> float:
        spot_open = bool(self.spot_gateway.final_state().get("open_positions"))
        perp_open = bool(self.perp_gateway.final_state().get("open_positions"))
        if spot_open and perp_open:
            return 0.0
        if spot_open:
            return notional
        if perp_open:
            return -notional
        return 0.0
