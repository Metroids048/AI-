"""Explicit Binance Testnet round-trip acceptance workflow."""

from __future__ import annotations

import contextlib
from typing import Protocol

from services.data.universe import AUTO_SIMULATION_EXECUTION_SYMBOLS
from shared.models import (
    TestnetAcceptanceOrderEvidence,
    TestnetAcceptanceRunRequest,
    TestnetAcceptanceRunResult,
    TestnetAcceptanceSymbolResult,
)
from shared.models.risk import medium_risk_profile

from .risk_tiers import default_asset_risk_tiers, resolve_asset_risk_tier, scale_asset_risk_tiers


class AcceptanceGateway(Protocol):
    def preflight(self) -> dict: ...

    def account_equity(self) -> float: ...

    def fetch_last_price(self, symbol: str) -> float: ...

    def set_leverage(self, *, symbol: str, leverage: float) -> dict: ...

    def submit_acceptance_order(
        self,
        *,
        symbol: str,
        side: str,
        requested_notional: float,
        reference_price: float,
        reduce_only: bool,
        stoploss_price: float | None,
        takeprofit_price: float | None,
        idempotency_key: str,
    ) -> dict: ...

    def cancel_protection_order(self, *, symbol: str, gateway_order_id: str) -> None: ...

    def final_state(self) -> dict: ...


