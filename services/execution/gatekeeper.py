"""Execution and paper-admission gatekeeper services."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from services.data import DataRepository
from services.strategy_library import (
    ExecutionRepository,
    HypothesisRepository,
    PaperRunRepository,
    ReviewRepository,
    RiskProfileRepository,
    ValidationRepository,
)
from services.validation.admission import ValidationAdmissionService
from shared.config import settings
from shared.models import (
    ExecutionOrderRequest,
    FailureRecord,
    OrderExecution,
    PaperRun,
    PaperRunRequest,
    RiskProfile,
)

from .kill_switch import KillSwitch, get_kill_switch
from .net_edge import net_edge_rejection_codes
from .paper import PaperOrchestrationService


def _freshness_delay() -> timedelta:
    """Resolve the order-freshness threshold from settings (previously hardcoded to 2h)."""
    return timedelta(seconds=settings.execution_freshness_delay_seconds)


def _positive_number(value: object) -> float | None:
    if not isinstance(value, str | int | float):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


class ExecutionGatekeeperService:
    """Apply validation, risk-event, veto, and stoploss gates before execution."""

    def __init__(
        self,
        *,
        data_repo: DataRepository,
        validation_repo: ValidationRepository,
        hypothesis_repo: HypothesisRepository | None,
        risk_profile_repo: RiskProfileRepository,
        execution_repo: ExecutionRepository,
        paper_repo: PaperRunRepository,
        review_repo: ReviewRepository | None = None,
        kill_switch: KillSwitch | None = None,
    ) -> None:
        self.data_repo = data_repo
        self.validation_repo = validation_repo
        self.hypothesis_repo = hypothesis_repo
        self.risk_profile_repo = risk_profile_repo
        self.execution_repo = execution_repo
        self.paper_repo = paper_repo
        self.review_repo = review_repo
        self.kill_switch = kill_switch or get_kill_switch()
        self.paper_service = PaperOrchestrationService()
        self.validation_admission = ValidationAdmissionService()

    def prepare_paper_run(self, request: PaperRunRequest) -> PaperRun:
        if not request.gate_decision_ref:
            raise ValueError("paper admission requires gate_decision_ref")
        backtest = self.validation_repo.get_backtest_run(request.gate_decision_ref)
        if backtest is None:
            raise ValueError("validation backtest run not found")
        if backtest.eligibility_result is None or not backtest.eligibility_result.passed:
            raise ValueError("validation gate rejected paper admission")
        hypothesis_id = backtest.validation_methodology.get("hypothesis_id")
        hypothesis = (
            self.hypothesis_repo.get_hypothesis(hypothesis_id) if self.hypothesis_repo and hypothesis_id else None
        )
        promotion_gate = self.validation_admission.assess_backtest_run(
            run=backtest,
            hypothesis=hypothesis,
        )
        if not promotion_gate.passed:
            raise ValueError(f"promotion evidence incomplete: {promotion_gate.reason}")
        prepared = self.paper_service.prepare_run(
            PaperRun(
                paper_run_id=str(uuid.uuid4()),
                strategy_id=request.strategy_id,
                version_id=request.version_id,
                exchange=request.exchange,
                symbol_scope=request.symbol_scope,
                candidate_symbols=request.candidate_symbols,
                selection_basis=request.selection_basis or "validation_admitted",
                run_window=request.run_window,
                execution_profile=request.execution_profile,
                gate_decision_ref=request.gate_decision_ref,
                paper_status="queued",
            )
        )
        return self.paper_repo.create_paper_run(prepared)

    def submit_order(self, request: ExecutionOrderRequest) -> OrderExecution:
        """Compatibility entry point while callers migrate to explicit gate APIs."""
        if bool(request.entry_context.get("close_only_mode", False)):
            result = self.validate_reduce_risk_exit(request)
            compatibility_status = "accepted" if result.execution_status == "PRETRADE_APPROVED" else "rejected"
            return self.execution_repo.update_order(
                result.order_execution_id or "",
                execution_status=compatibility_status,
            ) or result.model_copy(update={"execution_status": compatibility_status})
        return self.validate_entry(request)

    def validate_entry(self, request: ExecutionOrderRequest) -> OrderExecution:
        rejection_reasons: list[str] = []
        if request.entry_context.get("entry_enabled") is False:
            rejection_reasons.append("manifest_entry_disabled")
        # Kill switch — global trading halt. When triggered, ALL new orders are
        # rejected immediately regardless of validation/risk state.
        if self.kill_switch.is_triggered():
            rejection_reasons.append("kill_switch_active")
        close_only_mode = bool(request.entry_context.get("close_only_mode", False))
        stoploss_present = bool(request.stoploss_plan)
        if not stoploss_present and not close_only_mode:
            rejection_reasons.append("missing_stoploss")

        if request.veto_result is not None and request.veto_result.veto:
            rejection_reasons.append("llm_veto")

        if not request.validation_backtest_run_id:
            rejection_reasons.append("missing_validation_run")
        else:
            backtest = self.validation_repo.get_backtest_run(request.validation_backtest_run_id)
            if backtest is None:
                rejection_reasons.append("validation_run_not_found")
            elif backtest.eligibility_result is None or not backtest.eligibility_result.passed:
                rejection_reasons.append("validation_gate_rejected")

        profile = RiskProfile()
        if request.risk_profile_id:
            stored_profile = self.risk_profile_repo.get_profile(request.risk_profile_id)
            if stored_profile is None:
                rejection_reasons.append("risk_profile_not_found")
            else:
                profile = stored_profile

        risk_state = request.risk_state
        if risk_state is None and not close_only_mode:
            rejection_reasons.append("missing_risk_state")
        else:
            rejection_reasons.extend(self._evaluate_numeric_risk(profile=profile, request=request))

        timeframe = str(request.entry_context.get("timeframe", "1h"))
        reference_time = datetime.now(UTC)
        freshness = self.data_repo.check_freshness(
            symbol=request.symbol,
            timeframe=timeframe,
            reference_time=reference_time,
            max_delay=_freshness_delay(),
        )
        if not freshness["is_fresh"]:
            rejection_reasons.append("data_not_fresh")

        if self.data_repo.has_blocking_risk_event(scope=request.symbol, reference_time=reference_time):
            rejection_reasons.append("blocking_risk_event")

        rejection_reasons.extend(net_edge_rejection_codes(request.entry_context))

        order = OrderExecution(
            order_execution_id=str(uuid.uuid4()),
            strategy_id=request.strategy_id,
            version_id=request.version_id,
            symbol=request.symbol,
            direction=request.direction,
            execution_status="rejected" if rejection_reasons else "accepted",
            intent_id=request.trade_intent.intent_id if request.trade_intent is not None else None,
            cycle_id=request.trade_intent.cycle_id if request.trade_intent is not None else None,
            decision_id=request.trade_intent.decision_id if request.trade_intent is not None else None,
            config_snapshot_id=request.trade_intent.config_snapshot_id if request.trade_intent is not None else None,
            config_hash=request.trade_intent.config_hash if request.trade_intent is not None else None,
            intent_type=request.trade_intent.action.value if request.trade_intent is not None else None,
            timeframe=timeframe,
            signal_candle_close_time=(
                request.trade_intent.signal_candle_close_time if request.trade_intent is not None else None
            ),
            order_origin=request.order_origin,
            run_mode=request.run_mode,
            test_run_id=request.test_run_id,
            deployment_sha=request.deployment_sha,
            scheduler_instance_id=request.scheduler_instance_id,
            process_id=request.process_id,
            worker_id=request.worker_id,
            container_id=request.container_id,
            cycle_source=request.cycle_source,
            scheduled_for=request.scheduled_for,
            stoploss_present=stoploss_present,
            close_only_mode=close_only_mode,
            rejection_reason=";".join(rejection_reasons) if rejection_reasons else None,
            rejection_codes=rejection_reasons,
            entry_context={
                **request.entry_context,
                "freshness_check": freshness,
            },
            stoploss_plan=request.stoploss_plan,
            takeprofit_plan=request.takeprofit_plan,
            risk_profile_ref=request.risk_profile_id,
            validation_backtest_run_id=request.validation_backtest_run_id,
            paper_run_id=request.paper_run_id,
            live_run_id=request.live_run_id,
            signal_ensemble_id=request.signal_ensemble_id,
            meta_label_id=request.meta_label_id,
            veto_result=(request.veto_result.model_dump(mode="json") if request.veto_result is not None else {}),
            evaluated_risk_state=risk_state,
        )
        created, was_created = self.execution_repo.create_order_once(order)
        if not was_created:
            return created.model_copy(
                update={
                    "execution_status": "duplicate_skipped",
                    "rejection_reason": "duplicate_candle_intent",
                    "rejection_codes": ["duplicate_candle_intent"],
                }
            )
        if rejection_reasons:
            self._record_rejection(created)
        return created

    def validate_reduce_risk_exit(self, request: ExecutionOrderRequest) -> OrderExecution:
        """Authorize only an idempotent order proven to reduce exchange risk."""
        context = dict(request.entry_context)
        rejection_reasons: list[str] = []
        if not bool(context.get("close_only_mode")) or not bool(context.get("reduce_only")):
            rejection_reasons.append("reduce_risk_semantics_required")
        quantity = _positive_number(context.get("quantity"))
        authoritative_quantity = _positive_number(context.get("authoritative_position_quantity"))
        authoritative_side = str(context.get("authoritative_position_side") or "").lower()
        if authoritative_quantity is None:
            rejection_reasons.append("authoritative_exchange_position_required")
        elif quantity is None:
            rejection_reasons.append("reduce_quantity_required")
        elif quantity > authoritative_quantity:
            rejection_reasons.append("reduce_quantity_exceeds_exchange_position")
        if authoritative_side not in {"long", "short"}:
            rejection_reasons.append("authoritative_position_side_required")
        elif request.direction.value != authoritative_side:
            rejection_reasons.append("reduce_direction_mismatch")
        if request.cycle_source == "runtime_scheduler" and (
            request.scheduler_instance_id is None or request.fencing_token is None
        ):
            rejection_reasons.append("scheduler_fence_required")
        if context.get("exchange_available") is False:
            rejection_reasons.append("exchange_unavailable")

        order = OrderExecution(
            order_execution_id=str(uuid.uuid4()),
            strategy_id=request.strategy_id,
            version_id=request.version_id,
            symbol=request.symbol,
            direction=request.direction,
            execution_status=("PRETRADE_REJECTED" if rejection_reasons else "PRETRADE_APPROVED"),
            intent_id=request.trade_intent.intent_id if request.trade_intent is not None else None,
            cycle_id=request.trade_intent.cycle_id if request.trade_intent is not None else None,
            decision_id=request.trade_intent.decision_id if request.trade_intent is not None else None,
            config_snapshot_id=(request.trade_intent.config_snapshot_id if request.trade_intent is not None else None),
            config_hash=request.trade_intent.config_hash if request.trade_intent is not None else None,
            intent_type=request.trade_intent.action.value if request.trade_intent is not None else "REDUCE",
            timeframe=str(context.get("timeframe", "1h")),
            signal_candle_close_time=(
                request.trade_intent.signal_candle_close_time if request.trade_intent is not None else None
            ),
            order_origin=request.order_origin,
            run_mode=request.run_mode,
            test_run_id=request.test_run_id,
            deployment_sha=request.deployment_sha,
            scheduler_instance_id=request.scheduler_instance_id,
            process_id=request.process_id,
            worker_id=request.worker_id,
            container_id=request.container_id,
            cycle_source=request.cycle_source,
            scheduled_for=request.scheduled_for,
            stoploss_present=False,
            close_only_mode=True,
            rejection_reason=";".join(rejection_reasons) if rejection_reasons else None,
            rejection_codes=rejection_reasons,
            entry_context=context,
            stoploss_plan={},
            takeprofit_plan={},
            risk_profile_ref=request.risk_profile_id,
            paper_run_id=request.paper_run_id,
            live_run_id=request.live_run_id,
            evaluated_risk_state=request.risk_state,
        )
        created, was_created = self.execution_repo.create_order_once(order)
        if not was_created:
            return created.model_copy(
                update={
                    "execution_status": "duplicate_skipped",
                    "rejection_reason": "duplicate_candle_intent",
                    "rejection_codes": ["duplicate_candle_intent"],
                }
            )
        if rejection_reasons:
            self._record_rejection(created)
        return created

    @staticmethod
    def _evaluate_numeric_risk(*, profile: RiskProfile, request: ExecutionOrderRequest) -> list[str]:
        risk_state = request.risk_state
        if risk_state is None:
            return []
        if bool(request.entry_context.get("close_only_mode", False)):
            return []
        if risk_state.account_equity <= 0 or risk_state.equity_peak <= 0:
            return ["invalid_risk_state"]
        rejection_reasons: list[str] = []
        requested_fraction = risk_state.requested_notional / risk_state.account_equity
        requested_signed_fraction = requested_fraction if request.direction.value == "long" else -requested_fraction

        # For hedge legs in delta-neutral carry trades, the directional exposure
        # should be netted against the main leg. The hedge_leg flag in entry_context
        # signals this order is part of a hedged pair.
        is_hedge_leg = bool(request.entry_context.get("is_hedge_leg", False))
        if is_hedge_leg:
            # Hedge legs do not add to net directional exposure (they offset the main leg)
            # so we skip the net_directional_exposure check for hedge legs
            pass

        projected_symbol_exposure = risk_state.symbol_exposure + requested_fraction
        projected_total_exposure = risk_state.total_exposure + requested_fraction
        drawdown = max(0.0, (risk_state.equity_peak - risk_state.account_equity) / risk_state.equity_peak)

        if projected_symbol_exposure > profile.max_symbol_exposure:
            rejection_reasons.append("max_symbol_exposure_exceeded")
        if projected_total_exposure > profile.max_total_exposure:
            rejection_reasons.append("max_total_exposure_exceeded")
        correlated_peer_count_limit = int(request.entry_context.get("correlated_peer_count_limit", 2))
        correlated_cluster_exposure_limit = float(request.entry_context.get("correlated_cluster_exposure_limit", 0.35))
        net_directional_exposure_limit = float(request.entry_context.get("net_directional_exposure_limit", 0.40))
        if not risk_state.portfolio_correlation_available:
            rejection_reasons.append("portfolio_correlation_unavailable")
        elif risk_state.high_correlation_peer_count >= correlated_peer_count_limit:
            rejection_reasons.append("correlated_exposure_limit_exceeded")
        elif risk_state.correlated_cluster_exposure + requested_fraction > correlated_cluster_exposure_limit:
            rejection_reasons.append("correlated_cluster_exposure_exceeded")

        # Skip net directional exposure check for hedge legs (they offset, not add)
        if (
            not is_hedge_leg
            and abs(risk_state.net_directional_exposure + requested_signed_fraction) > net_directional_exposure_limit
        ):
            rejection_reasons.append("net_directional_exposure_exceeded")

        if risk_state.open_positions >= profile.max_open_positions:
            rejection_reasons.append("max_open_positions_exceeded")
        if risk_state.requested_leverage > profile.max_leverage:
            rejection_reasons.append("max_leverage_exceeded")
        if risk_state.requested_stop_risk_fraction > profile.single_trade_risk_limit:
            rejection_reasons.append("single_trade_stop_risk_exceeded")
        portfolio_risk_limit = float(request.entry_context.get("max_portfolio_initial_risk_fraction", 0.05))
        if risk_state.portfolio_initial_risk_fraction + risk_state.requested_stop_risk_fraction > portfolio_risk_limit:
            rejection_reasons.append("portfolio_initial_risk_exceeded")
        if abs(min(risk_state.daily_realized_pnl, 0.0)) >= risk_state.account_equity * profile.daily_loss_limit:
            rejection_reasons.append("daily_loss_limit_breached")
        if abs(min(risk_state.weekly_realized_pnl, 0.0)) >= risk_state.account_equity * profile.weekly_loss_limit:
            rejection_reasons.append("weekly_loss_limit_breached")
        if drawdown >= profile.hard_stop_drawdown_limit:
            rejection_reasons.append("hard_stop_drawdown_breached")
        elif drawdown >= profile.drawdown_limit:
            rejection_reasons.append("drawdown_limit_breached")
        if risk_state.consecutive_losses >= profile.consecutive_loss_limit:
            rejection_reasons.append("consecutive_loss_limit_breached")
        # Martingale guard (AGENTS.md §5: Martingale strategies are forbidden).
        # If the account is in a losing streak and this order's notional exceeds
        # 2× the existing symbol exposure, treat it as a Martingale attempt.
        if (
            risk_state.consecutive_losses > 0
            and risk_state.symbol_exposure > 0
            and requested_fraction > risk_state.symbol_exposure * 2
        ):
            rejection_reasons.append("martingale_detected")
        if risk_state.api_failures_window >= profile.api_failure_limit:
            rejection_reasons.append("api_failure_limit_breached")
        return rejection_reasons

    def _record_rejection(self, order: OrderExecution) -> None:
        if self.review_repo is None:
            return
        if not order.strategy_id:
            return
        failure = FailureRecord(
            strategy_id=order.strategy_id,
            version_id=order.version_id,
            origin_run_type="paper" if order.paper_run_id else "live" if order.live_run_id else "execution_request",
            origin_run_id=order.paper_run_id or order.live_run_id or (order.order_execution_id or ""),
            failure_type="execution_gate_reject",
            failure_summary=f"Gatekeeper rejected {order.symbol} order: {', '.join(order.rejection_codes)}",
            evidence_refs=[f"order_execution:{order.order_execution_id}"],
            recommended_change="Review gatekeeper rejection codes and risk-state inputs before retrying.",
        )
        self.review_repo.create_failure(failure)
