"""Exchange-facing request adaptation for the paper runtime.

The gateway remains the sole owner of the final exchange-side close reversal.
Keeping that mapping here prevents the runtime from accidentally reversing a
reduce-only close twice before it reaches Binance.
"""

from __future__ import annotations

from contextlib import suppress
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from services.data.universe import exchange_to_platform_symbol
from services.execution.execution_events import record_execution_event
from services.execution.paper_order_lifecycle import protection_geometry_valid
from services.execution.scheduler_coordination import validate_fence
from services.strategy_library import DecisionEventRepository, ExecutionRepository, ReviewRepository
from shared.models import (
    DecisionEventType,
    ExecutionOrderRequest,
    FailureRecord,
    OrderExecution,
    PaperRun,
    PaperRuntimeAction,
    PositionManagementStatus,
    PositionRecord,
    PositionSnapshot,
    ProtectionRecordStatus,
    StrategyContract,
    TradeSide,
)


def _float_or_none(value: object) -> float | None:
    if value is None:
        return None
    if not isinstance(value, str | int | float | Decimal):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_reduce_only_already_flat(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "-2022" in text or "reduceonly order is rejected" in text


class PaperExchangeExecutionService:
    """Build gateway-safe orders and execute exchange interactions for paper runs."""

    def __init__(
        self,
        *,
        execution_repo: ExecutionRepository | None = None,
        gateway: Any | None = None,
        review_repo: ReviewRepository | None = None,
    ) -> None:
        self.execution_repo = execution_repo
        self.gateway = gateway
        self.review_repo = review_repo
        self._session_managed_position_ids: set[str] = set()

    def register_session_managed_position(self, position_record_id: str | None) -> None:
        if position_record_id:
            self._session_managed_position_ids.add(position_record_id)

    @staticmethod
    def gateway_order_request(
        *,
        order_request: ExecutionOrderRequest,
        position: PositionSnapshot | None,
    ) -> ExecutionOrderRequest:
        context = dict(order_request.entry_context)
        close_only = bool(context.get("close_only_mode", False))
        reference_price = float(context.get("reference_price") or 0)
        if position is not None:
            reference_price = float(
                context.get("reference_price") or position.mark_price or position.entry_price or reference_price
            )
        if close_only and position is not None:
            context_qty = float(context.get("quantity") or 0.0)
            quantity = context_qty if context_qty > 0 else abs(position.quantity)
            direction = position.side
        else:
            quantity = float(context.get("quantity") or 0)
            requested_notional = float(context.get("requested_notional") or 0.0)
            if quantity <= 0 and reference_price > 0 and requested_notional > 0:
                quantity = requested_notional / reference_price
            direction = order_request.direction
            min_notional = float(context.get("min_notional_usdt", 50.0))
            if reference_price > 0 and quantity * reference_price < min_notional:
                quantity = min_notional / reference_price
        context["quantity"] = quantity
        if close_only:
            context["reduce_only"] = True
        return order_request.model_copy(
            update={
                "direction": direction,
                "entry_context": context,
                "paper_run_id": None,
                "live_run_id": None,
            }
        )

    @classmethod
    def gateway_mirror_request(
        cls,
        *,
        order_request: ExecutionOrderRequest,
        position: PositionSnapshot,
    ) -> ExecutionOrderRequest:
        return cls.gateway_order_request(order_request=order_request, position=position)

    def expire_pending_limit_entries(
        self,
        *,
        paper_run: PaperRun,
        cycle_time: datetime,
        parse_datetime: Any,
    ) -> list[PaperRuntimeAction]:
        """Cancel expired gateway limit entries instead of converting them to fills."""
        if self.gateway is None or self.execution_repo is None:
            return []
        actions: list[PaperRuntimeAction] = []
        for order in self.execution_repo.list_orders():
            if (
                order.paper_run_id != paper_run.paper_run_id
                or order.close_only_mode
                or order.execution_status not in {"submitted", "open"}
                or str(order.entry_context.get("order_type", "")).lower() != "limit"
                or not order.gateway_order_id
            ):
                continue
            expiry = parse_datetime(order.entry_context.get("entry_limit_expiry_at"))
            if expiry is None or expiry > cycle_time:
                continue
            try:
                self.gateway.cancel_order(gateway_order_id=str(order.gateway_order_id))
            except Exception as exc:  # noqa: BLE001 - await exchange truth before local rejection
                actions.append(
                    PaperRuntimeAction(
                        symbol=order.symbol,
                        action="entry_limit_cancel_unconfirmed",
                        direction=order.direction,
                        reason=str(exc),
                        order_execution_id=order.order_execution_id,
                    )
                )
                continue
            self.execution_repo.update_order(
                order.order_execution_id or "",
                execution_status="rejected",
                rejection_reason="entry_limit_expired",
                rejection_codes=[*order.rejection_codes, "entry_limit_expired"],
                gateway_status="cancelled",
                entry_context={
                    **order.entry_context,
                    "entry_limit_cancel_reason": "expired_at_closed_15m_boundary",
                    "entry_limit_cancelled_at": cycle_time.isoformat(),
                },
            )
            actions.append(
                PaperRuntimeAction(
                    symbol=order.symbol,
                    action="entry_limit_expired",
                    direction=order.direction,
                    reason="unfilled limit entry cancelled at its 15m expiry",
                    order_execution_id=order.order_execution_id,
                )
            )
        return actions

    # ------------------------------------------------------------------ #
    # Exchange-position helpers                                            #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _exchange_positions(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
        positions: dict[str, dict[str, Any]] = {}
        for item in snapshot.get("open_positions", []) or []:
            if not isinstance(item, dict):
                continue
            symbol = exchange_to_platform_symbol(str(item.get("symbol") or ""))
            quantity = abs(float(item.get("contracts") or 0.0))
            if symbol and quantity > 0:
                side = str(item.get("side") or "").lower() or "unknown"
                key = symbol if symbol not in positions else f"{symbol}#{side}"
                positions[key] = {**item, "contracts": quantity}
        return positions

    @staticmethod
    def _exchange_position_symbol(key: str, position: dict[str, Any]) -> str:
        return exchange_to_platform_symbol(str(position.get("symbol") or key.split("#", 1)[0]))

    @classmethod
    def _exchange_position_symbols(cls, positions: dict[str, dict[str, Any]]) -> set[str]:
        return {cls._exchange_position_symbol(key, item) for key, item in positions.items()}

    @classmethod
    def _ambiguous_hedge_symbols(cls, positions: dict[str, dict[str, Any]]) -> set[str]:
        counts: dict[str, int] = {}
        for key, item in positions.items():
            symbol = cls._exchange_position_symbol(key, item)
            counts[symbol] = counts.get(symbol, 0) + 1
        return {symbol for symbol, count in counts.items() if count > 1}

    def _exchange_position_present(self, *, paper_run: PaperRun, symbol: str) -> bool:
        gateway = self.gateway
        if gateway is None:
            return True
        try:
            snapshot = gateway.reconcile(
                live_run_id=f"paper-testnet:{paper_run.paper_run_id or 'unknown'}:close-confirm"
            )
        except Exception:  # noqa: BLE001 - preserve local position on uncertain exchange state
            return True
        return symbol in self._exchange_position_symbols(self._exchange_positions(snapshot))

    def _exchange_account(self) -> str:
        capability = getattr(self.gateway, "capability", None)
        exchange = str(getattr(capability, "exchange", None) or "paper")
        market_type = str(getattr(capability, "market_type", None) or "paper")
        backend = str(getattr(self.gateway, "api_backend", None) or "local")
        return f"{exchange}:{market_type}:{backend}"

    def _managed_record_for_exchange(
        self,
        *,
        paper_run: PaperRun,
        symbol: str,
        exchange_position: dict[str, Any],
    ):
        if self.execution_repo is None:
            return None
        side = TradeSide.SHORT if str(exchange_position.get("side") or "").lower() == "short" else TradeSide.LONG
        record = self.execution_repo.find_open_position_record(
            exchange_account=self._exchange_account(),
            symbol=symbol,
            position_side=side,
            run_id=paper_run.paper_run_id,
            managed_only=True,
        )
        if (
            record is None
            or record.management_status is not PositionManagementStatus.MANAGED_STRATEGY
            or record.entry_order_id is None
            or record.entry_fill_id is None
            or record.strategy_id != paper_run.strategy_id
        ):
            return None
        if record.position_record_id not in self._session_managed_position_ids:
            raw_update_time = _float_or_none(exchange_position.get("position_update_time"))
            if raw_update_time is None or raw_update_time <= 0:
                return None
            timestamp_seconds = raw_update_time / 1000 if raw_update_time > 10_000_000_000 else raw_update_time
            exchange_update_time = datetime.fromtimestamp(timestamp_seconds, tz=UTC)
            record_opened_at = record.opened_at
            if record_opened_at.tzinfo is None:
                record_opened_at = record_opened_at.replace(tzinfo=UTC)
            if abs((exchange_update_time - record_opened_at).total_seconds()) > 300:
                return None
        entry = self.execution_repo.get_order(record.entry_order_id)
        if (
            entry is None
            or entry.position_record_id != record.position_record_id
            or str(entry.gateway_order_id or "") != str(record.entry_fill_id)
            or entry.paper_run_id != paper_run.paper_run_id
            or entry.strategy_id != paper_run.strategy_id
            or entry.execution_status not in {"filled", "submitted", "open"}
            or entry.order_origin not in {"live_scheduler", "paper_scheduler"}
            or abs(abs(float(exchange_position.get("contracts") or 0.0)) - record.quantity) > 1e-8
        ):
            return None
        exchange_entry_price = _float_or_none(exchange_position.get("entry_price"))
        recorded_entry_price = _float_or_none(entry.entry_context.get("reference_price"))
        if (
            exchange_entry_price is not None
            and exchange_entry_price > 0
            and recorded_entry_price is not None
            and recorded_entry_price > 0
            and abs(exchange_entry_price - recorded_entry_price) > max(recorded_entry_price * 0.01, 1e-8)
        ):
            return None
        return record

    def _record_gateway_mirror_failure(
        self,
        *,
        paper_run: PaperRun,
        order: OrderExecution,
        exc: Exception,
    ) -> None:
        if self.review_repo is None:
            return
        self.review_repo.create_failure(
            FailureRecord(
                strategy_id=paper_run.strategy_id,
                version_id=paper_run.version_id,
                origin_run_type="paper",
                origin_run_id=paper_run.paper_run_id or "",
                failure_type="gateway_mirror_failed",
                failure_summary=f"Gateway mirror failed for {order.symbol}: {exc}",
                evidence_refs=[f"order_execution:{order.order_execution_id}"],
                recommended_change=(
                    "Check Binance Testnet credentials, balances, symbol mapping, and gateway availability."
                ),
            )
        )

    # ------------------------------------------------------------------ #
    # Migrated exchange-interaction methods (formerly in PaperRuntimeService)
    # ------------------------------------------------------------------ #

    def cancel_latest_entry_protections(
        self,
        *,
        paper_run: PaperRun,
        symbol: str,
    ) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {"cancelled": [], "failed": []}
        gateway = self.gateway
        if gateway is None:
            return result
        cancel = getattr(gateway, "cancel_protection_order", None)
        if not callable(cancel) or paper_run.paper_run_id is None:
            return result
        if self.execution_repo is None:
            return result
        record = self.execution_repo.find_managed_position_record_for_run(
            run_id=paper_run.paper_run_id,
            symbol=symbol,
        )
        if record is None or record.entry_order_id is None:
            return result
        entry = self.execution_repo.get_order(record.entry_order_id)
        if entry is None or entry.position_record_id != record.position_record_id:
            return result
        refs = entry.entry_context.get("protection_order_refs", [])
        for ref in refs if isinstance(refs, list) else []:
            if not isinstance(ref, dict):
                continue
            order_id = str(ref.get("algoId") or ref.get("gateway_order_id") or ref.get("id") or "")
            if not order_id:
                continue
            try:
                cancel(symbol=symbol, gateway_order_id=order_id)
                result["cancelled"].append(order_id)
            except Exception:  # noqa: BLE001 - a triggered protection is already terminal
                result["failed"].append(order_id)
        if result["cancelled"]:
            try:
                snapshot = gateway.reconcile(
                    live_run_id=f"paper-testnet:{paper_run.paper_run_id}:confirm-protection-cancel"
                )
                active_ids = {
                    str(item.get("algoId") or item.get("orderId") or item.get("id") or "")
                    for item in snapshot.get("open_orders", [])
                    if isinstance(item, dict)
                }
                still_open = [order_id for order_id in result["cancelled"] if order_id in active_ids]
                if still_open:
                    result["cancelled"] = [order_id for order_id in result["cancelled"] if order_id not in active_ids]
                    result["failed"].extend(still_open)
            except Exception:
                # A cancellation without an exchange confirmation is unsafe for a
                # subsequent ReduceOnly close because Binance may still reserve
                # the entire position for the old protection orders.
                result["failed"].extend(result["cancelled"])
                result["cancelled"] = []
        return result

    def cancel_orphan_exchange_protections(
        self,
        *,
        paper_run: PaperRun,
        snapshot: dict[str, Any],
        exchange_positions: dict[str, dict[str, Any]],
    ) -> list[PaperRuntimeAction]:
        gateway = self.gateway
        cancel = getattr(gateway, "cancel_protection_order", None)
        if not callable(cancel):
            return []
        allowed_symbols = set(paper_run.candidate_symbols)
        actions: list[PaperRuntimeAction] = []
        for raw in snapshot.get("open_orders", []) or []:
            if not isinstance(raw, dict):
                continue
            order_id = str(raw.get("algoId") or "")
            order_type = str(raw.get("orderType") or raw.get("type") or "").upper()
            reduce_only = raw.get("reduceOnly") is True or str(raw.get("reduceOnly")).lower() == "true"
            symbol = exchange_to_platform_symbol(str(raw.get("symbol") or ""))
            if (
                not order_id
                or not reduce_only
                or order_type not in {"STOP_MARKET", "TAKE_PROFIT_MARKET", "LIMIT"}
                or symbol not in allowed_symbols
                or symbol in self._exchange_position_symbols(exchange_positions)
            ):
                continue
            try:
                cancel(symbol=symbol, gateway_order_id=order_id)
            except Exception as exc:  # noqa: BLE001
                actions.append(
                    PaperRuntimeAction(
                        symbol=symbol,
                        action="reconcile_orphan_protection_cancel_failed",
                        reason=str(exc),
                        close_only=True,
                    )
                )
                continue
            actions.append(
                PaperRuntimeAction(
                    symbol=symbol,
                    action="reconcile_cancel_orphan_protection",
                    reason=f"cancelled {order_type} {order_id} because Binance position is flat",
                    close_only=True,
                )
            )
        return actions

    def ensure_exchange_protections(
        self,
        *,
        paper_run: PaperRun,
        snapshot: dict[str, Any],
        exchange_positions: dict[str, dict[str, Any]],
    ) -> list[PaperRuntimeAction]:
        gateway = self.gateway
        refresh = getattr(gateway, "refresh_protection_orders", None)
        submit = getattr(gateway, "submit_order", None)
        if gateway is None or (not callable(refresh) and not callable(submit)):
            return []
        open_algo_by_symbol: dict[str, set[str]] = {}
        for raw in snapshot.get("open_orders", []) or []:
            if not isinstance(raw, dict):
                continue
            if not (raw.get("reduceOnly") is True or str(raw.get("reduceOnly")).lower() == "true"):
                continue
            order_type = str(raw.get("orderType") or raw.get("type") or "").upper()
            if order_type not in {"STOP_MARKET", "TAKE_PROFIT_MARKET", "LIMIT"}:
                continue
            symbol = exchange_to_platform_symbol(str(raw.get("symbol") or ""))
            protection_kind = "STOP" if order_type == "STOP_MARKET" else "TAKE"
            open_algo_by_symbol.setdefault(symbol, set()).add(protection_kind)
        actions: list[PaperRuntimeAction] = []
        if self.execution_repo is None:
            return actions
        ambiguous_symbols = self._ambiguous_hedge_symbols(exchange_positions)
        for position_key, exchange_position in exchange_positions.items():
            symbol = self._exchange_position_symbol(position_key, exchange_position)
            if symbol in ambiguous_symbols:
                if not any(
                    action.symbol == symbol and action.action == "reconcile_ambiguous_hedge_position"
                    for action in actions
                ):
                    actions.append(
                        PaperRuntimeAction(
                            symbol=symbol,
                            action="reconcile_ambiguous_hedge_position",
                            reason="both exchange sides are open; automatic protection and exit are disabled",
                            close_only=True,
                            decision_trace={"reason_code": "AMBIGUOUS_HEDGE_POSITION_IDENTITY"},
                        )
                    )
                continue
            side = TradeSide.SHORT if str(exchange_position.get("side") or "").lower() == "short" else TradeSide.LONG
            any_record = self.execution_repo.find_open_position_record(
                exchange_account=self._exchange_account(),
                symbol=symbol,
                position_side=side,
                run_id=paper_run.paper_run_id,
                managed_only=True,
            )
            if any_record is None:
                any_record = self.execution_repo.find_unmanaged_position_record(
                    exchange_account=self._exchange_account(),
                    symbol=symbol,
                    position_side=side,
                    run_id=paper_run.paper_run_id,
                )
            candidate_protection = (
                self.execution_repo.get_latest_protection_record(any_record.position_record_id or "")
                if any_record is not None
                else None
            )
            reference_price = float(exchange_position.get("mark_price") or exchange_position.get("entry_price") or 0.0)
            if (
                candidate_protection is not None
                and candidate_protection.status is ProtectionRecordStatus.ACTIVE
                and not protection_geometry_valid(
                    side=side,
                    reference_price=reference_price,
                    stop_price=candidate_protection.stop_price,
                    take_price=candidate_protection.take_profit_price,
                )
            ):
                self.execution_repo.update_protection_record(
                    candidate_protection.protection_record_id or "",
                    status=ProtectionRecordStatus.INVALID_PROTECTION_GEOMETRY,
                )
            managed_record = self._managed_record_for_exchange(
                paper_run=paper_run,
                symbol=symbol,
                exchange_position=exchange_position,
            )
            if managed_record is None:
                actions.append(
                    PaperRuntimeAction(
                        symbol=symbol,
                        action="reconcile_unmanaged_external_position",
                        reason="no exact managed position identity; protection refresh and emergency close disabled",
                        close_only=True,
                        decision_trace={"reason_code": PositionManagementStatus.UNMANAGED_EXTERNAL_POSITION.value},
                    )
                )
                continue
            protection = self.execution_repo.get_latest_protection_record(managed_record.position_record_id or "")
            if protection is None or protection.status is not ProtectionRecordStatus.ACTIVE:
                actions.append(
                    PaperRuntimeAction(
                        symbol=symbol,
                        action="reconcile_invalid_or_missing_protection",
                        reason="managed position has no active protection identity",
                        close_only=True,
                        decision_trace={
                            "reason_code": (
                                protection.status.value if protection is not None else "PROTECTION_IDENTITY_UNAVAILABLE"
                            )
                        },
                    )
                )
                continue
            existing_types = open_algo_by_symbol.get(symbol, set())
            if {"STOP", "TAKE"}.issubset(existing_types):
                continue
            entry = (
                self.execution_repo.get_order(managed_record.entry_order_id) if managed_record.entry_order_id else None
            )
            quantity = abs(float(exchange_position.get("contracts") or 0.0))
            mark_price = float(exchange_position.get("mark_price") or 0.0)
            stop_price = protection.stop_price
            take_price = protection.take_profit_price
            failure_reason = "protection_refresh_returned_empty"
            if callable(refresh) and entry is not None and stop_price is not None and take_price is not None:
                protection_request = ExecutionOrderRequest(
                    strategy_id=entry.strategy_id,
                    version_id=entry.version_id,
                    symbol=symbol,
                    direction=entry.direction,
                    entry_context={
                        **entry.entry_context,
                        "order_type": "market",
                        "quantity": quantity,
                        "reference_price": mark_price,
                        "gateway_reference_price": mark_price,
                        "close_only_mode": False,
                        "reduce_only": False,
                    },
                    stoploss_plan={"price": stop_price},
                    takeprofit_plan={"price": take_price},
                    idempotency_key=f"rearm-protection:{paper_run.paper_run_id}:{symbol}",
                )
                try:
                    refs = refresh(
                        order_request=protection_request,
                        quantity=quantity,
                        previous_refs=entry.entry_context.get("protection_order_refs", []),
                    )
                    if refs:
                        self.execution_repo.update_order(
                            entry.order_execution_id or "",
                            entry_context={**entry.entry_context, "protection_order_refs": refs},
                        )
                        actions.append(
                            PaperRuntimeAction(
                                symbol=symbol,
                                action="reconcile_rearm_protection",
                                reference_price=mark_price,
                                decision_trace={
                                    "missing_protection_types": sorted({"STOP", "TAKE"} - existing_types),
                                    "protection_order_refs": refs,
                                },
                            )
                        )
                        continue
                except Exception as exc:  # noqa: BLE001 - fail closed below
                    failure_reason = str(exc)
            else:
                failure_reason = "missing_entry_protection_plan"
            if callable(submit):
                side = (
                    TradeSide.SHORT if str(exchange_position.get("side") or "").lower() == "short" else TradeSide.LONG
                )
                try:
                    result = submit(
                        live_run_id=f"paper-testnet:{paper_run.paper_run_id or 'unknown'}:protection-failure",
                        order_request=ExecutionOrderRequest(
                            strategy_id=paper_run.strategy_id,
                            symbol=symbol,
                            direction=side,
                            entry_context={
                                "order_type": "market",
                                "quantity": quantity,
                                "reference_price": mark_price,
                                "close_only_mode": True,
                                "reduce_only": True,
                            },
                        ),
                    )
                    actions.append(
                        PaperRuntimeAction(
                            symbol=symbol,
                            action="reconcile_close_unprotected_position",
                            direction=side,
                            close_only=True,
                            reason=f"protection rearm failed: {failure_reason}",
                            decision_trace={"gateway_result": result},
                        )
                    )
                except Exception as exc:  # noqa: BLE001 - keep the position visible for next retry
                    actions.append(
                        PaperRuntimeAction(
                            symbol=symbol,
                            action="reconcile_protection_failure",
                            direction=side,
                            close_only=True,
                            reason=f"protection rearm failed: {failure_reason}; emergency close failed: {exc}",
                        )
                    )
        return actions

    def _rearm_exchange_protections(self, *, paper_run: PaperRun, symbol: str) -> None:
        """Restore stop/target brackets after a close attempt fails while still open."""
        gateway = self.gateway
        if gateway is None:
            return
        with suppress(Exception):
            snapshot = gateway.reconcile(live_run_id=f"paper-testnet:{paper_run.paper_run_id or 'unknown'}:rearm")
            positions = self._exchange_positions(snapshot)
            if symbol in self._exchange_position_symbols(positions):
                self.ensure_exchange_protections(
                    paper_run=paper_run,
                    snapshot=snapshot,
                    exchange_positions=positions,
                )

    def reconcile_local_positions_with_exchange(
        self,
        *,
        paper_run: PaperRun,
        strategy: StrategyContract,
        paper_run_id: str,
        active_positions: dict[str, PositionSnapshot],
        exit_ladder_metrics: dict[str, Any],
        protective_trailing: dict[str, Any],
        reconcile_missing_counts: dict[str, int],
        cycle_time: datetime,
        close_position_fn: Any,
    ) -> dict[str, Any]:
        empty: dict[str, Any] = {
            "actions": [],
            "closed": 0,
            "net_pnl": 0.0,
            "gross_pnl": 0.0,
            "fee_cost": 0.0,
            "slippage_cost": 0.0,
        }
        gateway = self.gateway
        if gateway is None:
            return empty
        try:
            snapshot = gateway.reconcile(live_run_id=f"paper-testnet:{paper_run.paper_run_id or 'unknown'}")
        except Exception as exc:  # noqa: BLE001
            if self.review_repo is not None:
                self.review_repo.create_failure(
                    FailureRecord(
                        strategy_id=paper_run.strategy_id,
                        version_id=paper_run.version_id,
                        origin_run_type="paper",
                        origin_run_id=paper_run.paper_run_id or "",
                        failure_type="gateway_reconcile_failed",
                        failure_summary=f"Gateway reconcile failed: {exc}",
                        evidence_refs=[],
                        recommended_change="Inspect Binance simulation connectivity before trusting local positions.",
                    )
                )
            return empty
        if "open_positions" not in snapshot:
            return empty
        exchange_positions = self._exchange_positions(snapshot)
        ambiguous_symbols = self._ambiguous_hedge_symbols(exchange_positions)
        missing_symbols = set(active_positions) - self._exchange_position_symbols(exchange_positions)
        if missing_symbols:
            try:
                confirmation = gateway.reconcile(
                    live_run_id=f"paper-testnet:{paper_run.paper_run_id or 'unknown'}:confirm"
                )
            except Exception:  # noqa: BLE001 - retain local state when confirmation is unavailable
                return empty
            confirmed_positions = self._exchange_positions(confirmation)
            for symbol in missing_symbols:
                for position_key, item in confirmed_positions.items():
                    if self._exchange_position_symbol(position_key, item) == symbol:
                        exchange_positions[position_key] = item
            if confirmation.get("open_orders"):
                snapshot = {**snapshot, "open_orders": confirmation["open_orders"]}
            ambiguous_symbols = self._ambiguous_hedge_symbols(exchange_positions)
        actions: list[PaperRuntimeAction] = []
        for symbol in ambiguous_symbols:
            active_positions.pop(symbol, None)
            actions.append(
                PaperRuntimeAction(
                    symbol=symbol,
                    action="reconcile_ambiguous_hedge_position",
                    reason="both exchange sides are open; automatic protection and exit are disabled",
                    close_only=True,
                    decision_trace={"reason_code": "AMBIGUOUS_HEDGE_POSITION_IDENTITY"},
                )
            )
        allowed_symbols = set(paper_run.candidate_symbols)
        for position_key, item in list(exchange_positions.items()):
            symbol = self._exchange_position_symbol(position_key, item)
            if symbol in ambiguous_symbols:
                continue
            local_position = active_positions.get(symbol)
            if local_position is None:
                continue
            managed_record = self._managed_record_for_exchange(
                paper_run=paper_run,
                symbol=symbol,
                exchange_position=item,
            )
            if managed_record is not None:
                continue
            active_positions.pop(symbol, None)
            if self.execution_repo is not None and local_position.position_record_id is not None:
                stale_record = self.execution_repo.get_position_record(local_position.position_record_id)
                if stale_record is not None:
                    self.execution_repo.update_position_record(
                        stale_record.position_record_id or "",
                        management_status=PositionManagementStatus.CLOSED,
                    )
                    stale_protection = self.execution_repo.get_latest_protection_record(
                        stale_record.position_record_id or ""
                    )
                    if stale_protection is not None:
                        self.execution_repo.update_protection_record(
                            stale_protection.protection_record_id or "",
                            status=ProtectionRecordStatus.INACTIVE,
                        )
            actions.append(
                PaperRuntimeAction(
                    symbol=symbol,
                    action="reconcile_identity_mismatch_quarantined",
                    direction=local_position.side,
                    reason="exchange position cannot be proven to belong to the current execution session",
                    close_only=True,
                    decision_trace={"reason_code": PositionManagementStatus.UNMANAGED_EXTERNAL_POSITION.value},
                )
            )
        if self.execution_repo is not None:
            for position_key, item in exchange_positions.items():
                symbol = self._exchange_position_symbol(position_key, item)
                if symbol in ambiguous_symbols:
                    continue
                reconcile_missing_counts.pop(symbol, None)
                if symbol in active_positions or symbol not in allowed_symbols:
                    continue
                side = TradeSide.SHORT if str(item.get("side") or "").lower() == "short" else TradeSide.LONG
                entry_price = float(item.get("entry_price") or item.get("mark_price") or 0.0)
                mark_price = float(item.get("mark_price") or entry_price)
                record = self.execution_repo.find_unmanaged_position_record(
                    exchange_account=self._exchange_account(),
                    symbol=symbol,
                    position_side=side,
                    run_id=paper_run_id,
                )
                if record is None:
                    record = self.execution_repo.create_position_record(
                        PositionRecord(
                            exchange_account=self._exchange_account(),
                            symbol=symbol,
                            position_side=side,
                            opened_at=cycle_time,
                            quantity=abs(float(item.get("contracts") or 0.0)),
                            order_origin="external_reconciliation",
                            strategy_id=None,
                            run_id=paper_run_id,
                            management_status=PositionManagementStatus.UNMANAGED_EXTERNAL_POSITION,
                        )
                    )
                    self.execution_repo.create_position_snapshot(
                        PositionSnapshot(
                            run_type="paper",
                            run_id=paper_run_id,
                            symbol=symbol,
                            side=side,
                            quantity=abs(float(item.get("contracts") or 0.0)),
                            entry_price=entry_price,
                            mark_price=mark_price,
                            unrealized_pnl=float(item.get("unrealized_pnl") or 0.0),
                            snapshot_time=cycle_time,
                            position_record_id=record.position_record_id,
                        )
                    )
                actions.append(
                    PaperRuntimeAction(
                        symbol=symbol,
                        action="reconcile_unmanaged_external_position",
                        direction=side,
                        reference_price=mark_price,
                        decision_trace={
                            "recovery_source": "binance_position_truth",
                            "reconcile_decoupled_from_entry_cycle": True,
                            "reason_code": PositionManagementStatus.UNMANAGED_EXTERNAL_POSITION.value,
                        },
                    )
                )
        actions.extend(
            self.cancel_orphan_exchange_protections(
                paper_run=paper_run,
                snapshot=snapshot,
                exchange_positions=exchange_positions,
            )
        )
        actions.extend(
            self.ensure_exchange_protections(
                paper_run=paper_run,
                snapshot=snapshot,
                exchange_positions=exchange_positions,
            )
        )
        if self.execution_repo is not None:
            event_repo = DecisionEventRepository(self.execution_repo.session)
            for position_key, exchange_position in exchange_positions.items():
                symbol = self._exchange_position_symbol(position_key, exchange_position)
                if symbol in ambiguous_symbols:
                    continue
                managed_record = self._managed_record_for_exchange(
                    paper_run=paper_run,
                    symbol=symbol,
                    exchange_position=exchange_position,
                )
                if managed_record is None or managed_record.entry_order_id is None:
                    continue
                entry = self.execution_repo.get_order(managed_record.entry_order_id)
                if entry is None:
                    continue
                existing = event_repo.list_events(
                    paper_run_id=paper_run.paper_run_id or "",
                    cycle_id=entry.cycle_id,
                )
                if any(
                    event.event_type is DecisionEventType.POSITION_RECONCILED
                    and event.position_record_id == managed_record.position_record_id
                    for event in existing
                ):
                    continue
                record_execution_event(
                    repository=event_repo,
                    event_type=DecisionEventType.POSITION_RECONCILED,
                    paper_run=paper_run,
                    identity_order=entry,
                    position_record_id=managed_record.position_record_id,
                    reason_code="exchange_position_confirmed",
                    payload={
                        "exchange_quantity": exchange_position.get("contracts"),
                        "exchange_entry_price": exchange_position.get("entry_price"),
                        "exchange_mark_price": exchange_position.get("mark_price"),
                    },
                )
        closed = 0
        net_pnl = 0.0
        gross_pnl = 0.0
        fee_cost = 0.0
        slippage_cost = 0.0
        for symbol, position in list(active_positions.items()):
            if symbol in self._exchange_position_symbols(exchange_positions):
                reconcile_missing_counts.pop(symbol, None)
                continue
            missing_count = reconcile_missing_counts.get(symbol, 0) + 1
            reconcile_missing_counts[symbol] = missing_count
            if missing_count < 2:
                actions.append(
                    PaperRuntimeAction(
                        symbol=symbol,
                        action="reconcile_exchange_position_missing_pending",
                        direction=position.side,
                        close_only=True,
                        reason="awaiting a second scheduler cycle with Binance position flat",
                        decision_trace={
                            "exchange_missing_confirmation_count": missing_count,
                            "reconcile_decoupled_from_entry_cycle": True,
                        },
                    )
                )
                continue
            reconcile_missing_counts.pop(symbol, None)
            cleanup = self.cancel_latest_entry_protections(paper_run=paper_run, symbol=symbol)
            mark_price = float(position.mark_price or position.entry_price)
            realized = close_position_fn(
                paper_run_id=paper_run_id,
                position=position,
                mark_price=mark_price,
                cycle_time=cycle_time,
                strategy=strategy,
            )
            net_pnl += realized.net_pnl
            gross_pnl += realized.gross_pnl
            fee_cost += realized.fee_cost
            slippage_cost += realized.slippage_cost
            closed += 1
            active_positions.pop(symbol, None)
            exit_ladder_metrics.pop(symbol, None)
            protective_trailing.pop(symbol, None)
            actions.append(
                PaperRuntimeAction(
                    symbol=symbol,
                    action=f"reconcile_flat_close_{position.side}",
                    direction=position.side,
                    reference_price=mark_price,
                    close_only=True,
                    decision_trace={
                        "exit_reason": "exchange_position_flat",
                        "reconcile_decoupled_from_entry_cycle": True,
                        "cancelled_protection_order_ids": cleanup["cancelled"],
                        "terminal_protection_order_ids": cleanup["failed"],
                    },
                )
            )
        return {
            "actions": actions,
            "closed": closed,
            "net_pnl": net_pnl,
            "gross_pnl": gross_pnl,
            "fee_cost": fee_cost,
            "slippage_cost": slippage_cost,
        }

    def ensure_binance_execution(
        self,
        *,
        paper_run: PaperRun,
        order: OrderExecution,
        order_request: ExecutionOrderRequest,
        position: PositionSnapshot | None,
        refresh_protection: bool = False,
    ) -> OrderExecution:
        gateway = self.gateway
        if gateway is None:
            return order
        pre_close_cleanup: dict[str, list[str]] = {"cancelled": [], "failed": []}
        try:
            if order_request.cycle_source == "runtime_scheduler" and (
                self.execution_repo is None
                or order_request.scheduler_instance_id is None
                or order_request.fencing_token is None
                or not validate_fence(
                    self.execution_repo.session,
                    lease_name=order_request.lease_name,
                    owner_id=order_request.scheduler_instance_id,
                    fencing_token=order_request.fencing_token,
                )
            ):
                raise ValueError("lease_lost/fenced")
            if order.close_only_mode:
                pre_close_cleanup = self.cancel_latest_entry_protections(
                    paper_run=paper_run,
                    symbol=order.symbol,
                )
                if pre_close_cleanup["failed"]:
                    raise ValueError("gateway_protection_cancel_unconfirmed: " + ",".join(pre_close_cleanup["failed"]))
            mirror_request = self.gateway_order_request(order_request=order_request, position=position)
            event_repo = (
                DecisionEventRepository(self.execution_repo.session) if self.execution_repo is not None else None
            )
            event_request = mirror_request if mirror_request.trade_intent is not None else None
            event_identity = order
            if (
                event_request is None
                and position is not None
                and position.position_record_id is not None
                and self.execution_repo is not None
            ):
                position_record = self.execution_repo.get_position_record(position.position_record_id)
                if position_record is not None and position_record.entry_order_id is not None:
                    event_identity = self.execution_repo.get_order(position_record.entry_order_id) or order
            gateway_result = gateway.submit_order(
                live_run_id=f"paper-testnet:{paper_run.paper_run_id or 'unknown'}",
                order_request=mirror_request,
            )
            if self.execution_repo is not None:
                event_repo = DecisionEventRepository(self.execution_repo.session)
                event_payload = {
                    "order_execution_id": order.order_execution_id,
                    "gateway_order_id": gateway_result.get("gateway_order_id"),
                    "gateway_status": gateway_result.get("gateway_status"),
                }
                try:
                    submitted_event = record_execution_event(
                        repository=event_repo,
                        event_type=DecisionEventType.EXECUTION_ORDER_SUBMITTED,
                        paper_run=paper_run,
                        request=event_request,
                        identity_order=event_identity,
                        position_record_id=(
                            position.position_record_id if position is not None else order.position_record_id
                        ),
                        reason_code="gateway_submit_completed",
                        payload=event_payload,
                    )
                    if submitted_event is None and order_request.cycle_source == "runtime_scheduler":
                        event_payload["event_persistence_error"] = "EXECUTION_EVENT_IDENTITY_UNAVAILABLE"
                    acknowledged_event = record_execution_event(
                        repository=event_repo,
                        event_type=DecisionEventType.ORDER_ACKNOWLEDGED,
                        paper_run=paper_run,
                        request=event_request,
                        identity_order=event_identity,
                        position_record_id=(
                            position.position_record_id if position is not None else order.position_record_id
                        ),
                        reason_code=str(gateway_result.get("gateway_status") or "gateway_acknowledged"),
                        payload=event_payload,
                    )
                    if acknowledged_event is None:
                        event_payload["event_persistence_error"] = "EXECUTION_EVENT_IDENTITY_UNAVAILABLE"
                except Exception as event_exc:  # noqa: BLE001 - exchange acknowledgement remains authoritative
                    self.execution_repo.session.rollback()
                    event_payload["event_persistence_error"] = str(event_exc)
            if refresh_protection or bool(order_request.entry_context.get("refresh_protection")):
                remaining = float(order_request.entry_context.get("remaining_quantity") or 0.0)
                stop_price = order_request.entry_context.get("protection_stop_price")
                refresh = getattr(gateway, "refresh_protection_orders", None)
                if not callable(refresh):
                    raise ValueError("gateway_protection_refresh_unsupported")
                if remaining <= 0 or stop_price is None:
                    raise ValueError("gateway_protection_refresh_missing_levels")
                if position is not None and not protection_geometry_valid(
                    side=position.side,
                    reference_price=float(order_request.entry_context.get("reference_price") or position.mark_price),
                    stop_price=_float_or_none(stop_price),
                    take_price=None,
                ):
                    raise ValueError("INVALID_PROTECTION_GEOMETRY")
                protection_request = mirror_request.model_copy(
                    update={
                        "stoploss_plan": {"price": float(stop_price)},
                        "takeprofit_plan": {},
                        "entry_context": {
                            **mirror_request.entry_context,
                            "close_only_mode": False,
                            "reduce_only": False,
                            "quantity": remaining,
                        },
                    }
                )
                refreshed = refresh(
                    order_request=protection_request,
                    quantity=remaining,
                    previous_refs=order.entry_context.get("protection_order_refs")
                    or gateway_result.get("protection_order_refs"),
                )
                gateway_result["protection_order_refs"] = refreshed
                if not refreshed:
                    raise ValueError("gateway_protection_refresh_failed")
        except Exception as exc:  # noqa: BLE001
            # Exchange already flat: ReduceOnly rejects. Treat as reconcile success so
            # local ghosts cannot retry forever and block new directional opens.
            if (
                bool(order.close_only_mode)
                and _is_reduce_only_already_flat(exc)
                and not self._exchange_position_present(paper_run=paper_run, symbol=order.symbol)
            ):
                self._record_gateway_mirror_failure(paper_run=paper_run, order=order, exc=exc)
                flat_cleanup = self.cancel_latest_entry_protections(
                    paper_run=paper_run,
                    symbol=order.symbol,
                )
                if self.execution_repo is None:
                    return order
                return (
                    self.execution_repo.update_order(
                        order.order_execution_id or "",
                        execution_status="accepted",
                        rejection_reason=None,
                        rejection_codes=[
                            code for code in order.rejection_codes if code != "binance_auto_execute_failed"
                        ],
                        gateway_status="exchange_already_flat",
                        entry_context={
                            **order.entry_context,
                            "exchange_already_flat": True,
                            "gateway_flat_error": str(exc),
                            "cancelled_protection_order_ids": flat_cleanup["cancelled"],
                            "terminal_protection_order_ids": flat_cleanup["failed"],
                        },
                        lifecycle_history=[
                            *order.lifecycle_history,
                            {
                                "at": datetime.now(UTC).isoformat(),
                                "status": "exchange_already_flat",
                                "event": "binance_auto_execute",
                                "error": str(exc),
                            },
                        ],
                    )
                    or order
                )
            if order.close_only_mode and self._exchange_position_present(
                paper_run=paper_run,
                symbol=order.symbol,
            ):
                self._rearm_exchange_protections(paper_run=paper_run, symbol=order.symbol)
            self._record_gateway_mirror_failure(paper_run=paper_run, order=order, exc=exc)
            if self.execution_repo is None:
                return order
            return (
                self.execution_repo.update_order(
                    order.order_execution_id or "",
                    execution_status="rejected",
                    rejection_reason=f"binance_auto_execute_failed: {exc}",
                    rejection_codes=[*order.rejection_codes, "binance_auto_execute_failed"],
                    gateway_status="gateway_failed",
                    entry_context={
                        **order.entry_context,
                        "pre_close_cancelled_protection_order_ids": pre_close_cleanup["cancelled"],
                        "pre_close_failed_protection_order_ids": pre_close_cleanup["failed"],
                    },
                    lifecycle_history=[
                        *order.lifecycle_history,
                        {
                            "at": datetime.now(UTC).isoformat(),
                            "status": "gateway_failed",
                            "event": "binance_auto_execute",
                            "error": str(exc),
                        },
                    ],
                )
                or order
            )
        gateway_status = str(gateway_result.get("gateway_status", "submitted")).lower()
        cleanup: dict[str, list[str]] = pre_close_cleanup
        if gateway_status in {"filled", "closed"}:
            execution_status = "accepted"
            rejection_reason = None
            rejection_codes = order.rejection_codes
        elif gateway_status in {"rejected", "cancelled", "canceled", "expired"}:
            execution_status = "rejected"
            rejection_reason = f"binance_entry_not_filled: {gateway_status}"
            rejection_codes = [*order.rejection_codes, "binance_entry_not_filled"]
        else:
            execution_status = "submitted"
            rejection_reason = None
            rejection_codes = order.rejection_codes
        if self.execution_repo is None:
            return order
        return (
            self.execution_repo.update_order(
                order.order_execution_id or "",
                execution_status=execution_status,
                rejection_reason=rejection_reason,
                rejection_codes=rejection_codes,
                entry_context={
                    **order.entry_context,
                    "quantity": gateway_result.get("quantity", order.entry_context.get("quantity")),
                    "event_persistence_error": event_payload.get("event_persistence_error")
                    if self.execution_repo is not None
                    else None,
                    "protection_order_refs": gateway_result.get("protection_order_refs", []),
                    "pre_close_cancelled_protection_order_ids": pre_close_cleanup["cancelled"],
                    "pre_close_failed_protection_order_ids": pre_close_cleanup["failed"],
                    "cancelled_protection_order_ids": cleanup["cancelled"],
                    "terminal_protection_order_ids": cleanup["failed"],
                },
                gateway_name=getattr(gateway.capability, "gateway_name", "gateway_mirror"),
                gateway_order_id=gateway_result.get("gateway_order_id"),
                gateway_status=gateway_result.get("gateway_status", "submitted"),
                lifecycle_history=[
                    *order.lifecycle_history,
                    {
                        "at": datetime.now(UTC).isoformat(),
                        "status": gateway_result.get("gateway_status", "submitted"),
                        "event": "binance_auto_execute",
                    },
                ],
                last_gateway_update_at=datetime.now(UTC),
            )
            or order
        )
