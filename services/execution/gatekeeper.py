"""Execution and paper-admission gatekeeper services."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from services.data import DataRepository
from services.strategy_library import (
    ExecutionRepository,
    PaperRunRepository,
    RiskProfileRepository,
    ValidationRepository,
)
from shared.models import (
    ExecutionOrderRequest,
    OrderExecution,
    PaperRun,
    PaperRunRequest,
)

from .paper import PaperOrchestrationService

DEFAULT_FRESHNESS_DELAY = timedelta(hours=2)


class ExecutionGatekeeperService:
    """Apply validation, risk-event, veto, and stoploss gates before execution."""

    def __init__(
        self,
        *,
        data_repo: DataRepository,
        validation_repo: ValidationRepository,
        risk_profile_repo: RiskProfileRepository,
        execution_repo: ExecutionRepository,
        paper_repo: PaperRunRepository,
    ) -> None:
        self.data_repo = data_repo
        self.validation_repo = validation_repo
        self.risk_profile_repo = risk_profile_repo
        self.execution_repo = execution_repo
        self.paper_repo = paper_repo
        self.paper_service = PaperOrchestrationService()

    def prepare_paper_run(self, request: PaperRunRequest) -> PaperRun:
        if not request.gate_decision_ref:
            raise ValueError("paper admission requires gate_decision_ref")
        backtest = self.validation_repo.get_backtest_run(request.gate_decision_ref)
        if backtest is None:
            raise ValueError("validation backtest run not found")
        if backtest.eligibility_result is None or not backtest.eligibility_result.passed:
            raise ValueError("validation gate rejected paper admission")
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
        rejection_reasons: list[str] = []
        stoploss_present = bool(request.stoploss_plan)
        if not stoploss_present:
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

        if request.risk_profile_id:
            profile = self.risk_profile_repo.get_profile(request.risk_profile_id)
            if profile is None:
                rejection_reasons.append("risk_profile_not_found")

        timeframe = str(request.entry_context.get("timeframe", "1h"))
        reference_time = datetime.now(UTC)
        freshness = self.data_repo.check_freshness(
            symbol=request.symbol,
            timeframe=timeframe,
            reference_time=reference_time,
            max_delay=DEFAULT_FRESHNESS_DELAY,
        )
        if not freshness["is_fresh"]:
            rejection_reasons.append("data_not_fresh")

        if self.data_repo.has_blocking_risk_event(scope=request.symbol, reference_time=reference_time):
            rejection_reasons.append("blocking_risk_event")

        order = OrderExecution(
            order_execution_id=str(uuid.uuid4()),
            strategy_id=request.strategy_id,
            version_id=request.version_id,
            symbol=request.symbol,
            direction=request.direction,
            execution_status="rejected" if rejection_reasons else "accepted",
            stoploss_present=stoploss_present,
            close_only_mode=bool(request.entry_context.get("close_only_mode", False)),
            rejection_reason=";".join(rejection_reasons) if rejection_reasons else None,
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
        )
        return self.execution_repo.create_order(order)