class TestnetAcceptanceService:
    def __init__(self, *, gateway: AcceptanceGateway) -> None:
        self.gateway = gateway

    def run(self, request: TestnetAcceptanceRunRequest) -> TestnetAcceptanceRunResult:
        preflight = self.gateway.preflight()
        baseline_orders = list(preflight.get("open_orders") or [])
        baseline_positions = list(preflight.get("open_positions") or [])
        if (baseline_orders or baseline_positions) and not request.preserve_existing_state:
            raise ValueError("testnet acceptance requires zero existing positions and open orders")

        symbols = request.symbols or list(AUTO_SIMULATION_EXECUTION_SYMBOLS)
        equity = self.gateway.account_equity()
        profile = medium_risk_profile()
        tiers = request.asset_risk_tiers or scale_asset_risk_tiers(
            default_asset_risk_tiers(),
            max_leverage=profile.max_leverage,
            max_symbol_exposure=profile.max_symbol_exposure,
        )
        completed: list[str] = []
        evidence: list[TestnetAcceptanceOrderEvidence] = []
        symbol_results: list[TestnetAcceptanceSymbolResult] = []
        failed_symbol: str | None = None
        error_summary: str | None = None
        compensation_attempted = False

        for index, symbol in enumerate(symbols):
            tier = resolve_asset_risk_tier(symbol, tiers)
            notional = min(equity * tier.max_position_fraction, request.max_notional_usdt)
            reference_price = self.gateway.fetch_last_price(symbol)
            baseline_position = self._baseline_position_for_symbol(baseline_positions, symbol)
            acceptance_side = (
                "sell" if baseline_position and self._position_side(baseline_position) == "short" else "buy"
            )
            close_side = "buy" if acceptance_side == "sell" else "sell"
            if acceptance_side == "sell":
                stoploss_price = reference_price * (1 + request.stoploss_bps / 10_000)
                takeprofit_price = reference_price - 2.0 * (stoploss_price - reference_price)
            else:
                stoploss_price = reference_price * (1 - request.stoploss_bps / 10_000)
                takeprofit_price = reference_price + 2.0 * (reference_price - stoploss_price)
            if not baseline_position:
                self.gateway.set_leverage(symbol=symbol, leverage=tier.leverage)
            protection_refs: list[dict] = []
            order_refs: list[str] = []
            try:
                opened = self.gateway.submit_acceptance_order(
                    symbol=symbol,
                    side=acceptance_side,
                    requested_notional=notional,
                    reference_price=reference_price,
                    reduce_only=False,
                    stoploss_price=stoploss_price,
                    takeprofit_price=takeprofit_price,
                    idempotency_key=f"{request.idempotency_key or 'acceptance'}-{index}-open",
                )
                evidence.append(self._evidence(opened, leverage=tier.leverage, action="open"))
                order_refs.append(str(opened.get("gateway_order_id", "")))
                protection_refs = list(opened.get("protection_order_refs", []))
                closed = self.gateway.submit_acceptance_order(
                    symbol=symbol,
                    side=close_side,
                    requested_notional=notional,
                    reference_price=reference_price,
                    reduce_only=True,
                    stoploss_price=None,
                    takeprofit_price=None,
                    idempotency_key=f"{request.idempotency_key or 'acceptance'}-{index}-close",
                )
                evidence.append(self._evidence(closed, leverage=tier.leverage, action="close"))
                order_refs.append(str(closed.get("gateway_order_id", "")))
                completed.append(symbol)
                symbol_results.append(
                    TestnetAcceptanceSymbolResult(
                        symbol=symbol,
                        run_status="completed",
                        final_stage="closed",
                        leverage=tier.leverage,
                        requested_notional=notional,
                        reference_price=reference_price,
                        order_refs=[ref for ref in order_refs if ref],
                        protection_order_refs=self._protection_ids(protection_refs),
                        final_position_status="baseline_preserved" if request.preserve_existing_state else "flat",
                    )
                )
            except Exception as exc:  # noqa: BLE001 - compensation must run for gateway failures
                failed_symbol = symbol
                error_summary = str(exc)
                compensation_attempted = True
                compensation_succeeded = False
                try:
                    compensated = self.gateway.submit_acceptance_order(
                        symbol=symbol,
                        side=close_side,
                        requested_notional=notional,
                        reference_price=reference_price,
                        reduce_only=True,
                        stoploss_price=None,
                        takeprofit_price=None,
                        idempotency_key=f"{request.idempotency_key or 'acceptance'}-{index}-compensate",
                    )
                    evidence.append(self._evidence(compensated, leverage=tier.leverage, action="compensate"))
                    order_refs.append(str(compensated.get("gateway_order_id", "")))
                    compensation_succeeded = True
                except Exception as compensation_exc:  # noqa: BLE001
                    error_summary = f"{error_summary}; compensation failed: {compensation_exc}"
                symbol_results.append(
                    TestnetAcceptanceSymbolResult(
                        symbol=symbol,
                        run_status="failed",
                        final_stage="compensated" if compensation_succeeded else "compensation_failed",
                        leverage=tier.leverage,
                        requested_notional=notional,
                        reference_price=reference_price,
                        order_refs=[ref for ref in order_refs if ref],
                        protection_order_refs=self._protection_ids(protection_refs),
                        compensation_attempted=True,
                        compensation_succeeded=compensation_succeeded,
                        final_position_status=(
                            "baseline_preserved"
                            if compensation_succeeded and request.preserve_existing_state
                            else ("flat" if compensation_succeeded else "residual_possible")
                        ),
                        failure_class=type(exc).__name__,
                        error_summary=error_summary,
                    )
                )
                break
            finally:
                for ref in protection_refs:
                    order_id = str(ref.get("gateway_order_id") or ref.get("algoId") or ref.get("id") or "")
                    if not order_id:
                        continue
                    with contextlib.suppress(Exception):
                        self.gateway.cancel_protection_order(symbol=symbol, gateway_order_id=order_id)

        processed = {item.symbol for item in symbol_results}
        for symbol in symbols:
            if symbol not in processed:
                symbol_results.append(
                    TestnetAcceptanceSymbolResult(
                        symbol=symbol,
                        run_status="skipped",
                        final_stage="not_started",
                        final_position_status="not_checked",
                        failure_class="stopped_after_failure",
                    )
                )

        final = self.gateway.final_state()
        open_orders = list(final.get("open_orders", []))
        open_positions = list(final.get("open_positions", []))
        completed_all = len(completed) == len(symbols)
        if request.preserve_existing_state:
            baseline_order_ids = {self._order_id(order) for order in baseline_orders}
            final_order_ids = {self._order_id(order) for order in open_orders}
            clean_final_state = (
                self._position_signature(open_positions) == self._position_signature(baseline_positions)
                and final_order_ids == baseline_order_ids
            )
        else:
            clean_final_state = not open_orders and not open_positions
        status = "completed" if completed_all and clean_final_state else "failed"
        if status == "failed" and error_summary is None:
            error_summary = "acceptance finished with residual positions or open orders"
        return TestnetAcceptanceRunResult(
            run_status=status,
            requested_symbols=list(symbols),
            completed_symbols=completed,
            failed_symbol=failed_symbol,
            filled_order_count=sum(1 for item in evidence if item.gateway_status == "filled"),
            orders=evidence,
            symbol_results=symbol_results,
            compensation_attempted=compensation_attempted,
            final_open_position_count=len(open_positions),
            final_open_order_count=len(open_orders),
            error_summary=error_summary,
            baseline_preserved=bool(request.preserve_existing_state and clean_final_state),
            baseline_position_count=len(baseline_positions),
            baseline_order_count=len(baseline_orders),
        )

    @staticmethod
    def _order_id(order: dict) -> str:
        return str(order.get("algoId") or order.get("id") or order.get("orderId") or "")

    @staticmethod
    def _normal_symbol(symbol: str) -> str:
        return symbol.replace(":USDT", "").upper()

    @classmethod
    def _baseline_position_for_symbol(cls, positions: list[dict], symbol: str) -> dict | None:
        expected = cls._normal_symbol(symbol)
        for position in positions:
            if cls._normal_symbol(str(position.get("symbol") or "")) == expected:
                return position
        return None

    @staticmethod
    def _position_side(position: dict) -> str:
        side = str(position.get("side") or "").lower()
        if side in {"long", "short"}:
            return side
        amount = float(position.get("contracts") or position.get("positionAmt") or 0.0)
        return "short" if amount < 0 else "long"

    @classmethod
    def _position_signature(cls, positions: list[dict]) -> dict[tuple[str, str], float]:
        signature: dict[tuple[str, str], float] = {}
        for position in positions:
            quantity = abs(float(position.get("contracts") or position.get("positionAmt") or 0.0))
            if quantity <= 0:
                continue
            key = (cls._normal_symbol(str(position.get("symbol") or "")), cls._position_side(position))
            signature[key] = round(quantity, 10)
        return signature

    @staticmethod
    def _protection_ids(refs: list[dict]) -> list[str]:
        return [
            order_id
            for ref in refs
            if (order_id := str(ref.get("gateway_order_id") or ref.get("algoId") or ref.get("id") or ""))
        ]

    @staticmethod
    def _evidence(raw: dict, *, leverage: float, action: str) -> TestnetAcceptanceOrderEvidence:
        return TestnetAcceptanceOrderEvidence(
            gateway_order_id=str(raw.get("gateway_order_id", "")),
            gateway_status=str(raw.get("gateway_status", "unknown")),
            symbol=str(raw.get("symbol", "")),
            side=str(raw.get("side", "")),
            action=action,
            quantity=float(raw.get("quantity", 0.0)),
            requested_notional=float(raw.get("requested_notional", 0.0)),
            leverage=leverage,
            reduce_only=bool(raw.get("reduce_only", False)),
        )
