"""Autonomous paper-runtime cycles over validation-admitted strategies."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from services.data import DataRepository
from services.data.service import DEFAULT_BINANCE_TOP20
from services.execution.gatekeeper import ExecutionGatekeeperService
from services.execution.gateway import ExchangeGateway, gateway_symbol_available
from services.execution.paper_signal import PaperSignalGenerator
from services.strategy_library import (
    AgentTaskRepository,
    ExecutionRepository,
    NotificationRepository,
    PaperRunRepository,
    ReviewRepository,
    StrategyRepository,
)
from shared.config import settings
from shared.models import (
    ExecutionOrderRequest,
    FailureRecord,
    OHLCVBar,
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
        gateway: ExchangeGateway | None = None,
    ) -> None:
        self.data_repo = data_repo
        self.execution_repo = execution_repo
        self.paper_repo = paper_repo
        self.strategy_repo = strategy_repo
        self.review_repo = review_repo
        self.gatekeeper = gatekeeper
        self.gateway = gateway
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
        runtime_timeframe = self._runtime_timeframe(strategy=strategy, request=request)
        actions: list[PaperRuntimeAction] = []
        metrics = dict(paper_run.paper_metrics_summary)
        protective_trailing = dict(metrics.get("protective_trailing", {}))
        processed_keys = set(metrics.get("processed_cycle_keys", []))
        new_processed_keys = list(processed_keys)
        realized_total = float(metrics.get("net_realized_pnl_total", metrics.get("realized_pnl_total", 0.0)))
        gross_realized_total = float(metrics.get("gross_realized_pnl_total", metrics.get("realized_pnl_total", 0.0)))
        estimated_fee_total = float(metrics.get("estimated_fee_total", 0.0))
        estimated_slippage_total = float(metrics.get("estimated_slippage_total", 0.0))
        daily_realized_pnl = float(metrics.get("daily_realized_pnl", 0.0))
        weekly_realized_pnl = float(metrics.get("weekly_realized_pnl", 0.0))
        consecutive_losses = int(metrics.get("consecutive_losses", 0))
        opened_positions = 0
        closed_positions = 0
        rejected_orders = 0
        skipped_symbols = 0

        for symbol in scanned_symbols:
            tradable_skip_reason = _fixed_universe_skip_reason(paper_run, symbol)
            if tradable_skip_reason is not None:
                skipped_symbols += 1
                cycle_key = f"{paper_run_id}:{symbol}:universe_status:{cycle_time.date().isoformat()}"
                actions.append(
                    PaperRuntimeAction(
                        symbol=symbol,
                        action="skip_untradable_symbol",
                        reason=tradable_skip_reason,
                        idempotency_key=cycle_key,
                        decision_trace={
                            "pipeline_status": "universe_status_rejected",
                            "rejection_reason": tradable_skip_reason,
                        },
                    )
                )
                continue
            latest_bar = self.data_repo.get_latest_ohlcv_bar(symbol=symbol, timeframe=runtime_timeframe)
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
            cycle_key = f"{paper_run_id}:{symbol}:{runtime_timeframe}:{latest_bar.timestamp.isoformat()}"
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

            lane = paper_run.execution_profile.get("strategy_lane", "directional")
            enable_veto = (
                request.enable_decision_veto
                and bool(paper_run.execution_profile.get("llm_veto_enabled", True))
                and lane != "carry"
            )
            base_order = self.signal_generator.generate_order(
                paper_run=paper_run,
                strategy=strategy,
                request=PaperRunStepRequest(
                    symbol=symbol,
                    timeframe=runtime_timeframe,
                    idempotency_key=cycle_key,
                    enable_decision_veto=enable_veto,
                ),
                positions=list(active_positions.values()),
            )
            decision_trace = dict(base_order.entry_context.get("decision_pipeline", {}))
            current_position = active_positions.get(symbol)
            if current_position is None and not bool(base_order.entry_context.get("paper_order_should_trade", True)):
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
            reference_price = float(latest_bar.close)

            if current_position is not None:
                levels = self._resolve_protective_levels(
                    paper_run=paper_run,
                    strategy=strategy,
                    position=current_position,
                    metrics=metrics,
                )
                if levels is not None:
                    levels = self._apply_trailing_ratchet(
                        paper_run=paper_run,
                        strategy=strategy,
                        position=current_position,
                        levels=levels,
                        bar=latest_bar,
                        trailing_state=protective_trailing,
                        cycle_time=cycle_time,
                    )
                    trigger = self._check_protective_trigger(
                        position=current_position,
                        levels=levels,
                        bar=latest_bar,
                    )
                    if trigger is not None:
                        close_order = self._close_order_request(
                            base_order=base_order,
                            current_position=current_position,
                            close_price=trigger.price,
                            close_reason=trigger.trigger_type,
                        )
                        order = self.gatekeeper.submit_order(close_order)
                        if order.execution_status == "accepted":
                            if self._should_execute_on_binance(paper_run, order=order):
                                order = self._ensure_binance_execution(
                                    paper_run=paper_run,
                                    order=order,
                                    order_request=close_order,
                                    position=current_position,
                                )
                                if order.execution_status != "accepted":
                                    rejected_orders += 1
                                    actions.append(
                                        PaperRuntimeAction(
                                            symbol=symbol,
                                            action="rejected",
                                            direction=current_position.side,
                                            reason=order.rejection_reason,
                                            order_execution_id=order.order_execution_id,
                                            reference_price=trigger.price,
                                            close_only=True,
                                            idempotency_key=cycle_key,
                                            decision_trace=decision_trace,
                                        )
                                    )
                                    continue
                            order = self._fill_order(order=order, cycle_time=cycle_time)
                            realized = self._close_position(
                                paper_run_id=paper_run_id,
                                position=current_position,
                                mark_price=trigger.price,
                                cycle_time=cycle_time,
                                strategy=strategy,
                            )
                            order = self._record_estimated_order_cost(
                                order=order,
                                strategy=strategy,
                                price=trigger.price,
                            )
                            if not self._should_execute_on_binance(paper_run):
                                self._maybe_mirror_to_gateway(
                                    paper_run=paper_run,
                                    order=order,
                                    order_request=close_order,
                                    position=current_position,
                                )
                            self._record_protective_outcome(
                                paper_run=paper_run,
                                order=order,
                                trigger=trigger,
                                position=current_position,
                                realized=realized.net_pnl,
                            )
                            realized_total += realized.net_pnl
                            gross_realized_total += realized.gross_pnl
                            estimated_fee_total += realized.fee_cost
                            estimated_slippage_total += realized.slippage_cost
                            daily_realized_pnl += realized.net_pnl
                            weekly_realized_pnl += realized.net_pnl
                            consecutive_losses = consecutive_losses + 1 if realized.net_pnl < 0 else 0
                            closed_positions += 1
                            active_positions.pop(symbol, None)
                            actions.append(
                                PaperRuntimeAction(
                                    symbol=symbol,
                                    action=f"{trigger.trigger_type}_close_{current_position.side}",
                                    direction=current_position.side,
                                    order_execution_id=order.order_execution_id,
                                    reference_price=trigger.price,
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
                                    reference_price=trigger.price,
                                    close_only=True,
                                    idempotency_key=cycle_key,
                                    decision_trace=decision_trace,
                                )
                            )
                        continue

                if not bool(base_order.entry_context.get("paper_order_should_trade", True)):
                    skipped_symbols += 1
                    actions.append(
                        PaperRuntimeAction(
                            symbol=symbol,
                            action="hold_long" if current_position.side == TradeSide.LONG else "hold_short",
                            direction=current_position.side,
                            reason=base_order.entry_context.get("decision_reason"),
                            reference_price=reference_price,
                            idempotency_key=cycle_key,
                            decision_trace=decision_trace,
                        )
                    )
                    self._mark_position(
                        paper_run_id=paper_run_id,
                        position=current_position,
                        mark_price=reference_price,
                        cycle_time=cycle_time,
                    )
                    continue

                if request.close_on_opposite_signal and current_position.side != base_order.direction:
                    close_order = self._close_order_request(
                        base_order=base_order,
                        current_position=current_position,
                        close_price=reference_price,
                        close_reason="opposite_signal",
                    )
                    order = self.gatekeeper.submit_order(close_order)
                    if order.execution_status == "accepted":
                        if self._should_execute_on_binance(paper_run, order=order):
                            order = self._ensure_binance_execution(
                                paper_run=paper_run,
                                order=order,
                                order_request=close_order,
                                position=current_position,
                            )
                            if order.execution_status != "accepted":
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
                        order = self._fill_order(order=order, cycle_time=cycle_time)
                        realized = self._close_position(
                            paper_run_id=paper_run_id,
                            position=current_position,
                            mark_price=reference_price,
                            cycle_time=cycle_time,
                            strategy=strategy,
                        )
                        order = self._record_estimated_order_cost(order=order, strategy=strategy, price=reference_price)
                        realized_total += realized.net_pnl
                        gross_realized_total += realized.gross_pnl
                        estimated_fee_total += realized.fee_cost
                        estimated_slippage_total += realized.slippage_cost
                        daily_realized_pnl += realized.net_pnl
                        weekly_realized_pnl += realized.net_pnl
                        consecutive_losses = consecutive_losses + 1 if realized.net_pnl < 0 else 0
                        closed_positions += 1
                        active_positions.pop(symbol, None)
                        if not self._should_execute_on_binance(paper_run):
                            self._maybe_mirror_to_gateway(
                                    paper_run=paper_run,
                                    order=order,
                                    order_request=close_order,
                                    position=current_position,
                                )
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

            if (
                self._should_execute_on_binance(paper_run)
                and self.gateway is not None
                and not gateway_symbol_available(gateway=self.gateway, symbol=symbol)
            ):
                skipped_symbols += 1
                actions.append(
                    PaperRuntimeAction(
                        symbol=symbol,
                        action="skip_unlisted_on_gateway",
                        direction=base_order.direction,
                        reason="symbol not listed on Binance Testnet gateway",
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

            if self._should_execute_on_binance(paper_run, order=order):
                order = self._ensure_binance_execution(
                    paper_run=paper_run,
                    order=order,
                    order_request=base_order,
                    position=None,
                )
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
            order = self._record_estimated_order_cost(
                order=order,
                strategy=strategy,
                price=position.entry_price,
            )
            if not self._should_execute_on_binance(paper_run):
                self._maybe_mirror_to_gateway(
                    paper_run=paper_run,
                    order=order,
                    order_request=base_order,
                    position=position,
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
        account_equity = self._initial_equity(paper_run) + realized_total
        equity_peak = max(float(metrics.get("equity_peak", self._initial_equity(paper_run))), account_equity)
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
            "net_realized_pnl_total": realized_total,
            "gross_realized_pnl_total": gross_realized_total,
            "estimated_fee_total": estimated_fee_total,
            "estimated_slippage_total": estimated_slippage_total,
            "daily_realized_pnl": daily_realized_pnl,
            "weekly_realized_pnl": weekly_realized_pnl,
            "consecutive_losses": consecutive_losses,
            "last_cycle_at": cycle_time.isoformat(),
            "last_scanned_symbols": scanned_symbols,
            "last_runtime_timeframe": runtime_timeframe,
            "last_action_counts": last_action_counts,
            "protective_trailing": protective_trailing,
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
        # Write back strategy-level paper_status — closes the state-machine gap
        # where paper_status was only updated on PaperRun, never on Strategy.
        # Wrapped so that a writeback failure (e.g. cross-session commit) never
        # interrupts the live trading cycle.
        if updated_run.strategy_id:
            with suppress(Exception):
                self.strategy_repo.update_lifecycle_status(
                    updated_run.strategy_id,
                    paper_status="running",
                )
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
    def _initial_equity(paper_run: PaperRun) -> float:
        return float(paper_run.execution_profile.get("account_equity") or 10_000.0)

    @staticmethod
    def _select_symbols(*, paper_run: PaperRun, request: PaperRuntimeCycleRequest) -> list[str]:
        base = request.symbols or paper_run.candidate_symbols or DEFAULT_BINANCE_TOP20
        deduped = list(dict.fromkeys(base))
        configured_max = int(paper_run.execution_profile.get("max_symbols") or request.max_symbols)
        return deduped[: min(request.max_symbols, configured_max)]

    @staticmethod
    def _runtime_timeframe(*, strategy: StrategyContract, request: PaperRuntimeCycleRequest) -> str:
        entry_timeframe = strategy.rules.entry_rules.get("entry_timeframe")
        return str(entry_timeframe or request.timeframe)

    @staticmethod
    def _close_order_request(
        *,
        base_order: ExecutionOrderRequest,
        current_position: PositionSnapshot,
        close_price: float,
        close_reason: str,
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
                    "paper_runtime_action": close_reason,
                    "reference_price": str(close_price),
                    "requested_notional": abs(current_position.quantity) * close_price,
                    "quantity": abs(current_position.quantity),
                },
                "stoploss_plan": {},
                "takeprofit_plan": {},
                "risk_state": risk_state,
            }
        )

    def _resolve_protective_levels(
        self,
        *,
        paper_run: PaperRun,
        strategy: StrategyContract,
        position: PositionSnapshot,
        metrics: dict[str, Any],
    ) -> ProtectiveLevels | None:
        if paper_run.paper_run_id is None:
            return None
        entry_order = self.execution_repo.find_latest_filled_entry_order(
            run_type="paper",
            run_id=paper_run.paper_run_id,
            symbol=position.symbol,
        )
        if entry_order is None:
            return None
        stop_price = _float_or_none(entry_order.stoploss_plan.get("price"))
        take_price = _float_or_none(entry_order.takeprofit_plan.get("price"))
        if stop_price is None and take_price is None:
            return None
        trailing = dict(metrics.get("protective_trailing", {})).get(position.symbol, {})
        trailed_stop = _float_or_none(trailing.get("stop_price")) if isinstance(trailing, dict) else None
        if trailed_stop is not None and stop_price is not None:
            if position.side == TradeSide.LONG and trailed_stop > stop_price:
                stop_price = trailed_stop
            if position.side == TradeSide.SHORT and trailed_stop < stop_price:
                stop_price = trailed_stop
        return ProtectiveLevels(
            stop_price=stop_price,
            take_price=take_price,
            original_stop_price=_float_or_none(entry_order.stoploss_plan.get("price")),
            entry_order_id=entry_order.order_execution_id,
            trail_after_r=_float_or_none(strategy.rules.takeprofit_rules.get("trail_after_r")),
        )

    def _apply_trailing_ratchet(
        self,
        *,
        paper_run: PaperRun,
        strategy: StrategyContract,
        position: PositionSnapshot,
        levels: ProtectiveLevels,
        bar: OHLCVBar,
        trailing_state: dict,
        cycle_time: datetime,
    ) -> ProtectiveLevels:
        if levels.stop_price is None or levels.original_stop_price is None or levels.trail_after_r is None:
            return levels
        initial_distance = abs(position.entry_price - levels.original_stop_price)
        if initial_distance <= 0:
            return levels
        if position.side == TradeSide.LONG:
            favorable_move = float(bar.high) - position.entry_price
            if favorable_move < levels.trail_after_r * initial_distance:
                return levels
            next_stop = max(levels.stop_price, position.entry_price)
            if next_stop <= levels.stop_price:
                return levels
        else:
            favorable_move = position.entry_price - float(bar.low)
            if favorable_move < levels.trail_after_r * initial_distance:
                return levels
            next_stop = min(levels.stop_price, position.entry_price)
            if next_stop >= levels.stop_price:
                return levels
        trailing_state[position.symbol] = {
            "stop_price": next_stop,
            "original_stop_price": levels.original_stop_price,
            "trail_after_r": levels.trail_after_r,
            "entry_price": position.entry_price,
            "updated_at": cycle_time.isoformat(),
            "strategy_id": strategy.strategy_id,
            "paper_run_id": paper_run.paper_run_id,
        }
        return ProtectiveLevels(
            stop_price=next_stop,
            take_price=levels.take_price,
            original_stop_price=levels.original_stop_price,
            entry_order_id=levels.entry_order_id,
            trail_after_r=levels.trail_after_r,
        )

    @staticmethod
    def _check_protective_trigger(
        *,
        position: PositionSnapshot,
        levels: ProtectiveLevels,
        bar: OHLCVBar,
    ) -> ProtectiveTrigger | None:
        if position.side == TradeSide.LONG:
            if levels.stop_price is not None and float(bar.low) <= levels.stop_price:
                return ProtectiveTrigger(trigger_type="stoploss", price=levels.stop_price)
            if levels.take_price is not None and float(bar.high) >= levels.take_price:
                return ProtectiveTrigger(trigger_type="takeprofit", price=levels.take_price)
        else:
            if levels.stop_price is not None and float(bar.high) >= levels.stop_price:
                return ProtectiveTrigger(trigger_type="stoploss", price=levels.stop_price)
            if levels.take_price is not None and float(bar.low) <= levels.take_price:
                return ProtectiveTrigger(trigger_type="takeprofit", price=levels.take_price)
        return None

    def _record_protective_outcome(
        self,
        *,
        paper_run: PaperRun,
        order: OrderExecution,
        trigger: ProtectiveTrigger,
        position: PositionSnapshot,
        realized: float,
    ) -> None:
        if trigger.trigger_type == "stoploss":
            if self.review_repo is None:
                return
            self.review_repo.create_failure(
                FailureRecord(
                    strategy_id=paper_run.strategy_id,
                    version_id=paper_run.version_id,
                    origin_run_type="paper",
                    origin_run_id=paper_run.paper_run_id or "",
                    failure_type="stoploss_triggered",
                    failure_summary=(
                        f"Protective stoploss closed {position.symbol} {position.side} at {trigger.price}"
                    ),
                    evidence_refs=[f"order_execution:{order.order_execution_id}"],
                    recommended_change=(
                        "Review stop distance, market regime, and strategy risk sizing before iteration."
                    ),
                )
            )
            return
        self.strategy_repo.append_iteration_event(
            paper_run.strategy_id,
            {
                "event_type": "takeprofit_triggered",
                "summary": f"Protective takeprofit closed {position.symbol} {position.side} at {trigger.price}",
                "paper_run_id": paper_run.paper_run_id,
                "order_execution_id": order.order_execution_id,
                "realized_pnl": realized,
            },
        )

    def _should_execute_on_binance(self, paper_run: PaperRun, *, order: OrderExecution | None = None) -> bool:
        execution_mode = str(paper_run.execution_profile.get("execution_mode", "paper_only"))
        legacy_mirror_enabled = bool(paper_run.execution_profile.get("mirror_to_gateway", False))
        enabled = (
            (execution_mode == "binance_simulation_first" or legacy_mirror_enabled)
            and bool(paper_run.execution_profile.get("cost_gate_verified", False))
            and settings.binance_auto_execute
            and settings.binance_use_testnet
            and not settings.live_trading_enabled
            and self.gateway is not None
        )
        if not enabled or order is None or order.close_only_mode:
            return enabled
        trace = order.entry_context.get("decision_pipeline", {})
        if not isinstance(trace, dict) or trace.get("strategy_lane") != "carry":
            return False
        estimated_net_edge_bps = _float_or_none(trace.get("estimated_net_edge_bps"))
        minimum_net_edge_bps = _float_or_none(trace.get("min_estimated_net_edge_bps"))
        return (
            estimated_net_edge_bps is not None
            and minimum_net_edge_bps is not None
            and estimated_net_edge_bps >= minimum_net_edge_bps
        )

    def _ensure_binance_execution(
        self,
        *,
        paper_run: PaperRun,
        order: OrderExecution,
        order_request: ExecutionOrderRequest,
        position: PositionSnapshot | None,
    ) -> OrderExecution:
        if not self._should_execute_on_binance(paper_run, order=order):
            return order
        gateway = self.gateway
        if gateway is None:
            return order
        try:
            mirror_request = self._gateway_order_request(order_request=order_request, position=position)
            gateway_result = gateway.submit_order(
                live_run_id=f"paper-testnet:{paper_run.paper_run_id or 'unknown'}",
                order_request=mirror_request,
            )
        except Exception as exc:  # noqa: BLE001
            self._record_gateway_mirror_failure(paper_run=paper_run, order=order, exc=exc)
            return (
                self.execution_repo.update_order(
                    order.order_execution_id or "",
                    execution_status="rejected",
                    rejection_reason=f"binance_auto_execute_failed: {exc}",
                    rejection_codes=[*order.rejection_codes, "binance_auto_execute_failed"],
                    gateway_status="gateway_failed",
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
        return (
            self.execution_repo.update_order(
                order.order_execution_id or "",
                entry_context={
                    **order.entry_context,
                    "protection_order_refs": gateway_result.get("protection_order_refs", []),
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

    def _maybe_mirror_to_gateway(
        self,
        *,
        paper_run: PaperRun,
        order: OrderExecution,
        order_request: ExecutionOrderRequest,
        position: PositionSnapshot,
    ) -> None:
        del paper_run, order, order_request, position
        # Testnet submission is gateway-first. The legacy post-fill mirror path
        # could submit an order even after the cost gate had rejected it.
        return

    @staticmethod
    def _gateway_order_request(
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
            quantity = abs(position.quantity)
            direction = TradeSide.SHORT if position.side == TradeSide.LONG else TradeSide.LONG
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

    @staticmethod
    def _gateway_mirror_request(
        *,
        order_request: ExecutionOrderRequest,
        position: PositionSnapshot,
    ) -> ExecutionOrderRequest:
        return PaperRuntimeService._gateway_order_request(order_request=order_request, position=position)

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

    def _fill_order(self, *, order: OrderExecution, cycle_time: datetime) -> OrderExecution:
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
        strategy: StrategyContract,
    ) -> RealizedOutcome:
        gross_pnl = _realized_pnl(position=position, mark_price=mark_price)
        entry_cost = _estimated_transaction_cost(
            price=position.entry_price,
            quantity=abs(position.quantity),
            strategy=strategy,
        )
        exit_cost = _estimated_transaction_cost(
            price=mark_price,
            quantity=abs(position.quantity),
            strategy=strategy,
        )
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
        return RealizedOutcome(
            gross_pnl=gross_pnl,
            fee_cost=entry_cost.fee_cost + exit_cost.fee_cost,
            slippage_cost=entry_cost.slippage_cost + exit_cost.slippage_cost,
        )

    def _record_estimated_order_cost(
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
        cost = _estimated_transaction_cost(price=price, quantity=quantity, strategy=strategy)
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


def _fixed_universe_skip_reason(paper_run: PaperRun, symbol: str) -> str | None:
    if paper_run.execution_profile.get("universe_mode") != "fixed_top20":
        return None
    for asset in paper_run.execution_profile.get("universe_assets", []) or []:
        if not isinstance(asset, dict):
            continue
        if asset.get("platform_symbol") != symbol:
            continue
        status = str(asset.get("tradable_status") or "unknown").lower()
        if status == "trading":
            return None
        return str(asset.get("reason") or f"Binance contract status is {status}")
    return None


@dataclass(frozen=True)
class ProtectiveLevels:
    stop_price: float | None
    take_price: float | None
    original_stop_price: float | None
    entry_order_id: str | None
    trail_after_r: float | None = None


@dataclass(frozen=True)
class ProtectiveTrigger:
    trigger_type: str
    price: float


@dataclass(frozen=True)
class EstimatedTransactionCost:
    fee_cost: float
    slippage_cost: float
    fee_bps: float
    slippage_bps: float

    def as_dict(self) -> dict[str, float]:
        return {
            "fee_cost": self.fee_cost,
            "slippage_cost": self.slippage_cost,
            "total_cost": self.fee_cost + self.slippage_cost,
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


def _estimated_transaction_cost(
    *,
    price: float,
    quantity: float,
    strategy: StrategyContract,
) -> EstimatedTransactionCost:
    entry_rules = strategy.rules.entry_rules
    fee_bps = float(entry_rules.get("fee_bps", 8.0))
    slippage_bps = float(entry_rules.get("slippage_bps", 6.0))
    notional = abs(price * quantity)
    return EstimatedTransactionCost(
        fee_cost=notional * fee_bps / 10_000,
        slippage_cost=notional * slippage_bps / 10_000,
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
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


def _parse_datetime(value: object) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    return None
