"""Autonomous paper-runtime cycles over validation-admitted strategies."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from services.data import DataRepository
from services.data.service import DEFAULT_BINANCE_TOP20
from services.execution.gatekeeper import ExecutionGatekeeperService
from services.execution.paper_signal import PaperSignalGenerator
from services.strategy_library import (
    AgentTaskRepository,
    ExecutionRepository,
    NotificationRepository,
    PaperRunRepository,
    ReviewRepository,
    StrategyRepository,
)
from shared.models import (
    ExecutionOrderRequest,
    OrderExecution,
    PaperRun,
    PaperRunStepRequest,
    PaperRuntimeAction,
    PaperRuntimeCycleRequest,
    PaperRuntimeCycleResult,
    PaperRuntimeStatus,
    PositionSnapshot,
    StrategyContract,
    TradeSide,
)


class PaperRuntimeService:
    """Run one autonomous paper cycle while preserving gatekeeper admission."""

    def __init__(
        self,
        *,
        data_repo: DataRepository,
        execution_repo: ExecutionRepository,
        paper_repo: PaperRunRepository,
        strategy_repo: StrategyRepository,
        agent_repo: AgentTaskRepository | None = None,
        review_repo: ReviewRepository | None = None,
        notification_repo: NotificationRepository | None = None,
        gatekeeper: ExecutionGatekeeperService,
    ) -> None:
        self.data_repo = data_repo
        self.execution_repo = execution_repo
        self.paper_repo = paper_repo
        self.strategy_repo = strategy_repo
        self.gatekeeper = gatekeeper
        self.signal_generator = PaperSignalGenerator(
            data_repo=data_repo,
            execution_repo=execution_repo,
            agent_repo=agent_repo,
            strategy_repo=strategy_repo,
            review_repo=review_repo,
            notification_repo=notification_repo,
        )

    def get_runtime_status(self, *, paper_run_id: str) -> PaperRuntimeStatus:
        paper_run = self._require_paper_run(paper_run_id)
        positions = self.execution_repo.list_latest_positions_for_run(
            run_type="paper",
            run_id=paper_run_id,
        )
        metrics = dict(paper_run.paper_metrics_summary)
        return PaperRuntimeStatus(
            paper_run_id=paper_run_id,
            paper_status=paper_run.paper_status,
            candidate_symbols=paper_run.candidate_symbols,
            open_position_symbols=sorted(position.symbol for position in positions),
            account_equity=float(metrics.get("account_equity", self._starting_equity(paper_run))),
            last_cycle_at=_parse_datetime(metrics.get("last_cycle_at")),
            last_scanned_symbols=list(metrics.get("last_scanned_symbols", [])),
            last_action_counts=dict(metrics.get("last_action_counts", {})),
            last_cycle_decisions=list(metrics.get("last_cycle_decisions", [])),
        )

    def run_cycle(self, *, paper_run_id: str, request: PaperRuntimeCycleRequest) -> PaperRuntimeCycleResult:
        paper_run = self._require_paper_run(paper_run_id)
        strategy = self._require_strategy(paper_run.strategy_id)
        cycle_time = datetime.now(UTC)
        current_positions = self.execution_repo.list_latest_positions_for_run(
            run_type="paper",
            run_id=paper_run_id,
        )
        active_positions = {position.symbol: position for position in current_positions}
        scanned_symbols = self._select_symbols(paper_run=paper_run, request=request)
        actions: list[PaperRuntimeAction] = []
        metrics = dict(paper_run.paper_metrics_summary)
        processed_keys = set(metrics.get("processed_cycle_keys", []))
        new_processed_keys = list(processed_keys)
        realized_total = float(metrics.get("realized_pnl_total", 0.0))
        daily_realized_pnl = float(metrics.get("daily_realized_pnl", 0.0))
        weekly_realized_pnl = float(metrics.get("weekly_realized_pnl", 0.0))
        consecutive_losses = int(metrics.get("consecutive_losses", 0))
        opened_positions = 0
        closed_positions = 0
        rejected_orders = 0
        skipped_symbols = 0

        for symbol in scanned_symbols:
            latest_bar = self.data_repo.get_latest_ohlcv_bar(symbol=symbol, timeframe=request.timeframe)
            if latest_bar is None:
                skipped_symbols += 1
                actions.append(
                    PaperRuntimeAction(
                        symbol=symbol,
                        action="skip_no_market_data",
                        reason="latest market bar is unavailable",
                    )
                )
                continue
            cycle_key = f"{paper_run_id}:{symbol}:{request.timeframe}:{latest_bar.timestamp.isoformat()}"
            if cycle_key in processed_keys:
                skipped_symbols += 1
                actions.append(
                    PaperRuntimeAction(
                        symbol=symbol,
                        action="skip_duplicate_cycle",
                        reason="symbol already processed for this closed bar",
                        reference_price=float(latest_bar.close),
                        idempotency_key=cycle_key,
                    )
                )
                continue

            base_order = self.signal_generator.generate_order(
                paper_run=paper_run,
                strategy=strategy,
                request=PaperRunStepRequest(
                    symbol=symbol,
                    timeframe=request.timeframe,
                    idempotency_key=cycle_key,
                    enable_decision_veto=request.enable_decision_veto,
                ),
                positions=list(active_positions.values()),
            )
            decision_trace = dict(base_order.entry_context.get("decision_pipeline", {}))
            if not bool(base_order.entry_context.get("paper_order_should_trade", True)):
                skipped_symbols += 1
                if cycle_key not in new_processed_keys:
                    new_processed_keys.append(cycle_key)
                actions.append(
                    PaperRuntimeAction(
                        symbol=symbol,
                        action="skip_no_trade_decision",
                        direction=base_order.direction,
                        reason=base_order.entry_context.get("decision_reason"),
                        reference_price=float(latest_bar.close),
                        idempotency_key=cycle_key,
                        decision_trace=decision_trace,
                    )
                )
                continue
            if cycle_key not in new_processed_keys:
                new_processed_keys.append(cycle_key)
            current_position = active_positions.get(symbol)
            reference_price = float(latest_bar.close)

            if current_position is not None:
                if request.close_on_opposite_signal and current_position.side != base_order.direction:
                    close_order = self._close_order_request(
                        base_order=base_order,
                        current_position=current_position,
                    )
                    order = self.gatekeeper.submit_order(close_order)
                    if order.execution_status == "accepted":
                        order = self._fill_order(order=order, cycle_time=cycle_time)
                        realized = self._close_position(
                            paper_run_id=paper_run_id,
                            position=current_position,
                            mark_price=reference_price,
                            cycle_time=cycle_time,
                        )
                        realized_total += realized
                        daily_realized_pnl += realized
                        weekly_realized_pnl += realized
                        consecutive_losses = consecutive_losses + 1 if realized < 0 else 0
                        closed_positions += 1
                        active_positions.pop(symbol, None)
                        actions.append(
                            PaperRuntimeAction(
                                symbol=symbol,
                                action="close_long" if current_position.side == TradeSide.LONG else "close_short",
                                direction=current_position.side,
                                order_execution_id=order.order_execution_id,
                                reference_price=reference_price,
                                close_only=True,
                                idempotency_key=cycle_key,
                                decision_trace=decision_trace,
                            )
                        )
                    else:
                        rejected_orders += 1
                        actions.append(
                            PaperRuntimeAction(
                                symbol=symbol,
                                action="rejected",
                                direction=current_position.side,
                                reason=order.rejection_reason,
                                order_execution_id=order.order_execution_id,
                                reference_price=reference_price,
                                close_only=True,
                                idempotency_key=cycle_key,
                                decision_trace=decision_trace,
                            )
                        )
                    continue

                self._mark_position(
                    paper_run_id=paper_run_id,
                    position=current_position,
                    mark_price=reference_price,
                    cycle_time=cycle_time,
                )
                actions.append(
                    PaperRuntimeAction(
                        symbol=symbol,
                        action="hold_long" if current_position.side == TradeSide.LONG else "hold_short",
                        direction=current_position.side,
                        reference_price=reference_price,
                        idempotency_key=cycle_key,
                        decision_trace=decision_trace,
                    )
                )
                continue

            order = self.gatekeeper.submit_order(base_order)
            if order.execution_status != "accepted":
                rejected_orders += 1
                actions.append(
                    PaperRuntimeAction(
                        symbol=symbol,
                        action="rejected",
                        direction=base_order.direction,
                        reason=order.rejection_reason,
                        order_execution_id=order.order_execution_id,
                        reference_price=reference_price,
                        idempotency_key=cycle_key,
                        decision_trace=decision_trace,
                    )
                )
                continue

            order = self._fill_order(order=order, cycle_time=cycle_time)
            position = self._open_position(
                paper_run_id=paper_run_id,
                order=order,
                cycle_time=cycle_time,
            )
            active_positions[symbol] = position
            opened_positions += 1
            actions.append(
                PaperRuntimeAction(
                    symbol=symbol,
                    action="open_long" if position.side == TradeSide.LONG else "open_short",
                    direction=position.side,
                    order_execution_id=order.order_execution_id,
                    reference_price=reference_price,
                    idempotency_key=cycle_key,
                    decision_trace=decision_trace,
                )
            )
        account_equity = self._starting_equity(paper_run) + realized_total
        equity_peak = max(float(metrics.get("equity_peak", self._starting_equity(paper_run))), account_equity)
        last_action_counts = {
            "opened": opened_positions,
            "closed": closed_positions,
            "rejected": rejected_orders,
            "skipped": skipped_symbols,
        }
        updated_metrics = {
            **metrics,
            "account_equity": account_equity,
            "equity_peak": equity_peak,
            "realized_pnl_total": realized_total,
            "daily_realized_pnl": daily_realized_pnl,
            "weekly_realized_pnl": weekly_realized_pnl,
            "consecutive_losses": consecutive_losses,
            "last_cycle_at": cycle_time.isoformat(),
            "last_scanned_symbols": scanned_symbols,
            "last_action_counts": last_action_counts,
            "last_cycle_actions": [action.model_dump(mode="json") for action in actions],
            "last_cycle_decisions": [
                {
                    "symbol": action.symbol,
                    "action": action.action,
                    "idempotency_key": action.idempotency_key,
                    "decision_trace": action.decision_trace,
                    "reason": action.reason,
                }
                for action in actions
                if action.decision_trace
            ],
            "processed_cycle_keys": new_processed_keys[-500:],
            "open_position_symbols": sorted(active_positions.keys()),
        }
        updated_run = self.paper_repo.update_paper_run(
            paper_run_id,
            paper_status="running",
            paper_metrics_summary=updated_metrics,
        )
        if updated_run is None:
            raise ValueError("paper run disappeared during runtime update")
        return PaperRuntimeCycleResult(
            paper_run_id=paper_run_id,
            paper_status=updated_run.paper_status,
            cycle_time=cycle_time,
            scanned_symbols=scanned_symbols,
            actions=actions,
            opened_positions=opened_positions,
            closed_positions=closed_positions,
            rejected_orders=rejected_orders,
            skipped_symbols=skipped_symbols,
            open_position_symbols=sorted(active_positions.keys()),
            account_equity=account_equity,
        )

    def _require_paper_run(self, paper_run_id: str) -> PaperRun:
        paper_run = self.paper_repo.get_paper_run(paper_run_id)
        if paper_run is None:
            raise ValueError("paper run not found")
        return paper_run

    def _require_strategy(self, strategy_id: str) -> StrategyContract:
        strategy = self.strategy_repo.get_strategy(strategy_id)
        if strategy is None:
            raise ValueError("strategy not found")
        return strategy

    @staticmethod
    def _starting_equity(paper_run: PaperRun) -> float:
        return float(
            paper_run.paper_metrics_summary.get("account_equity")
            or paper_run.execution_profile.get("account_equity")
            or 10_000.0
        )

    @staticmethod
    def _select_symbols(*, paper_run: PaperRun, request: PaperRuntimeCycleRequest) -> list[str]:
        base = request.symbols or paper_run.candidate_symbols or DEFAULT_BINANCE_TOP20
        deduped = list(dict.fromkeys(base))
        return deduped[: request.max_symbols]

    @staticmethod
    def _close_order_request(
        *,
        base_order: ExecutionOrderRequest,
        current_position: PositionSnapshot,
    ) -> ExecutionOrderRequest:
        risk_state = (
            base_order.risk_state.model_copy(
                update={
                    "requested_notional": 0.0,
                    "requested_leverage": 1.0,
                }
            )
            if base_order.risk_state is not None
            else None
        )
        return base_order.model_copy(
            update={
                "direction": current_position.side,
                "entry_context": {
                    **base_order.entry_context,
                    "close_only_mode": True,
                    "paper_runtime_action": "close_position",
                },
                "risk_state": risk_state,
            }
        )

    def _fill_order(self, *, order: OrderExecution, cycle_time: datetime) -> OrderExecution:
        return self.execution_repo.update_order(
            order.order_execution_id or "",
            execution_status="filled",
            gateway_name="paper_runtime",
            gateway_status="filled",
            lifecycle_history=[
                *order.lifecycle_history,
                {
                    "at": cycle_time.isoformat(),
                    "status": "filled",
                    "event": "paper_runtime_fill",
                },
            ],
            last_gateway_update_at=cycle_time,
        ) or order

    def _open_position(self, *, paper_run_id: str, order: OrderExecution, cycle_time: datetime) -> PositionSnapshot:
        reference_price = Decimal(str(order.entry_context.get("reference_price", "0")))
        requested_notional = Decimal(str(order.entry_context.get("requested_notional", "0")))
        quantity = float(requested_notional / reference_price) if reference_price > 0 else 0.0
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
            )
        )

    def _close_position(
        self,
        *,
        paper_run_id: str,
        position: PositionSnapshot,
        mark_price: float,
        cycle_time: datetime,
    ) -> float:
        realized = _realized_pnl(position=position, mark_price=mark_price)
        self.execution_repo.create_position_snapshot(
            PositionSnapshot(
                run_type="paper",
                run_id=paper_run_id,
                symbol=position.symbol,
                side=position.side,
                quantity=0.0,
                entry_price=position.entry_price,
                mark_price=mark_price,
                unrealized_pnl=0.0,
                snapshot_time=cycle_time,
            )
        )
        return realized

    def _mark_position(
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
                unrealized_pnl=_realized_pnl(position=position, mark_price=mark_price),
                snapshot_time=cycle_time,
            )
        )


def _realized_pnl(*, position: PositionSnapshot, mark_price: float) -> float:
    if position.side == TradeSide.LONG:
        return (mark_price - position.entry_price) * position.quantity
    return (position.entry_price - mark_price) * position.quantity


def _parse_datetime(value: object) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    return None
