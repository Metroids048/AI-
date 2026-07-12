"""Autonomous paper-runtime cycles over validation-admitted strategies."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from services.data import DataRepository
from services.data.service import DEFAULT_BINANCE_TOP20
from services.execution.exit_ladder import (
    ExitLadderState,
    apply_level_fill,
    close_quantity_for_level,
    initialize_exit_ladder,
    ladder_config_from_rules,
    level_hit,
    level_trigger_price,
    next_pending_level,
)
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
        exit_ladder_metrics = dict(metrics.get("exit_ladder", {}))
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
        hard_drawdown_locked = self._is_hard_drawdown_locked(paper_run=paper_run, metrics=metrics)

        if self._gateway_mirror_armed(paper_run) and active_positions:
            reconcile_result = self._reconcile_local_positions_with_exchange(
                paper_run=paper_run,
                strategy=strategy,
                paper_run_id=paper_run_id,
                active_positions=active_positions,
                exit_ladder_metrics=exit_ladder_metrics,
                protective_trailing=protective_trailing,
                cycle_time=cycle_time,
            )
            actions.extend(reconcile_result["actions"])
            closed_positions += int(reconcile_result["closed"])
            realized_total += float(reconcile_result["net_pnl"])
            gross_realized_total += float(reconcile_result["gross_pnl"])
            estimated_fee_total += float(reconcile_result["fee_cost"])
            estimated_slippage_total += float(reconcile_result["slippage_cost"])
            daily_realized_pnl += float(reconcile_result["net_pnl"])
            weekly_realized_pnl += float(reconcile_result["net_pnl"])

        for symbol in scanned_symbols:
            current_position = active_positions.get(symbol)
            if hard_drawdown_locked and current_position is not None:
                protection_bar = self.data_repo.get_latest_ohlcv_bar(symbol=symbol, timeframe="1m")
                if protection_bar is not None:
                    close_order = self._close_order_request(
                        base_order=self._protection_order_request(
                            paper_run=paper_run,
                            strategy=strategy,
                            position=current_position,
                        ),
                        current_position=current_position,
                        close_price=float(protection_bar.close),
                        close_reason="hard_drawdown",
                    )
                    order = self.gatekeeper.submit_order(close_order)
                    if order.execution_status == "accepted":
                        order = self._fill_order(order=order, cycle_time=cycle_time)
                        realized = self._close_position(
                            paper_run_id=paper_run_id,
                            position=current_position,
                            mark_price=float(protection_bar.close),
                            cycle_time=cycle_time,
                            strategy=strategy,
                        )
                        realized_total += realized.net_pnl
                        gross_realized_total += realized.gross_pnl
                        estimated_fee_total += realized.fee_cost
                        estimated_slippage_total += realized.slippage_cost
                        daily_realized_pnl += realized.net_pnl
                        weekly_realized_pnl += realized.net_pnl
                        closed_positions += 1
                        active_positions.pop(symbol, None)
                        actions.append(
                            PaperRuntimeAction(
                                symbol=symbol,
                                action=f"hard_drawdown_close_{current_position.side}",
                                direction=current_position.side,
                                order_execution_id=order.order_execution_id,
                                reference_price=float(protection_bar.close),
                                close_only=True,
                                decision_trace={"exit_reason": "hard_drawdown_lock"},
                            )
                        )
                continue
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
            protection_bar = self.data_repo.get_latest_ohlcv_bar(symbol=symbol, timeframe="1m")
            if current_position is not None and protection_bar is not None:
                metrics["exit_ladder"] = exit_ladder_metrics
                ladder = self._ensure_exit_ladder(
                    paper_run=paper_run,
                    strategy=strategy,
                    position=current_position,
                    exit_ladder_metrics=exit_ladder_metrics,
                )
                if ladder is not None:
                    pending = next_pending_level(ladder)
                    if pending is not None and level_hit(
                        state=ladder,
                        level=pending,
                        bar_high=float(protection_bar.high),
                        bar_low=float(protection_bar.low),
                    ):
                        trigger_price = level_trigger_price(ladder, pending)
                        close_abs = close_quantity_for_level(ladder, pending)
                        sign = 1.0 if current_position.side == TradeSide.LONG else -1.0
                        partial_quantity = sign * close_abs
                        remaining_quantity = current_position.quantity - partial_quantity
                        predicted_ladder = apply_level_fill(
                            ladder,
                            level=pending,
                            trigger_price=trigger_price,
                            closed_quantity=close_abs,
                        )
                        close_order = self._close_order_request(
                            base_order=self._protection_order_request(
                                paper_run=paper_run,
                                strategy=strategy,
                                position=current_position,
                            ),
                            current_position=current_position,
                            close_price=trigger_price,
                            close_reason="exit_ladder_partial",
                            close_quantity=abs(partial_quantity),
                        )
                        close_order = close_order.model_copy(
                            update={
                                "stoploss_plan": {"price": predicted_ladder.current_stop_price},
                                "entry_context": {
                                    **close_order.entry_context,
                                    "remaining_quantity": predicted_ladder.remaining_quantity,
                                    "refresh_protection": True,
                                    "protection_stop_price": predicted_ladder.current_stop_price,
                                    "open_side": current_position.side.value
                                    if hasattr(current_position.side, "value")
                                    else str(current_position.side),
                                },
                            }
                        )
                        order = self.gatekeeper.submit_order(close_order)
                        if order.execution_status == "accepted":
                            if self._should_execute_on_binance(paper_run, order=order):
                                order = self._ensure_binance_execution(
                                    paper_run=paper_run,
                                    order=order,
                                    order_request=close_order,
                                    position=current_position,
                                    refresh_protection=True,
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
                                            reference_price=trigger_price,
                                            close_only=True,
                                        )
                                    )
                                    continue
                            order = self._fill_order(order=order, cycle_time=cycle_time)
                            partial_position = current_position.model_copy(update={"quantity": partial_quantity})
                            realized = self._close_position(
                                paper_run_id=paper_run_id,
                                position=partial_position,
                                mark_price=trigger_price,
                                cycle_time=cycle_time,
                                strategy=strategy,
                                remaining_quantity=remaining_quantity,
                            )
                            self._record_estimated_order_cost(order=order, strategy=strategy, price=trigger_price)
                            if not self._should_execute_on_binance(paper_run):
                                self._maybe_mirror_to_gateway(
                                    paper_run=paper_run,
                                    order=order,
                                    order_request=close_order,
                                    position=current_position,
                                )
                            realized_total += realized.net_pnl
                            gross_realized_total += realized.gross_pnl
                            estimated_fee_total += realized.fee_cost
                            estimated_slippage_total += realized.slippage_cost
                            updated_ladder = predicted_ladder
                            exit_ladder_metrics[symbol] = updated_ladder.as_dict()
                            metrics["exit_ladder"] = exit_ladder_metrics
                            remaining = current_position.model_copy(
                                update={"quantity": remaining_quantity, "mark_price": trigger_price}
                            )
                            active_positions[symbol] = remaining
                            protective_trailing[symbol] = {
                                "stop_price": updated_ladder.current_stop_price,
                                "original_stop_price": updated_ladder.initial_stop_price,
                                "entry_price": current_position.entry_price,
                                "updated_at": cycle_time.isoformat(),
                                "exit_ladder_level": pending.r_multiple,
                            }
                            actions.append(
                                PaperRuntimeAction(
                                    symbol=symbol,
                                    action=f"exit_ladder_partial_{current_position.side}",
                                    direction=current_position.side,
                                    order_execution_id=order.order_execution_id,
                                    reference_price=trigger_price,
                                    close_only=True,
                                    decision_trace={
                                        "exit_ladder_r": pending.r_multiple,
                                        "close_fraction": pending.close_fraction,
                                        "remaining_quantity": abs(remaining_quantity),
                                        "protection_timeframe": "1m",
                                    },
                                )
                            )
                            continue
                levels = self._resolve_protective_levels(
                    paper_run=paper_run,
                    strategy=strategy,
                    position=current_position,
                    metrics=metrics,
                    exit_ladder=ladder,
                )
                if levels is not None:
                    levels = self._apply_trailing_ratchet(
                        paper_run=paper_run,
                        strategy=strategy,
                        position=current_position,
                        levels=levels,
                        bar=protection_bar,
                        trailing_state=protective_trailing,
                        cycle_time=cycle_time,
                    )
                    trigger = self._check_protective_trigger(
                        position=current_position,
                        levels=levels,
                        bar=protection_bar,
                    )
                    if trigger is not None:
                        partial_fraction = (
                            None
                            if ladder is not None
                            else self._partial_takeprofit_fraction(
                                strategy=strategy,
                                trigger=trigger,
                                position=current_position,
                                levels=levels,
                            )
                        )
                        if partial_fraction is not None:
                            partial_quantity = current_position.quantity * partial_fraction
                            partial_position = current_position.model_copy(update={"quantity": partial_quantity})
                            realized = self._close_position(
                                paper_run_id=paper_run_id,
                                position=partial_position,
                                mark_price=trigger.price,
                                cycle_time=cycle_time,
                                strategy=strategy,
                                remaining_quantity=current_position.quantity - partial_quantity,
                            )
                            realized_total += realized.net_pnl
                            gross_realized_total += realized.gross_pnl
                            estimated_fee_total += realized.fee_cost
                            estimated_slippage_total += realized.slippage_cost
                            remaining = current_position.model_copy(
                                update={
                                    "quantity": current_position.quantity - partial_quantity,
                                    "mark_price": trigger.price,
                                }
                            )
                            active_positions[symbol] = remaining
                            protective_trailing[symbol] = {
                                "stop_price": current_position.entry_price,
                                "original_stop_price": levels.original_stop_price,
                                "entry_price": current_position.entry_price,
                                "updated_at": cycle_time.isoformat(),
                                "partial_takeprofit_done": True,
                            }
                            actions.append(
                                PaperRuntimeAction(
                                    symbol=symbol,
                                    action=f"partial_takeprofit_{current_position.side}",
                                    direction=current_position.side,
                                    reference_price=trigger.price,
                                    close_only=True,
                                    decision_trace={
                                        "partial_close_fraction": partial_fraction,
                                        "protection_timeframe": "1m",
                                    },
                                )
                            )
                            continue
                        close_order = self._close_order_request(
                            base_order=self._protection_order_request(
                                paper_run=paper_run,
                                strategy=strategy,
                                position=current_position,
                            ),
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
                            self._record_estimated_order_cost(order=order, strategy=strategy, price=trigger.price)
                            realized_total += realized.net_pnl
                            gross_realized_total += realized.gross_pnl
                            estimated_fee_total += realized.fee_cost
                            estimated_slippage_total += realized.slippage_cost
                            daily_realized_pnl += realized.net_pnl
                            weekly_realized_pnl += realized.net_pnl
                            consecutive_losses = consecutive_losses + 1 if realized.net_pnl < 0 else 0
                            closed_positions += 1
                            active_positions.pop(symbol, None)
                            exit_ladder_metrics.pop(symbol, None)
                            protective_trailing.pop(symbol, None)
                            actions.append(
                                PaperRuntimeAction(
                                    symbol=symbol,
                                    action=f"{trigger.trigger_type}_close_{current_position.side}",
                                    direction=current_position.side,
                                    order_execution_id=order.order_execution_id,
                                    reference_price=trigger.price,
                                    close_only=True,
                                    decision_trace={"protection_timeframe": "1m"},
                                )
                            )
                            continue
                    if self._should_time_exit(
                        strategy=strategy,
                        position=current_position,
                        levels=levels,
                        bar=protection_bar,
                        cycle_time=cycle_time,
                    ):
                        close_order = self._close_order_request(
                            base_order=self._protection_order_request(
                                paper_run=paper_run,
                                strategy=strategy,
                                position=current_position,
                            ),
                            current_position=current_position,
                            close_price=float(protection_bar.close),
                            close_reason="time_exit",
                        )
                        order = self.gatekeeper.submit_order(close_order)
                        if order.execution_status == "accepted":
                            order = self._fill_order(order=order, cycle_time=cycle_time)
                            realized = self._close_position(
                                paper_run_id=paper_run_id,
                                position=current_position,
                                mark_price=float(protection_bar.close),
                                cycle_time=cycle_time,
                                strategy=strategy,
                            )
                            self._record_estimated_order_cost(
                                order=order,
                                strategy=strategy,
                                price=float(protection_bar.close),
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
                                    action=f"time_exit_close_{current_position.side}",
                                    direction=current_position.side,
                                    order_execution_id=order.order_execution_id,
                                    reference_price=float(protection_bar.close),
                                    close_only=True,
                                    decision_trace={"protection_timeframe": "1m", "exit_reason": "time_exit"},
                                )
                            )
                            continue
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
            # Entry evaluation is idempotent per closed entry candle. Existing
            # exposure must still pass through protective management on every
            # scheduler cycle, otherwise a duplicated entry candle can defer a
            # stop indefinitely.
            if cycle_key in processed_keys and current_position is None:
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
            ladder_state = initialize_exit_ladder(
                symbol=position.symbol,
                side=position.side,
                entry_price=position.entry_price,
                quantity=abs(position.quantity),
                stop_price=float(order.stoploss_plan.get("price") or 0.0),
                takeprofit_rules=strategy.rules.takeprofit_rules,
            )
            if ladder_state is not None:
                exit_ladder_metrics[position.symbol] = ladder_state.as_dict()
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
            "exit_ladder": exit_ladder_metrics,
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
            paper_status="locked" if hard_drawdown_locked else "running",
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

    def _is_hard_drawdown_locked(self, *, paper_run: PaperRun, metrics: dict[str, Any]) -> bool:
        account_equity = float(metrics.get("account_equity") or self._initial_equity(paper_run))
        equity_peak = float(metrics.get("equity_peak") or account_equity)
        if equity_peak <= 0:
            return False
        profile_id = paper_run.execution_profile.get("risk_profile_id")
        profile = self.gatekeeper.risk_profile_repo.get_profile(profile_id) if profile_id else None
        hard_limit = float(profile.hard_stop_drawdown_limit) if profile is not None else 0.20
        return (equity_peak - account_equity) / equity_peak >= hard_limit

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
        close_quantity: float | None = None,
    ) -> ExecutionOrderRequest:
        quantity = abs(current_position.quantity) if close_quantity is None else abs(close_quantity)
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
                    "requested_notional": quantity * close_price,
                    "quantity": quantity,
                    "reduce_only": True,
                },
                "stoploss_plan": {},
                "takeprofit_plan": {},
                "risk_state": risk_state,
            }
        )

    def _gateway_mirror_armed(self, paper_run: PaperRun) -> bool:
        execution_mode = str(paper_run.execution_profile.get("execution_mode", "paper_only"))
        legacy_mirror_enabled = bool(paper_run.execution_profile.get("mirror_to_gateway", False))
        return (
            self.gateway is not None
            and (execution_mode == "binance_simulation_first" or legacy_mirror_enabled)
            and bool(paper_run.execution_profile.get("cost_gate_verified", False))
        )

    def _reconcile_local_positions_with_exchange(
        self,
        *,
        paper_run: PaperRun,
        strategy: StrategyContract,
        paper_run_id: str,
        active_positions: dict[str, PositionSnapshot],
        exit_ladder_metrics: dict[str, Any],
        protective_trailing: dict[str, Any],
        cycle_time: datetime,
    ) -> dict[str, Any]:
        empty = {
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
        exchange_qty_by_platform: dict[str, float] = {}
        for item in snapshot.get("open_positions", []) or []:
            if not isinstance(item, dict):
                continue
            raw_symbol = str(item.get("symbol") or "")
            platform_symbol = raw_symbol.replace(":USDT", "")
            exchange_qty_by_platform[platform_symbol] = abs(float(item.get("contracts") or 0.0))
        actions: list[PaperRuntimeAction] = []
        closed = 0
        net_pnl = 0.0
        gross_pnl = 0.0
        fee_cost = 0.0
        slippage_cost = 0.0
        for symbol, position in list(active_positions.items()):
            exchange_qty = exchange_qty_by_platform.get(symbol, 0.0)
            if exchange_qty > 0:
                continue
            mark_price = float(position.mark_price or position.entry_price)
            realized = self._close_position(
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

    def _ensure_exit_ladder(
        self,
        *,
        paper_run: PaperRun,
        strategy: StrategyContract,
        position: PositionSnapshot,
        exit_ladder_metrics: dict[str, Any],
    ) -> ExitLadderState | None:
        from services.execution.exit_ladder import exit_ladder_from_dict

        existing = exit_ladder_metrics.get(position.symbol)
        if isinstance(existing, dict):
            state = exit_ladder_from_dict(existing)
            if abs(state.remaining_quantity - abs(position.quantity)) > 1e-9:
                state = ExitLadderState(
                    symbol=state.symbol,
                    side=state.side,
                    entry_price=state.entry_price,
                    original_quantity=state.original_quantity,
                    remaining_quantity=abs(position.quantity),
                    initial_stop_price=state.initial_stop_price,
                    current_stop_price=state.current_stop_price,
                    levels=state.levels,
                    remainder_trail_after_r=state.remainder_trail_after_r,
                    locked_level1_price=state.locked_level1_price,
                )
                exit_ladder_metrics[position.symbol] = state.as_dict()
            return state
        if ladder_config_from_rules(strategy.rules.takeprofit_rules) is None:
            return None
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
        if stop_price is None:
            return None
        initialized = initialize_exit_ladder(
            symbol=position.symbol,
            side=position.side,
            entry_price=position.entry_price,
            quantity=abs(position.quantity),
            stop_price=stop_price,
            takeprofit_rules=strategy.rules.takeprofit_rules,
        )
        if initialized is not None:
            exit_ladder_metrics[position.symbol] = initialized.as_dict()
        return initialized

    def _resolve_protective_levels(
        self,
        *,
        paper_run: PaperRun,
        strategy: StrategyContract,
        position: PositionSnapshot,
        metrics: dict[str, Any],
        exit_ladder=None,
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
        original_stop = stop_price
        trail_after_r = _float_or_none(strategy.rules.takeprofit_rules.get("trail_after_r"))
        if exit_ladder is not None:
            stop_price = exit_ladder.current_stop_price
            original_stop = exit_ladder.initial_stop_price
            # Ladder levels replace fixed take; remainder uses trail only.
            take_price = None
            trail_after_r = (
                exit_ladder.remainder_trail_after_r if exit_ladder.all_levels_executed else None
            )
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
            original_stop_price=original_stop,
            entry_order_id=entry_order.order_execution_id,
            trail_after_r=trail_after_r,
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
        if not isinstance(trace, dict):
            return False
        if trace.get("strategy_lane") != "carry":
            return bool(trace.get("pipeline_status")) and not order.rejection_codes
        estimated_net_edge_bps = _float_or_none(trace.get("estimated_net_edge_bps"))
        minimum_net_edge_bps = _float_or_none(trace.get("min_estimated_net_edge_bps"))
        return (
            estimated_net_edge_bps is not None
            and minimum_net_edge_bps is not None
            and estimated_net_edge_bps >= minimum_net_edge_bps
        )

    @staticmethod
    def _should_time_exit(
        *,
        strategy: StrategyContract,
        position: PositionSnapshot,
        levels: ProtectiveLevels,
        bar: OHLCVBar,
        cycle_time: datetime,
    ) -> bool:
        exit_rules = strategy.rules.exit_rules
        hours = _float_or_none(exit_rules.get("time_exit_hours"))
        min_r = _float_or_none(exit_rules.get("time_exit_min_r"))
        if hours is None or min_r is None or levels.original_stop_price is None:
            return False
        snapshot_time = position.snapshot_time
        if snapshot_time.tzinfo is None:
            snapshot_time = snapshot_time.replace(tzinfo=UTC)
        age_hours = (cycle_time - snapshot_time).total_seconds() / 3600
        initial_risk = abs(position.entry_price - levels.original_stop_price)
        if initial_risk <= 0 or age_hours < hours:
            return False
        favorable_move = (
            float(bar.close) - position.entry_price
            if position.side == TradeSide.LONG
            else position.entry_price - float(bar.close)
        )
        return favorable_move < min_r * initial_risk

    @staticmethod
    def _partial_takeprofit_fraction(
        *,
        strategy: StrategyContract,
        trigger: ProtectiveTrigger,
        position: PositionSnapshot,
        levels: ProtectiveLevels,
    ) -> float | None:
        if trigger.trigger_type != "takeprofit" or levels.original_stop_price is None:
            return None
        fraction = _float_or_none(strategy.rules.takeprofit_rules.get("partial_close_fraction"))
        if fraction is None or not 0 < fraction < 1:
            return None
        partial_r = _float_or_none(strategy.rules.takeprofit_rules.get("partial_take_profit_r"))
        target_r = (
            partial_r
            if partial_r is not None
            else _float_or_none(strategy.rules.takeprofit_rules.get("risk_reward"))
        )
        initial_risk = abs(position.entry_price - levels.original_stop_price)
        if target_r is None or initial_risk <= 0:
            return None
        expected_price = (
            position.entry_price + target_r * initial_risk
            if position.side == TradeSide.LONG
            else position.entry_price - target_r * initial_risk
        )
        reached = (
            trigger.price >= expected_price
            if position.side == TradeSide.LONG
            else trigger.price <= expected_price
        )
        return fraction if reached else None

    @staticmethod
    def _protection_order_request(
        *,
        paper_run: PaperRun,
        strategy: StrategyContract,
        position: PositionSnapshot,
    ) -> ExecutionOrderRequest:
        return ExecutionOrderRequest(
            strategy_id=paper_run.strategy_id,
            version_id=paper_run.version_id,
            symbol=position.symbol,
            direction=position.side,
            entry_context={"timeframe": "1m", "paper_order_should_trade": True},
            validation_backtest_run_id=paper_run.gate_decision_ref,
            risk_profile_id=paper_run.execution_profile.get("risk_profile_id"),
            paper_run_id=paper_run.paper_run_id,
        )

    def _ensure_binance_execution(
        self,
        *,
        paper_run: PaperRun,
        order: OrderExecution,
        order_request: ExecutionOrderRequest,
        position: PositionSnapshot | None,
        refresh_protection: bool = False,
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
            if refresh_protection or bool(order_request.entry_context.get("refresh_protection")):
                remaining = float(order_request.entry_context.get("remaining_quantity") or 0.0)
                stop_price = order_request.entry_context.get("protection_stop_price")
                refresh = getattr(gateway, "refresh_protection_orders", None)
                if not callable(refresh):
                    raise ValueError("gateway_protection_refresh_unsupported")
                if remaining <= 0 or stop_price is None:
                    raise ValueError("gateway_protection_refresh_missing_levels")
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
            if bool(order.close_only_mode) and _is_reduce_only_already_flat(exc):
                self._record_gateway_mirror_failure(paper_run=paper_run, order=order, exc=exc)
                return (
                    self.execution_repo.update_order(
                        order.order_execution_id or "",
                        execution_status="accepted",
                        rejection_reason=None,
                        rejection_codes=[
                            code
                            for code in order.rejection_codes
                            if code != "binance_auto_execute_failed"
                        ],
                        gateway_status="exchange_already_flat",
                        entry_context={
                            **order.entry_context,
                            "exchange_already_flat": True,
                            "gateway_flat_error": str(exc),
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
            context_qty = float(context.get("quantity") or 0.0)
            quantity = context_qty if context_qty > 0 else abs(position.quantity)
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
        remaining_quantity: float = 0.0,
    ) -> RealizedOutcome:
        gross_pnl = _realized_pnl(position=position, mark_price=mark_price)
        entry_cost = _estimated_transaction_cost(
            price=position.entry_price,
            quantity=abs(position.quantity),
            strategy=strategy,
            symbol=position.symbol,
        )
        exit_cost = _estimated_transaction_cost(
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
        cost = _estimated_transaction_cost(price=price, quantity=quantity, strategy=strategy, symbol=order.symbol)
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


def _estimated_transaction_cost(
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


def _parse_datetime(value: object) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    return None
