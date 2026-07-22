"""Autonomous paper-trading cycle orchestration — owns the full cycle logic."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from services.data import DataRepository
from services.data.service import DEFAULT_BINANCE_TOP20
from services.execution.account_equity import sync_paper_account_equity
from services.execution.cross_sectional import CrossSectionalRankEntry, compute_funding_rank_snapshot
from services.execution.decision_engine import DecisionEngine
from services.execution.exit_ladder import (
    ExitLadderState,
    apply_level_fill,
    close_quantity_for_level,
    initialize_exit_ladder,
    ladder_config_from_rules,
    level_hit,
    level_trigger_price,
    next_pending_level,
    next_trailed_stop_price,
)
from services.execution.gatekeeper import ExecutionGatekeeperService
from services.execution.gateway import ExchangeGateway, gateway_symbol_available
from services.execution.paper_exchange_execution import PaperExchangeExecutionService
from services.execution.paper_order_lifecycle import (
    EstimatedTransactionCost,
    PaperOrderLifecycleService,
    RealizedOutcome,
    estimated_transaction_cost,
    realized_pnl,
)
from services.execution.paper_signal import PaperSignalGenerator
from services.strategy_library import (
    ConfigSnapshotRepository,
    DecisionSnapshotRepository,
    ExecutionRepository,
    PaperRunRepository,
    ReviewRepository,
    StrategyRepository,
)
from shared.config import settings
from shared.models import (
    ConfigSnapshot,
    DecisionSnapshot,
    ExchangeSide,
    ExecutionOrderRequest,
    FailureRecord,
    MarketRegime,
    OHLCVBar,
    OrderExecution,
    PaperRun,
    PaperRunStepRequest,
    PaperRuntimeAction,
    PaperRuntimeCycleRequest,
    PaperRuntimeCycleResult,
    PortfolioDecision,
    PositionSide,
    PositionSnapshot,
    ProtectionPolicy,
    RuntimeMode,
    StrategyContract,
    StrategyRules,
    StrategySignal,
    TradeAction,
    TradeSide,
)


class PaperCycleOrchestrator:
    """Owns the complete paper-runtime cycle: signal → gatekeeper → execution → metrics."""

    def __init__(
        self,
        *,
        data_repo: DataRepository,
        execution_repo: ExecutionRepository,
        paper_repo: PaperRunRepository,
        strategy_repo: StrategyRepository,
        gatekeeper: ExecutionGatekeeperService,
        gateway: ExchangeGateway | None = None,
        exchange_execution: PaperExchangeExecutionService,
        order_lifecycle: PaperOrderLifecycleService,
        signal_generator: PaperSignalGenerator,
        decision_snapshot_repo: DecisionSnapshotRepository,
        review_repo: ReviewRepository | None = None,
    ) -> None:
        self.data_repo = data_repo
        self.execution_repo = execution_repo
        self.paper_repo = paper_repo
        self.strategy_repo = strategy_repo
        self.gatekeeper = gatekeeper
        self.gateway = gateway
        self.exchange_execution = exchange_execution
        self.order_lifecycle = order_lifecycle
        self.signal_generator = signal_generator
        self.decision_snapshot_repo = decision_snapshot_repo
        self.review_repo = review_repo

    def run_cycle(
        self,
        *,
        paper_run_id: str,
        request: PaperRuntimeCycleRequest,
    ) -> PaperRuntimeCycleResult:
        return self._run_cycle(paper_run_id, request)

    def _run_cycle(self, paper_run_id: str, request: PaperRuntimeCycleRequest) -> PaperRuntimeCycleResult:
        paper_run = self.paper_repo.get_paper_run(paper_run_id)
        if paper_run is None:
            raise ValueError("paper run not found")
        cycle_time = datetime.now(UTC)
        config_repo = ConfigSnapshotRepository(self.execution_repo.session)
        config_repo.activate_pending(paper_run_id, cycle_id=cycle_time.isoformat())
        paper_run = self.paper_repo.get_paper_run(paper_run_id) or paper_run
        strategy = self._require_strategy(paper_run.strategy_id)
        active_config = config_repo.get_active(paper_run_id)
        if active_config is not None:
            execution_profile = active_config.config.get("execution_profile")
            if isinstance(execution_profile, dict):
                paper_run = paper_run.model_copy(update={"execution_profile": execution_profile})
            strategy_rules = active_config.config.get("strategy_rules")
            if isinstance(strategy_rules, dict):
                strategy = strategy.model_copy(update={"rules": StrategyRules(**strategy_rules)})
        current_positions = self.execution_repo.list_latest_positions_for_run(
            run_type="paper",
            run_id=paper_run_id,
        )
        active_positions = {position.symbol: position for position in current_positions}
        scanned_symbols = self._select_symbols(paper_run=paper_run, request=request)
        runtime_timeframe = self._runtime_timeframe(strategy=strategy, request=request)
        actions: list[PaperRuntimeAction] = []
        metrics = dict(paper_run.paper_metrics_summary)
        metrics = sync_paper_account_equity(
            paper_run=paper_run,
            metrics=metrics,
            execution_repo=self.execution_repo,
            gateway=self.gateway,
            prefer_exchange=self._gateway_mirror_armed(paper_run),
            paper_run_id=paper_run_id,
        )
        paper_run = paper_run.model_copy(update={"paper_metrics_summary": metrics})
        protective_trailing = dict(metrics.get("protective_trailing", {}))
        exit_ladder_metrics = dict(metrics.get("exit_ladder", {}))
        reconcile_missing_counts = {
            str(symbol): int(count)
            for symbol, count in dict(metrics.get("exchange_missing_position_counts", {})).items()
        }
        processed_keys = set(metrics.get("processed_cycle_keys", []))
        new_processed_keys = list(processed_keys)
        realized_total = float(metrics.get("net_realized_pnl_total", metrics.get("realized_pnl_total", 0.0)))
        realized_total_at_cycle_start = realized_total
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

        actions.extend(self._expire_pending_limit_entries(paper_run=paper_run, cycle_time=cycle_time))

        if self._gateway_mirror_armed(paper_run):
            reconcile_result = self.exchange_execution.reconcile_local_positions_with_exchange(
                paper_run=paper_run,
                strategy=strategy,
                paper_run_id=paper_run_id,
                active_positions=active_positions,
                exit_ladder_metrics=exit_ladder_metrics,
                protective_trailing=protective_trailing,
                reconcile_missing_counts=reconcile_missing_counts,
                cycle_time=cycle_time,
                close_position_fn=self._close_position,
            )
            actions.extend(reconcile_result["actions"])
            closed_positions += int(reconcile_result["closed"])
            realized_total += float(reconcile_result["net_pnl"])
            gross_realized_total += float(reconcile_result["gross_pnl"])
            estimated_fee_total += float(reconcile_result["fee_cost"])
            estimated_slippage_total += float(reconcile_result["slippage_cost"])
            daily_realized_pnl += float(reconcile_result["net_pnl"])
            weekly_realized_pnl += float(reconcile_result["net_pnl"])

        cross_sectional_snapshot: dict[str, CrossSectionalRankEntry] = {}
        if paper_run.execution_profile.get("strategy_lane") == "cross_sectional_carry":
            basket_size = int(strategy.rules.entry_rules.get("basket_size", 3))
            cross_sectional_snapshot = compute_funding_rank_snapshot(
                data_repo=self.data_repo,
                symbols=scanned_symbols,
                basket_size=basket_size,
            )

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
                                order = self.exchange_execution.ensure_binance_execution(
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
                                order = self.exchange_execution.ensure_binance_execution(
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
                and lane not in {"carry", "cross_sectional_carry"}
            )
            rank_entry = cross_sectional_snapshot.get(symbol)
            base_order = self.signal_generator.generate_order(
                paper_run=paper_run,
                strategy=strategy,
                request=PaperRunStepRequest(
                    symbol=symbol,
                    timeframe=runtime_timeframe,
                    idempotency_key=cycle_key,
                    enable_decision_veto=enable_veto,
                    cross_sectional_rank=(
                        {
                            "basket_side": rank_entry.basket_side,
                            "funding_rate_bps": rank_entry.funding_rate_bps,
                            "rank": rank_entry.rank,
                            "total_ranked": rank_entry.total_ranked,
                        }
                        if rank_entry is not None
                        else None
                    ),
                ),
                positions=list(active_positions.values()),
            )
            base_order = base_order.model_copy(
                update={
                    "order_origin": "live_scheduler",
                    "run_mode": request.run_mode,
                    "deployment_sha": request.deployment_sha,
                    "scheduler_instance_id": request.scheduler_instance_id,
                    "process_id": request.process_id,
                    "worker_id": request.worker_id,
                    "container_id": request.container_id,
                    "cycle_source": request.cycle_source,
                    "scheduled_for": request.scheduled_for,
                }
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
                                order = self.exchange_execution.ensure_binance_execution(
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

                rank_dropout = "outside_funding_basket" in decision_trace.get("rejection_reasons", [])
                if not rank_dropout and not bool(base_order.entry_context.get("paper_order_should_trade", True)):
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

                if rank_dropout or (request.close_on_opposite_signal and current_position.side != base_order.direction):
                    close_order = self._close_order_request(
                        base_order=base_order,
                        current_position=current_position,
                        close_price=reference_price,
                        close_reason="rank_dropout" if rank_dropout else "opposite_signal",
                    )
                    order = self.gatekeeper.submit_order(close_order)
                    if order.execution_status == "accepted":
                        if self._should_execute_on_binance(paper_run, order=order):
                            order = self.exchange_execution.ensure_binance_execution(
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

            base_order = self._with_open_trade_intent(
                base_order=base_order,
                cycle_id=cycle_key,
                active_config=active_config,
                reference_price=Decimal(str(reference_price)),
                decision_candle_close_time=latest_bar.timestamp,
            )
            order = self.gatekeeper.submit_order(base_order)
            if order.execution_status != "accepted":
                retryable_rejections = {"data_not_fresh", "blocking_risk_event"}
                if order.rejection_codes and set(order.rejection_codes).issubset(retryable_rejections):
                    with suppress(ValueError):
                        new_processed_keys.remove(cycle_key)
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
                order = self.exchange_execution.ensure_binance_execution(
                    paper_run=paper_run,
                    order=order,
                    order_request=base_order,
                    position=None,
                )
                if order.execution_status in {"submitted", "open"}:
                    skipped_symbols += 1
                    actions.append(
                        PaperRuntimeAction(
                            symbol=symbol,
                            action="pending_gateway_fill",
                            direction=base_order.direction,
                            reason="Binance entry accepted but not filled; local position remains flat",
                            order_execution_id=order.order_execution_id,
                            reference_price=reference_price,
                            idempotency_key=cycle_key,
                            decision_trace=decision_trace,
                        )
                    )
                    continue
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

            # Delta-neutral hedge leg execution for carry strategy
            hedge_leg_info = decision_trace.get("hedge_leg")
            hedge_order = None
            hedge_position = None
            if hedge_leg_info is not None:
                try:
                    hedge_order_request = self._create_hedge_order_request(
                        base_order=base_order,
                        hedge_leg=hedge_leg_info,
                        paper_run=paper_run,
                        strategy=strategy,
                        reference_price=reference_price,
                        cycle_key=cycle_key,
                    )
                    hedge_order = self.gatekeeper.submit_order(hedge_order_request)
                    if hedge_order.execution_status == "accepted":
                        if self._should_execute_on_binance(paper_run, order=hedge_order):
                            hedge_order = self.exchange_execution.ensure_binance_execution(
                                paper_run=paper_run,
                                order=hedge_order,
                                order_request=hedge_order_request,
                                position=None,
                            )
                        if hedge_order.execution_status == "accepted":
                            hedge_order = self._fill_order(order=hedge_order, cycle_time=cycle_time)
                        else:
                            # Hedge leg failed after gatekeeper approval - rollback main leg
                            # by immediately closing the perpetual position
                            # (In real implementation, we would need to track and close,
                            # but for paper mode we just skip opening the main position)
                            rejected_orders += 1
                            actions.append(
                                PaperRuntimeAction(
                                    symbol=symbol,
                                    action="rejected",
                                    direction=base_order.direction,
                                    reason=f"hedge_leg_execution_failed: {hedge_order.rejection_reason}",
                                    order_execution_id=order.order_execution_id,
                                    reference_price=reference_price,
                                    idempotency_key=cycle_key,
                                    decision_trace=decision_trace,
                                )
                            )
                            continue
                    else:
                        # Hedge leg rejected by gatekeeper - skip main leg
                        rejected_orders += 1
                        actions.append(
                            PaperRuntimeAction(
                                symbol=symbol,
                                action="rejected",
                                direction=base_order.direction,
                                reason=f"hedge_leg_rejected: {hedge_order.rejection_reason}",
                                order_execution_id=order.order_execution_id,
                                reference_price=reference_price,
                                idempotency_key=cycle_key,
                                decision_trace=decision_trace,
                            )
                        )
                        continue
                except Exception as exc:
                    # Hedge leg failed - skip main leg
                    rejected_orders += 1
                    actions.append(
                        PaperRuntimeAction(
                            symbol=symbol,
                            action="rejected",
                            direction=base_order.direction,
                            reason=f"hedge_leg_exception: {exc}",
                            order_execution_id=order.order_execution_id,
                            reference_price=reference_price,
                            idempotency_key=cycle_key,
                            decision_trace=decision_trace,
                        )
                    )
                    continue

            position = self._open_position(
                paper_run_id=paper_run_id,
                order=order,
                cycle_time=cycle_time,
            )

            # Open hedge position if hedge order was successfully filled
            if hedge_order is not None and hedge_order.execution_status == "accepted":
                hedge_position = self._open_position(
                    paper_run_id=paper_run_id,
                    order=hedge_order,
                    cycle_time=cycle_time,
                )
                # Mark both positions as part of a hedge group
                hedge_group_id = f"hedge_{order.order_execution_id}"
                position = self._mark_position_as_hedged(
                    position=position,
                    hedge_group_id=hedge_group_id,
                    is_hedge_leg=False,
                )
                hedge_position = self._mark_position_as_hedged(
                    position=hedge_position,
                    hedge_group_id=hedge_group_id,
                    is_hedge_leg=True,
                )
                if hedge_leg_info is not None:
                    active_positions[str(hedge_leg_info["symbol"])] = hedge_position
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
        initial_equity = float(paper_run.execution_profile.get("account_equity") or 10_000.0)
        account_equity = float(metrics.get("account_equity", initial_equity)) + (
            realized_total - realized_total_at_cycle_start
        )
        equity_peak = max(
            float(metrics.get("equity_peak", initial_equity)),
            account_equity,
        )
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
            "exchange_missing_position_counts": reconcile_missing_counts,
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
        # last_cycle_decisions above is overwritten every cycle, so persist an
        # appended history row per decision here — this is the only durable
        # record of "no trade" skip reasons (technical_signals_insufficient,
        # confirmation_unavailable_fail_closed, etc.) that never reach
        # gatekeeper.submit_order() and therefore never create an OrderExecution row.
        for action in actions:
            if not action.decision_trace:
                continue
            with suppress(Exception):
                self.decision_snapshot_repo.create_snapshot(
                    DecisionSnapshot(
                        paper_run_id=paper_run_id,
                        symbol=action.symbol,
                        action=action.action,
                        pipeline_status=action.decision_trace.get("pipeline_status"),
                        reason=action.reason,
                        decision_trace=action.decision_trace,
                        cycle_time=cycle_time,
                    )
                )
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
        initial_equity = float(paper_run.execution_profile.get("account_equity") or 10_000.0)
        account_equity = float(metrics.get("account_equity") or initial_equity)
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
    def _with_open_trade_intent(
        *,
        base_order: ExecutionOrderRequest,
        cycle_id: str,
        active_config: ConfigSnapshot | None,
        reference_price: Decimal,
        decision_candle_close_time: datetime,
    ) -> ExecutionOrderRequest:
        """Attach the immutable OPEN contract when the cycle has a real config identity."""
        if active_config is None or active_config.config_snapshot_id is None:
            return base_order

        position_side, exchange_side = (
            (PositionSide.LONG, ExchangeSide.BUY)
            if base_order.direction == TradeSide.LONG
            else (PositionSide.SHORT, ExchangeSide.SELL)
        )
        signal = StrategySignal(
            decision_id=cycle_id,
            symbol=base_order.symbol,
            side=position_side,
            score=Decimal("100"),
            regime=MarketRegime.RANGE,
            signal_candle_close_time=decision_candle_close_time,
            strategy_id=base_order.strategy_id,
            strategy_version=base_order.version_id or "",
        )
        intent = DecisionEngine().build_intent(
            adapter_mode=RuntimeMode.PAPER,
            cycle_id=cycle_id,
            signal=signal,
            portfolio=PortfolioDecision(
                decision_id=cycle_id,
                symbol=base_order.symbol,
                raw_side=position_side,
                final_side=position_side,
                accepted=True,
            ),
            config_snapshot_id=active_config.config_snapshot_id,
            config_hash=active_config.config_hash,
            quantity=Decimal(str(base_order.entry_context["requested_notional"])) / reference_price,
            reference_price=reference_price,
            protection=ProtectionPolicy(
                stop_price=Decimal(str(base_order.stoploss_plan["price"])),
                take_profit_price=Decimal(str(base_order.takeprofit_plan["price"])),
            ),
            action=TradeAction.OPEN,
            exchange_side=exchange_side,
        )
        return base_order.model_copy(update={"trade_intent": intent})

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
                    # Exits always favor fill-certainty over price improvement, same as the
                    # manual close_position() path — a resting limit exit could miss a stop.
                    "order_type": "market",
                    "limit_price": None,
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
            trail_after_r = exit_ladder.remainder_trail_after_r if exit_ladder.all_levels_executed else None
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
        next_stop = next_trailed_stop_price(
            side=position.side.value,
            entry_price=position.entry_price,
            current_stop_price=levels.stop_price,
            initial_distance=initial_distance,
            trail_after_r=levels.trail_after_r,
            bar_high=float(bar.high),
            bar_low=float(bar.low),
        )
        if next_stop is None:
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
            partial_r if partial_r is not None else _float_or_none(strategy.rules.takeprofit_rules.get("risk_reward"))
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
            trigger.price >= expected_price if position.side == TradeSide.LONG else trigger.price <= expected_price
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

    def _expire_pending_limit_entries(
        self,
        *,
        paper_run: PaperRun,
        cycle_time: datetime,
    ) -> list[PaperRuntimeAction]:
        return self.exchange_execution.expire_pending_limit_entries(
            paper_run=paper_run,
            cycle_time=cycle_time,
            parse_datetime=_parse_datetime,
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
        return PaperExchangeExecutionService.gateway_order_request(
            order_request=order_request,
            position=position,
        )

    @staticmethod
    def _gateway_mirror_request(
        *,
        order_request: ExecutionOrderRequest,
        position: PositionSnapshot,
    ) -> ExecutionOrderRequest:
        return PaperExchangeExecutionService.gateway_mirror_request(
            order_request=order_request,
            position=position,
        )

    def _fill_order(self, *, order: OrderExecution, cycle_time: datetime) -> OrderExecution:
        return self.order_lifecycle.fill_order(order=order, cycle_time=cycle_time)

    def _open_position(self, *, paper_run_id: str, order: OrderExecution, cycle_time: datetime) -> PositionSnapshot:
        return self.order_lifecycle.open_position(
            paper_run_id=paper_run_id,
            order=order,
            cycle_time=cycle_time,
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
        return self.order_lifecycle.close_position(
            paper_run_id=paper_run_id,
            position=position,
            mark_price=mark_price,
            cycle_time=cycle_time,
            strategy=strategy,
            remaining_quantity=remaining_quantity,
        )

    def _record_estimated_order_cost(
        self,
        *,
        order: OrderExecution,
        strategy: StrategyContract,
        price: float,
    ) -> OrderExecution:
        return self.order_lifecycle.record_estimated_order_cost(order=order, strategy=strategy, price=price)

    def _mark_position(
        self,
        *,
        paper_run_id: str,
        position: PositionSnapshot,
        mark_price: float,
        cycle_time: datetime,
    ) -> PositionSnapshot:
        return self.order_lifecycle.mark_position(
            paper_run_id=paper_run_id,
            position=position,
            mark_price=mark_price,
            cycle_time=cycle_time,
        )

    def _create_hedge_order_request(
        self,
        *,
        base_order: ExecutionOrderRequest,
        hedge_leg: dict,
        paper_run: PaperRun,
        strategy: StrategyContract,
        reference_price: float,
        cycle_key: str,
    ) -> ExecutionOrderRequest:
        """Create an order request for the delta-neutral hedge leg."""
        hedge_symbol = str(hedge_leg["symbol"])
        hedge_direction = TradeSide(hedge_leg["direction"])

        # Use same notional as main leg for true delta-neutral hedge
        requested_notional = float(base_order.entry_context.get("requested_notional", 0.0))

        # Hedge leg uses same stop/take logic but on spot symbol
        reference_price_decimal = Decimal(str(reference_price))
        stop_distance = abs(
            reference_price_decimal - Decimal(str(base_order.stoploss_plan.get("price", reference_price)))
        )
        take_distance = abs(
            Decimal(str(base_order.takeprofit_plan.get("price", reference_price))) - reference_price_decimal
        )

        if hedge_direction == TradeSide.LONG:
            stoploss_price = float(max(reference_price_decimal - stop_distance, Decimal("0.00000001")))
            takeprofit_price = float(reference_price_decimal + take_distance)
        else:
            stoploss_price = float(reference_price_decimal + stop_distance)
            takeprofit_price = float(max(reference_price_decimal - take_distance, Decimal("0.00000001")))

        return ExecutionOrderRequest(
            strategy_id=base_order.strategy_id,
            version_id=base_order.version_id,
            symbol=hedge_symbol,
            direction=hedge_direction,
            risk_profile_id=base_order.risk_profile_id,
            entry_context={
                **base_order.entry_context,
                "hedge_for_symbol": base_order.symbol,
                "is_hedge_leg": True,
                "hedge_reason": hedge_leg.get("reason", "delta_neutral_hedge"),
                "reference_price": str(reference_price),
                "requested_notional": requested_notional,
            },
            stoploss_plan={"price": stoploss_price, "basis": "hedge_leg_protection"},
            takeprofit_plan={"price": takeprofit_price, "basis": "hedge_leg_exit"},
            signal_ensemble_id=base_order.signal_ensemble_id,
            meta_label_id=base_order.meta_label_id,
            veto_result=base_order.veto_result,
            validation_backtest_run_id=base_order.validation_backtest_run_id,
            paper_run_id=base_order.paper_run_id,
            risk_state=base_order.risk_state,
            idempotency_key=f"{cycle_key}_hedge",
        )

    def _mark_position_as_hedged(
        self,
        *,
        position: PositionSnapshot,
        hedge_group_id: str,
        is_hedge_leg: bool,
    ) -> PositionSnapshot:
        """Mark a position as part of a hedge group."""
        return position.model_copy(
            update={
                "hedge_group_id": hedge_group_id,
                "is_hedge_leg": is_hedge_leg,
            }
        )


def _realized_pnl(*, position: PositionSnapshot, mark_price: float) -> float:
    return realized_pnl(position=position, mark_price=mark_price)


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
        if (
            status == "unknown"
            and paper_run.execution_profile.get("execution_mode", "paper_only") == "paper_only"
            and not paper_run.execution_profile.get("mirror_to_gateway", False)
        ):
            # The fixed local universe is Paper-only here. Do not waste the first
            # cycle while the independently scheduled exchange-info refresh is
            # still resolving metadata; gateway-capable runs remain fail-closed.
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


def _estimated_transaction_cost(
    *,
    price: float,
    quantity: float,
    strategy: StrategyContract,
    symbol: str,
) -> EstimatedTransactionCost:
    return estimated_transaction_cost(price=price, quantity=quantity, strategy=strategy, symbol=symbol)


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
