"""Repositories and mappers for the platform research loop."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from shared.models import (
    AgentTask,
    BacktestReport,
    BacktestRun,
    BetDecision,
    DecisionMemoryEntry,
    DecisionSnapshot,
    EnsembleStatus,
    Exchange,
    ExchangeAccountSnapshot,
    ExecutionRiskState,
    FailureRecord,
    GateDecision,
    HypothesisRecord,
    IngestionJob,
    LiveRun,
    Market,
    MetaLabel,
    NotificationOutboxItem,
    OptimizationRun,
    OrderExecution,
    PaperRun,
    PositionSnapshot,
    ReconciliationRecord,
    ReviewReport,
    RiskLevel,
    RiskProfile,
    RiskProfileUpdate,
    RunStatus,
    SignalEnsemble,
    StrategyContract,
    StrategyCreate,
    StrategyDraft,
    StrategyIdea,
    StrategyRules,
    StrategyStatus,
    StrategyUpdate,
    StrategyVersion,
    Timeframe,
    TradeSide,
    TripleBarrierOutcome,
)

from . import models


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _ensure_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _jsonable(value: Any):
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump(mode="json"))
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value


def _idea_from_orm(row: models.StrategyIdea) -> StrategyIdea:
    return StrategyIdea(
        idea_id=row.idea_id,
        title=row.title,
        source=row.source,
        market=Market(row.market),
        symbol_scope=row.symbol_scope,
        hypothesis_summary=row.hypothesis_summary,
        source_ref=row.source_ref,
        rationale=row.rationale,
        intake_metadata=row.intake_metadata or {},
        intake_bucket=row.intake_bucket,
        created_at=row.created_at,
    )


def _hypothesis_from_orm(row: models.Hypothesis) -> HypothesisRecord:
    return HypothesisRecord(
        hypothesis_id=row.hypothesis_id,
        strategy_id=row.strategy_id,
        idea_id=row.idea_id,
        title=row.title,
        statement=row.statement,
        rationale=row.rationale,
        benchmark_plan=row.benchmark_plan or {},
        acceptance_criteria=row.acceptance_criteria or {},
        status=row.status,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _draft_rules_from_orm(row: models.StrategyDraft) -> StrategyRules:
    return StrategyRules(
        entry_rules=row.entry_rules,
        exit_rules=row.exit_rules,
        stoploss_rules=row.stoploss_rules,
        takeprofit_rules=row.takeprofit_rules,
        position_rules=row.position_rules,
    )


def _draft_from_orm(row: models.StrategyDraft) -> StrategyDraft:
    return StrategyDraft(
        draft_id=row.draft_id,
        idea_id=row.idea_id,
        title=row.title,
        source=row.source,
        core_thesis=row.core_thesis,
        market=Market(row.market),
        symbol_scope=row.symbol_scope,
        timeframe=Timeframe(row.timeframe),
        market_regime=row.market_regime,
        risk_level=RiskLevel(row.risk_level),
        rules=_draft_rules_from_orm(row),
        draft_status=row.draft_status,
        review_notes=row.review_notes,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _strategy_rules_from_orm(row: models.Strategy) -> StrategyRules:
    return StrategyRules(
        entry_rules=row.entry_rules,
        exit_rules=row.exit_rules,
        stoploss_rules=row.stoploss_rules,
        takeprofit_rules=row.takeprofit_rules,
        position_rules=row.position_rules,
    )


def _run_status_from_orm(value: str) -> RunStatus:
    # Older API versions copied the PaperRun-only "paused" state into the
    # strategy lifecycle column. A paused run still means the strategy reached
    # the running Paper stage, so normalize that legacy value at the boundary.
    if value == "paused":
        return RunStatus.RUNNING
    return RunStatus(value)


def _strategy_from_orm(row: models.Strategy) -> StrategyContract:
    return StrategyContract(
        strategy_id=row.id,
        strategy_key=row.strategy_key,
        source=row.source,
        core_thesis=row.core_thesis,
        market=Market(row.market),
        symbol_scope=row.symbol_scope,
        timeframe=Timeframe(row.timeframe),
        market_regime=row.market_regime,
        risk_level=RiskLevel(row.risk_level),
        rules=_strategy_rules_from_orm(row),
        strategy_status=StrategyStatus(row.strategy_status),
        backtest_status=_run_status_from_orm(row.backtest_status),
        paper_status=_run_status_from_orm(row.paper_status),
        live_status=_run_status_from_orm(row.live_status),
        failure_reasons=row.failure_reasons,
        iteration_history=row.iteration_history,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _version_from_orm(row: models.StrategyVersion) -> StrategyVersion:
    return StrategyVersion(
        version_id=row.version_id,
        strategy_id=row.strategy_id,
        version_label=row.version_label,
        change_summary=row.change_summary,
        code_artifact_ref=row.code_artifact_ref,
        created_at=row.created_at,
    )


def _gate_from_payload(strategy_id: str, payload: dict | GateDecision | None) -> GateDecision | None:
    if payload is None:
        return None
    if isinstance(payload, GateDecision):
        return payload
    normalized = dict(payload)
    normalized.setdefault("strategy_id", strategy_id)
    return GateDecision(**normalized)


def _backtest_from_orm(row: models.BacktestRun) -> BacktestRun:
    metrics_summary = (
        BacktestReport(**row.metrics_summary) if isinstance(row.metrics_summary, dict) else row.metrics_summary
    )
    return BacktestRun(
        backtest_run_id=row.backtest_run_id,
        strategy_id=row.strategy_id,
        version_id=row.version_id,
        dataset_scope=row.dataset_scope,
        execution_engine=row.execution_engine,
        parameter_set=row.parameter_set,
        market_regime_coverage=row.market_regime_coverage,
        sample_split_plan=row.sample_split_plan,
        cost_model_ref=row.cost_model_ref,
        validation_methodology=row.validation_methodology,
        stress_test_scenarios=row.stress_test_scenarios,
        metrics_summary=metrics_summary,
        run_status=row.run_status,
        eligibility_result=_gate_from_payload(row.strategy_id, row.eligibility_result),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _optimization_from_orm(row: models.OptimizationRun) -> OptimizationRun:
    return OptimizationRun(
        optimization_run_id=row.optimization_run_id,
        strategy_id=row.strategy_id,
        version_id=row.version_id,
        search_space_ref=row.search_space_ref,
        optimization_method=row.optimization_method,
        best_candidate_summary=row.best_candidate_summary,
        run_status=row.run_status,
        created_at=row.created_at,
    )


def _ingestion_job_from_orm(row: models.IngestionJob) -> IngestionJob:
    return IngestionJob(
        ingestion_job_id=row.ingestion_job_id,
        source_family=row.source_family,
        source_name=row.source_name,
        job_type=row.job_type,
        schedule_mode=row.schedule_mode,
        job_status=row.job_status,
        input_window=row.input_window,
        target_symbols=row.target_symbols,
        output_ref=row.output_ref,
        error_summary=row.error_summary,
        execution_summary=row.execution_summary,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _paper_run_from_orm(row: models.PaperRun) -> PaperRun:
    return PaperRun(
        paper_run_id=row.paper_run_id,
        strategy_id=row.strategy_id,
        version_id=row.version_id,
        exchange=Exchange(row.exchange),
        symbol_scope=row.symbol_scope,
        candidate_symbols=row.candidate_symbols,
        selection_basis=row.selection_basis,
        run_window=row.run_window,
        execution_profile=row.execution_profile,
        gate_decision_ref=row.gate_decision_ref,
        paper_metrics_summary=row.paper_metrics_summary,
        paper_status=row.paper_status,
        created_at=row.created_at,
    )


def _live_run_from_orm(row: models.LiveRun) -> LiveRun:
    return LiveRun(
        live_run_id=row.live_run_id,
        strategy_id=row.strategy_id,
        version_id=row.version_id,
        exchange=Exchange(row.exchange),
        capital_tier=row.capital_tier,
        live_status=row.live_status,
        validation_backtest_run_id=row.validation_backtest_run_id,
        risk_profile_ref=row.risk_profile_ref,
        live_metrics_summary=row.live_metrics_summary,
        created_at=row.created_at,
    )


def _risk_profile_from_orm(row: models.RiskProfile) -> RiskProfile:
    return RiskProfile(
        risk_profile_id=row.risk_profile_id,
        single_trade_risk_limit=row.single_trade_risk_limit,
        max_symbol_exposure=row.max_symbol_exposure,
        max_total_exposure=row.max_total_exposure,
        max_open_positions=row.max_open_positions,
        max_leverage=row.max_leverage,
        daily_loss_limit=row.daily_loss_limit,
        weekly_loss_limit=row.weekly_loss_limit,
        drawdown_limit=row.drawdown_limit,
        hard_stop_drawdown_limit=row.hard_stop_drawdown_limit,
        consecutive_loss_limit=row.consecutive_loss_limit,
        api_failure_limit=row.api_failure_limit,
        api_failure_window_minutes=row.api_failure_window_minutes,
        market_scope=row.market_scope,
        config_source=row.config_source,
    )


def _notification_from_orm(row: models.NotificationOutbox) -> NotificationOutboxItem:
    return NotificationOutboxItem(
        notification_id=row.notification_id,
        event_type=row.event_type,
        severity=row.severity,
        channel_group=row.channel_group,
        delivery_channels=row.delivery_channels or [],
        subject=row.subject,
        body=row.body,
        source_ref=row.source_ref,
        delivery_status=row.delivery_status,
        delivery_attempts=row.delivery_attempts,
        next_attempt_at=_ensure_utc(row.next_attempt_at),
        last_attempt_at=_ensure_utc(row.last_attempt_at),
        attempt_history=row.attempt_history or [],
        last_error=row.last_error,
        delivered_at=_ensure_utc(row.delivered_at),
        created_at=_ensure_utc(row.created_at),
        updated_at=_ensure_utc(row.updated_at),
    )


def _review_report_from_orm(row: models.ReviewReport) -> ReviewReport:
    return ReviewReport(
        review_report_id=row.review_report_id,
        report_date=row.report_date,
        scope_type=row.scope_type,
        strategy_refs=row.strategy_refs,
        worst_performer_refs=row.worst_performer_refs,
        failure_patterns=row.failure_patterns,
        deviation_analysis=row.deviation_analysis,
        recommendations=row.recommendations,
        report_status=row.report_status,
        created_at=row.created_at,
    )


def _decision_memory_from_orm(row: models.DecisionMemoryEntry) -> DecisionMemoryEntry:
    return DecisionMemoryEntry(
        decision_memory_id=row.decision_memory_id,
        scope_type=row.scope_type,
        scope_id=row.scope_id,
        decision_type=row.decision_type,
        verdict=row.verdict,
        summary=row.summary,
        tags=row.tags or [],
        evidence_refs=row.evidence_refs or [],
        context_payload=row.context_payload or {},
        created_at=row.created_at,
    )


def _decision_snapshot_from_orm(row: models.DecisionSnapshot) -> DecisionSnapshot:
    return DecisionSnapshot(
        decision_snapshot_id=row.decision_snapshot_id,
        paper_run_id=row.paper_run_id,
        symbol=row.symbol,
        action=row.action,
        pipeline_status=row.pipeline_status,
        reason=row.reason,
        decision_trace=row.decision_trace or {},
        cycle_time=_ensure_utc(row.cycle_time),
        created_at=row.created_at,
    )


def _failure_record_from_orm(row: models.FailureRecord) -> FailureRecord:
    return FailureRecord(
        failure_record_id=row.failure_record_id,
        strategy_id=row.strategy_id,
        idea_id=row.idea_id,
        version_id=row.version_id,
        origin_run_type=row.origin_run_type,
        origin_run_id=row.origin_run_id,
        failure_type=row.failure_type,
        failure_summary=row.failure_summary,
        evidence_refs=row.evidence_refs,
        recommended_change=row.recommended_change,
        created_at=row.created_at,
    )


def _agent_task_from_orm(row: models.AgentTask) -> AgentTask:
    return AgentTask(
        agent_task_id=row.agent_task_id,
        agent_type=row.agent_type,
        task_type=row.task_type,
        input_ref=row.input_ref,
        output_ref=row.output_ref,
        input_payload=row.input_payload,
        output_payload=row.output_payload,
        priority=row.priority,
        task_status=row.task_status,
        error_summary=row.error_summary,
        executor_name=row.executor_name,
        attempt_history=row.attempt_history or [],
        provider_trace=row.provider_trace or {},
        schema_validation_status=row.schema_validation_status,
        scheduled_at=row.scheduled_at,
        created_at=row.created_at,
    )


def _signal_ensemble_from_orm(row: models.SignalEnsemble) -> SignalEnsemble:
    return SignalEnsemble(
        ensemble_id=row.ensemble_id,
        strategy_refs=row.strategy_refs,
        fusion_method=row.fusion_method,
        correlation_matrix_ref=row.correlation_matrix_ref,
        raw_votes=row.raw_votes,
        fused_direction=TradeSide(row.fused_direction) if row.fused_direction is not None else None,
        fused_confidence=row.fused_confidence,
        ensemble_status=EnsembleStatus(row.ensemble_status),
        created_at=row.created_at,
    )


def _meta_label_from_orm(row: models.MetaLabel) -> MetaLabel:
    return MetaLabel(
        meta_label_id=row.meta_label_id,
        ensemble_id=row.ensemble_id,
        triple_barrier_result=(
            TripleBarrierOutcome(row.triple_barrier_result) if row.triple_barrier_result is not None else None
        ),
        bet_decision=BetDecision(row.bet_decision),
        position_size_fraction=row.position_size_fraction,
        model_ref=row.model_ref,
        training_window_ref=row.training_window_ref,
    )


def _order_execution_from_orm(row: models.OrderExecution) -> OrderExecution:
    return OrderExecution(
        order_execution_id=row.order_execution_id,
        strategy_id=row.strategy_id,
        version_id=row.version_id,
        symbol=row.symbol,
        direction=TradeSide(row.direction),
        execution_status=row.execution_status,
        stoploss_present=row.stoploss_present,
        close_only_mode=row.close_only_mode,
        rejection_reason=row.rejection_reason,
        rejection_codes=row.rejection_codes,
        entry_context=row.entry_context,
        stoploss_plan=row.stoploss_plan,
        takeprofit_plan=row.takeprofit_plan,
        risk_profile_ref=row.risk_profile_ref,
        validation_backtest_run_id=row.validation_backtest_run_id,
        paper_run_id=row.paper_run_id,
        live_run_id=row.live_run_id,
        signal_ensemble_id=row.signal_ensemble_id,
        meta_label_id=row.meta_label_id,
        veto_result=row.veto_result,
        evaluated_risk_state=(
            ExecutionRiskState(**row.evaluated_risk_state)
            if isinstance(row.evaluated_risk_state, dict) and row.evaluated_risk_state
            else None
        ),
        gateway_name=row.gateway_name,
        gateway_order_id=row.gateway_order_id,
        gateway_status=row.gateway_status,
        lifecycle_history=row.lifecycle_history or [],
        reconciliation_status=row.reconciliation_status,
        last_gateway_update_at=_ensure_utc(row.last_gateway_update_at),
        created_at=row.created_at,
    )


def _position_snapshot_from_orm(row: models.PositionSnapshot) -> PositionSnapshot:
    return PositionSnapshot(
        position_snapshot_id=row.position_snapshot_id,
        run_type=row.run_type,
        run_id=row.run_id,
        symbol=row.symbol,
        side=TradeSide(row.side),
        quantity=row.quantity,
        entry_price=row.entry_price,
        mark_price=row.mark_price,
        unrealized_pnl=row.unrealized_pnl,
        snapshot_time=_ensure_utc(row.snapshot_time) or _utcnow(),
    )


def _account_snapshot_from_orm(row: models.ExchangeAccountSnapshot) -> ExchangeAccountSnapshot:
    return ExchangeAccountSnapshot(
        snapshot_id=row.snapshot_id,
        live_run_id=row.live_run_id,
        exchange=row.exchange,
        wallet_balance=row.wallet_balance,
        available_balance=row.available_balance,
        margin_balance=row.margin_balance,
        unrealized_pnl=row.unrealized_pnl,
        open_position_count=row.open_position_count,
        source_ref=row.source_ref,
        snapshot_time=_ensure_utc(row.snapshot_time),
    )


def _reconciliation_from_orm(row: models.ReconciliationRecord) -> ReconciliationRecord:
    return ReconciliationRecord(
        reconciliation_id=row.reconciliation_id,
        live_run_id=row.live_run_id,
        reconciliation_status=row.reconciliation_status,
        open_order_count=row.open_order_count,
        position_mismatches=row.position_mismatches or [],
        notes=row.notes or [],
        created_at=_ensure_utc(row.created_at),
    )


def _draft_to_strategy_key(title: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_]+", "_", title).strip("_")
    base = normalized or "strategy"
    return f"{base}_{uuid.uuid4().hex[:8]}"


class StrategyRepository:
    """Repository for the strategy lifecycle."""

    def __init__(self, session: Session):
        self.session = session

    def list_ideas(self) -> list[StrategyIdea]:
        rows = self.session.query(models.StrategyIdea).order_by(models.StrategyIdea.created_at).all()
        return [_idea_from_orm(row) for row in rows]

    def get_idea(self, idea_id: str) -> StrategyIdea | None:
        row = self.session.get(models.StrategyIdea, idea_id)
        return _idea_from_orm(row) if row else None

    def create_idea(self, idea: StrategyIdea) -> StrategyIdea:
        row = models.StrategyIdea(
            idea_id=idea.idea_id or str(uuid.uuid4()),
            title=idea.title,
            source=idea.source,
            market=idea.market,
            symbol_scope=idea.symbol_scope,
            hypothesis_summary=idea.hypothesis_summary,
            source_ref=idea.source_ref,
            rationale=idea.rationale,
            intake_metadata=_jsonable(idea.intake_metadata),
            intake_bucket=idea.intake_bucket,
        )
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return _idea_from_orm(row)

    def list_drafts(self) -> list[StrategyDraft]:
        rows = self.session.query(models.StrategyDraft).order_by(models.StrategyDraft.created_at).all()
        return [_draft_from_orm(row) for row in rows]

    def get_draft(self, draft_id: str) -> StrategyDraft | None:
        row = self.session.get(models.StrategyDraft, draft_id)
        return _draft_from_orm(row) if row else None

    def create_draft(self, draft: StrategyDraft) -> StrategyDraft:
        row = models.StrategyDraft(
            draft_id=draft.draft_id or str(uuid.uuid4()),
            idea_id=draft.idea_id,
            title=draft.title,
            source=draft.source,
            core_thesis=draft.core_thesis,
            market=draft.market,
            symbol_scope=draft.symbol_scope,
            timeframe=draft.timeframe,
            market_regime=draft.market_regime,
            risk_level=draft.risk_level,
            draft_status=draft.draft_status,
            review_notes=draft.review_notes,
            entry_rules=draft.rules.entry_rules,
            exit_rules=draft.rules.exit_rules,
            stoploss_rules=draft.rules.stoploss_rules,
            takeprofit_rules=draft.rules.takeprofit_rules,
            position_rules=draft.rules.position_rules,
        )
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return _draft_from_orm(row)

    def promote_idea_to_draft(self, idea_id: str) -> StrategyDraft | None:
        idea = self.session.get(models.StrategyIdea, idea_id)
        if idea is None:
            return None
        draft = models.StrategyDraft(
            idea_id=idea.idea_id,
            title=idea.title,
            source=idea.source,
            core_thesis=idea.hypothesis_summary,
            market=idea.market,
            symbol_scope=idea.symbol_scope,
            review_notes=[f"seeded from intake bucket={idea.intake_bucket}"],
        )
        self.session.add(draft)
        self.session.commit()
        self.session.refresh(draft)
        return _draft_from_orm(draft)

    def list_strategies(self) -> list[StrategyContract]:
        rows = self.session.query(models.Strategy).order_by(models.Strategy.created_at).all()
        return [_strategy_from_orm(row) for row in rows]

    def get_strategy(self, strategy_id: str) -> StrategyContract | None:
        row = self.session.get(models.Strategy, strategy_id)
        return _strategy_from_orm(row) if row else None

    def create_strategy(self, body: StrategyCreate) -> StrategyContract:
        row = models.Strategy(
            strategy_key=body.strategy_key,
            source=body.source,
            core_thesis=body.core_thesis,
            market=body.market,
            symbol_scope=body.symbol_scope,
            timeframe=body.timeframe,
            market_regime=body.market_regime,
            risk_level=body.risk_level,
            entry_rules=body.rules.entry_rules,
            exit_rules=body.rules.exit_rules,
            stoploss_rules=body.rules.stoploss_rules,
            takeprofit_rules=body.rules.takeprofit_rules,
            position_rules=body.rules.position_rules,
        )
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return _strategy_from_orm(row)

    def materialize_strategy_from_draft(self, draft_id: str) -> StrategyContract | None:
        draft = self.session.get(models.StrategyDraft, draft_id)
        if draft is None:
            return None
        row = models.Strategy(
            strategy_key=_draft_to_strategy_key(draft.title),
            source=draft.source,
            core_thesis=draft.core_thesis,
            market=draft.market,
            symbol_scope=draft.symbol_scope,
            timeframe=draft.timeframe,
            market_regime=draft.market_regime,
            risk_level=draft.risk_level,
            entry_rules=draft.entry_rules,
            exit_rules=draft.exit_rules,
            stoploss_rules=draft.stoploss_rules,
            takeprofit_rules=draft.takeprofit_rules,
            position_rules=draft.position_rules,
        )
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return _strategy_from_orm(row)

    def update_strategy(self, strategy_id: str, body: StrategyUpdate) -> StrategyContract | None:
        row = self.session.get(models.Strategy, strategy_id)
        if row is None:
            return None
        if body.core_thesis is not None:
            row.core_thesis = body.core_thesis
        if body.market_regime is not None:
            row.market_regime = body.market_regime
        if body.risk_level is not None:
            row.risk_level = body.risk_level
        if body.strategy_status is not None:
            row.strategy_status = body.strategy_status
        if body.rules is not None:
            row.entry_rules = body.rules.entry_rules
            row.exit_rules = body.rules.exit_rules
            row.stoploss_rules = body.rules.stoploss_rules
            row.takeprofit_rules = body.rules.takeprofit_rules
            row.position_rules = body.rules.position_rules
        row.updated_at = _utcnow()
        self.session.commit()
        self.session.refresh(row)
        return _strategy_from_orm(row)

    def append_failure_record(self, strategy_id: str, failure_summary: str, recommended_change: str | None) -> None:
        row = self.session.get(models.Strategy, strategy_id)
        if row is None:
            return
        row.failure_reasons = [*row.failure_reasons, failure_summary]
        row.iteration_history = [
            *row.iteration_history,
            {
                "recorded_at": _utcnow().isoformat(),
                "failure_summary": failure_summary,
                "recommended_change": recommended_change,
            },
        ]
        row.updated_at = _utcnow()

    def update_lifecycle_status(
        self,
        strategy_id: str,
        *,
        backtest_status: str | None = None,
        paper_status: str | None = None,
        live_status: str | None = None,
    ) -> StrategyContract | None:
        """Update the lifecycle status fields on the Strategy table.

        This is the single write-path for ``backtest_status`` /
        ``paper_status`` / ``live_status``. Previously these fields were
        defined on the ORM but never written back, leaving the strategy state
        machine stuck at ``not_started`` and breaking the
        research→validation→execution→review closed loop.
        """
        row = self.session.get(models.Strategy, strategy_id)
        if row is None:
            return None
        if backtest_status is not None:
            row.backtest_status = backtest_status
        if paper_status is not None:
            row.paper_status = paper_status
        if live_status is not None:
            row.live_status = live_status
        row.updated_at = _utcnow()
        self.session.commit()
        self.session.refresh(row)
        return _strategy_from_orm(row)

    def append_iteration_event(self, strategy_id: str, event: dict) -> None:
        row = self.session.get(models.Strategy, strategy_id)
        if row is None:
            return
        row.iteration_history = [
            *row.iteration_history,
            {
                "recorded_at": _utcnow().isoformat(),
                **_jsonable(event),
            },
        ]
        row.updated_at = _utcnow()
        self.session.commit()

    def delete_strategy(self, strategy_id: str) -> bool:
        row = self.session.get(models.Strategy, strategy_id)
        if row is None:
            return False
        self.session.delete(row)
        self.session.commit()
        return True

    def list_versions(self, *, strategy_id: str | None = None) -> list[StrategyVersion]:
        query = self.session.query(models.StrategyVersion)
        if strategy_id is not None:
            query = query.filter(models.StrategyVersion.strategy_id == strategy_id)
        rows = query.order_by(models.StrategyVersion.created_at).all()
        return [_version_from_orm(row) for row in rows]

    def create_version(self, version: StrategyVersion) -> StrategyVersion:
        row = models.StrategyVersion(
            version_id=version.version_id or str(uuid.uuid4()),
            strategy_id=version.strategy_id,
            version_label=version.version_label,
            change_summary=version.change_summary,
            code_artifact_ref=version.code_artifact_ref,
        )
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return _version_from_orm(row)


class HypothesisRepository:
    def __init__(self, session: Session):
        self.session = session

    def list_hypotheses(
        self, *, strategy_id: str | None = None, idea_id: str | None = None, status: str | None = None
    ) -> list[HypothesisRecord]:
        query = self.session.query(models.Hypothesis)
        if strategy_id is not None:
            query = query.filter(models.Hypothesis.strategy_id == strategy_id)
        if idea_id is not None:
            query = query.filter(models.Hypothesis.idea_id == idea_id)
        if status is not None:
            query = query.filter(models.Hypothesis.status == status)
        rows = query.order_by(models.Hypothesis.created_at).all()
        return [_hypothesis_from_orm(row) for row in rows]

    def get_hypothesis(self, hypothesis_id: str) -> HypothesisRecord | None:
        row = self.session.get(models.Hypothesis, hypothesis_id)
        return _hypothesis_from_orm(row) if row else None

    def create_hypothesis(self, hypothesis: HypothesisRecord) -> HypothesisRecord:
        row = models.Hypothesis(
            hypothesis_id=hypothesis.hypothesis_id or str(uuid.uuid4()),
            strategy_id=hypothesis.strategy_id,
            idea_id=hypothesis.idea_id,
            title=hypothesis.title,
            statement=hypothesis.statement,
            rationale=hypothesis.rationale,
            benchmark_plan=_jsonable(hypothesis.benchmark_plan),
            acceptance_criteria=_jsonable(hypothesis.acceptance_criteria),
            status=hypothesis.status,
        )
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return _hypothesis_from_orm(row)

    def update_hypothesis(self, hypothesis_id: str, **fields) -> HypothesisRecord | None:
        row = self.session.get(models.Hypothesis, hypothesis_id)
        if row is None:
            return None
        for key, value in fields.items():
            setattr(row, key, _jsonable(value))
        row.updated_at = _utcnow()
        self.session.commit()
        self.session.refresh(row)
        return _hypothesis_from_orm(row)


class ValidationRepository:
    def __init__(self, session: Session):
        self.session = session

    def list_backtest_runs(self) -> list[BacktestRun]:
        rows = self.session.query(models.BacktestRun).order_by(models.BacktestRun.created_at).all()
        return [_backtest_from_orm(row) for row in rows]

    def get_backtest_run(self, backtest_run_id: str) -> BacktestRun | None:
        row = self.session.get(models.BacktestRun, backtest_run_id)
        return _backtest_from_orm(row) if row else None

    def create_backtest_run(self, run: BacktestRun) -> BacktestRun:
        row = models.BacktestRun(
            backtest_run_id=run.backtest_run_id or str(uuid.uuid4()),
            strategy_id=run.strategy_id,
            version_id=run.version_id,
            dataset_scope=run.dataset_scope,
            execution_engine=str(run.execution_engine),
            parameter_set=_jsonable(run.parameter_set),
            market_regime_coverage=_jsonable(run.market_regime_coverage),
            sample_split_plan=_jsonable(run.sample_split_plan),
            cost_model_ref=run.cost_model_ref,
            validation_methodology=_jsonable(run.validation_methodology),
            stress_test_scenarios=_jsonable(run.stress_test_scenarios),
            metrics_summary=(run.metrics_summary.model_dump(mode="json") if run.metrics_summary is not None else None),
            run_status=run.run_status,
            eligibility_result=(
                run.eligibility_result.model_dump(mode="json") if run.eligibility_result is not None else None
            ),
        )
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return _backtest_from_orm(row)


class OptimizationRepository:
    def __init__(self, session: Session):
        self.session = session

    def list_runs(self) -> list[OptimizationRun]:
        rows = self.session.query(models.OptimizationRun).order_by(models.OptimizationRun.created_at).all()
        return [_optimization_from_orm(row) for row in rows]

    def get_run(self, optimization_run_id: str) -> OptimizationRun | None:
        row = self.session.get(models.OptimizationRun, optimization_run_id)
        return _optimization_from_orm(row) if row else None

    def create_run(self, run: OptimizationRun) -> OptimizationRun:
        row = models.OptimizationRun(
            optimization_run_id=run.optimization_run_id or str(uuid.uuid4()),
            strategy_id=run.strategy_id,
            version_id=run.version_id,
            search_space_ref=run.search_space_ref,
            optimization_method=run.optimization_method,
            best_candidate_summary=_jsonable(run.best_candidate_summary),
            run_status=run.run_status,
        )
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return _optimization_from_orm(row)


class IngestionRepository:
    def __init__(self, session: Session):
        self.session = session

    def list_jobs(self) -> list[IngestionJob]:
        rows = self.session.query(models.IngestionJob).order_by(models.IngestionJob.created_at).all()
        return [_ingestion_job_from_orm(row) for row in rows]

    def get_job(self, ingestion_job_id: str) -> IngestionJob | None:
        row = self.session.get(models.IngestionJob, ingestion_job_id)
        return _ingestion_job_from_orm(row) if row else None

    def create_job(self, job: IngestionJob) -> IngestionJob:
        row = models.IngestionJob(
            ingestion_job_id=job.ingestion_job_id or str(uuid.uuid4()),
            source_family=job.source_family,
            source_name=job.source_name,
            job_type=job.job_type,
            schedule_mode=job.schedule_mode,
            job_status=job.job_status,
            input_window=_jsonable(job.input_window),
            target_symbols=job.target_symbols,
            output_ref=job.output_ref,
            error_summary=job.error_summary,
            execution_summary=_jsonable(job.execution_summary),
        )
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return _ingestion_job_from_orm(row)

    def update_job(self, ingestion_job_id: str, **fields) -> IngestionJob | None:
        row = self.session.get(models.IngestionJob, ingestion_job_id)
        if row is None:
            return None
        for key, value in fields.items():
            setattr(row, key, _jsonable(value))
        row.updated_at = _utcnow()
        self.session.commit()
        self.session.refresh(row)
        return _ingestion_job_from_orm(row)


class PaperRunRepository:
    def __init__(self, session: Session):
        self.session = session

    def list_paper_runs(self) -> list[PaperRun]:
        rows = self.session.query(models.PaperRun).order_by(models.PaperRun.created_at).all()
        return [_paper_run_from_orm(row) for row in rows]

    def get_paper_run(self, paper_run_id: str) -> PaperRun | None:
        row = self.session.get(models.PaperRun, paper_run_id)
        return _paper_run_from_orm(row) if row else None

    def update_paper_run_status(self, paper_run_id: str, paper_status: str) -> PaperRun | None:
        row = self.session.get(models.PaperRun, paper_run_id)
        if row is None:
            return None
        row.paper_status = paper_status
        self.session.commit()
        self.session.refresh(row)
        return _paper_run_from_orm(row)

    def update_paper_run(self, paper_run_id: str, **fields) -> PaperRun | None:
        row = self.session.get(models.PaperRun, paper_run_id)
        if row is None:
            return None
        for key, value in fields.items():
            setattr(row, key, _jsonable(value))
        self.session.commit()
        self.session.refresh(row)
        return _paper_run_from_orm(row)

    def create_paper_run(self, run: PaperRun) -> PaperRun:
        row = models.PaperRun(
            paper_run_id=run.paper_run_id or str(uuid.uuid4()),
            strategy_id=run.strategy_id,
            version_id=run.version_id,
            exchange=str(run.exchange),
            symbol_scope=run.symbol_scope,
            candidate_symbols=run.candidate_symbols,
            selection_basis=run.selection_basis,
            run_window=_jsonable(run.run_window),
            execution_profile=_jsonable(run.execution_profile),
            gate_decision_ref=run.gate_decision_ref,
            paper_metrics_summary=_jsonable(run.paper_metrics_summary),
            paper_status=run.paper_status,
        )
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return _paper_run_from_orm(row)


class RiskProfileRepository:
    def __init__(self, session: Session):
        self.session = session

    def list_profiles(self) -> list[RiskProfile]:
        rows = self.session.query(models.RiskProfile).order_by(models.RiskProfile.created_at).all()
        return [_risk_profile_from_orm(row) for row in rows]

    def get_profile(self, risk_profile_id: str) -> RiskProfile | None:
        row = self.session.get(models.RiskProfile, risk_profile_id)
        return _risk_profile_from_orm(row) if row else None

    def create_profile(self, profile: RiskProfile) -> RiskProfile:
        row = models.RiskProfile(
            risk_profile_id=profile.risk_profile_id or str(uuid.uuid4()),
            single_trade_risk_limit=profile.single_trade_risk_limit,
            max_symbol_exposure=profile.max_symbol_exposure,
            max_total_exposure=profile.max_total_exposure,
            max_open_positions=profile.max_open_positions,
            max_leverage=profile.max_leverage,
            daily_loss_limit=profile.daily_loss_limit,
            weekly_loss_limit=profile.weekly_loss_limit,
            drawdown_limit=profile.drawdown_limit,
            hard_stop_drawdown_limit=profile.hard_stop_drawdown_limit,
            consecutive_loss_limit=profile.consecutive_loss_limit,
            api_failure_limit=profile.api_failure_limit,
            api_failure_window_minutes=profile.api_failure_window_minutes,
            market_scope=profile.market_scope,
            config_source=profile.config_source,
        )
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return _risk_profile_from_orm(row)

    def update_profile(self, risk_profile_id: str, body: RiskProfileUpdate) -> RiskProfile | None:
        row = self.session.get(models.RiskProfile, risk_profile_id)
        if row is None:
            return None
        for field_name, value in body.model_dump(exclude_unset=True).items():
            setattr(row, field_name, value)
        self.session.commit()
        self.session.refresh(row)
        return _risk_profile_from_orm(row)


class NotificationRepository:
    def __init__(self, session: Session):
        self.session = session

    def list_notifications(
        self,
        *,
        delivery_status: str | None = None,
        severity: str | None = None,
        event_type: str | None = None,
        channel_group: str | None = None,
        only_due: bool = False,
        limit: int | None = None,
    ) -> list[NotificationOutboxItem]:
        query = self.session.query(models.NotificationOutbox)
        if delivery_status is not None:
            query = query.filter(models.NotificationOutbox.delivery_status == delivery_status)
        if severity is not None:
            query = query.filter(models.NotificationOutbox.severity == severity)
        if event_type is not None:
            query = query.filter(models.NotificationOutbox.event_type == event_type)
        if channel_group is not None:
            query = query.filter(models.NotificationOutbox.channel_group == channel_group)
        if only_due:
            now = _utcnow()
            query = query.filter(
                models.NotificationOutbox.delivery_status.in_(("pending_adapter", "pending_retry"))
            ).filter(
                (models.NotificationOutbox.next_attempt_at.is_(None))
                | (models.NotificationOutbox.next_attempt_at <= now)
            )
        query = query.order_by(models.NotificationOutbox.created_at)
        if limit is not None:
            query = query.limit(limit)
        rows = query.all()
        return [_notification_from_orm(row) for row in rows]

    def get_notification(self, notification_id: str) -> NotificationOutboxItem | None:
        row = self.session.get(models.NotificationOutbox, notification_id)
        return _notification_from_orm(row) if row else None

    def create_notification(self, item: NotificationOutboxItem) -> NotificationOutboxItem:
        existing = self.session.get(models.NotificationOutbox, item.notification_id)
        if existing is not None:
            return _notification_from_orm(existing)
        values = {
            "notification_id": item.notification_id,
            "event_type": item.event_type,
            "severity": item.severity,
            "channel_group": item.channel_group,
            "delivery_channels": item.delivery_channels,
            "subject": item.subject,
            "body": item.body,
            "source_ref": item.source_ref,
            "delivery_status": item.delivery_status,
            "delivery_attempts": item.delivery_attempts,
            "next_attempt_at": item.next_attempt_at,
            "last_attempt_at": item.last_attempt_at,
            "attempt_history": _jsonable(item.attempt_history),
            "last_error": item.last_error,
            "delivered_at": item.delivered_at,
        }
        if item.created_at is not None:
            values["created_at"] = item.created_at
        if item.updated_at is not None:
            values["updated_at"] = item.updated_at
        row = models.NotificationOutbox(**values)
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return _notification_from_orm(row)

    def update_delivery(
        self,
        notification_id: str,
        *,
        delivery_status: str,
        last_error: str | None = None,
        next_attempt_at: datetime | None = None,
    ) -> NotificationOutboxItem | None:
        row = self.session.get(models.NotificationOutbox, notification_id)
        if row is None:
            return None
        now = _utcnow()
        row.delivery_status = delivery_status
        row.delivery_attempts = (row.delivery_attempts or 0) + 1
        row.last_error = last_error
        row.next_attempt_at = next_attempt_at
        row.last_attempt_at = now
        row.updated_at = now
        if delivery_status == "sent":
            row.delivered_at = now
        self.session.commit()
        self.session.refresh(row)
        return _notification_from_orm(row)

    def update_notification(self, notification_id: str, **fields) -> NotificationOutboxItem | None:
        row = self.session.get(models.NotificationOutbox, notification_id)
        if row is None:
            return None
        for key, value in fields.items():
            if isinstance(value, datetime) or value is None:
                setattr(row, key, value)
            else:
                setattr(row, key, _jsonable(value))
        row.updated_at = _utcnow()
        self.session.commit()
        self.session.refresh(row)
        return _notification_from_orm(row)


class DecisionMemoryRepository:
    def __init__(self, session: Session):
        self.session = session

    def list_entries(
        self,
        *,
        scope_type: str | None = None,
        scope_id: str | None = None,
        decision_type: str | None = None,
        verdict: str | None = None,
    ) -> list[DecisionMemoryEntry]:
        query = self.session.query(models.DecisionMemoryEntry)
        if scope_type is not None:
            query = query.filter(models.DecisionMemoryEntry.scope_type == scope_type)
        if scope_id is not None:
            query = query.filter(models.DecisionMemoryEntry.scope_id == scope_id)
        if decision_type is not None:
            query = query.filter(models.DecisionMemoryEntry.decision_type == decision_type)
        if verdict is not None:
            query = query.filter(models.DecisionMemoryEntry.verdict == verdict)
        rows = query.order_by(models.DecisionMemoryEntry.created_at).all()
        return [_decision_memory_from_orm(row) for row in rows]

    def create_entry(self, entry: DecisionMemoryEntry) -> DecisionMemoryEntry:
        row = models.DecisionMemoryEntry(
            decision_memory_id=entry.decision_memory_id or str(uuid.uuid4()),
            scope_type=entry.scope_type,
            scope_id=entry.scope_id,
            decision_type=entry.decision_type,
            verdict=entry.verdict,
            summary=entry.summary,
            tags=_jsonable(entry.tags),
            evidence_refs=_jsonable(entry.evidence_refs),
            context_payload=_jsonable(entry.context_payload),
            created_at=entry.created_at or _utcnow(),
        )
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return _decision_memory_from_orm(row)


class DecisionSnapshotRepository:
    def __init__(self, session: Session):
        self.session = session

    def list_snapshots(
        self,
        *,
        paper_run_id: str | None = None,
        symbol: str | None = None,
        pipeline_status: str | None = None,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> list[DecisionSnapshot]:
        query = self.session.query(models.DecisionSnapshot)
        if paper_run_id is not None:
            query = query.filter(models.DecisionSnapshot.paper_run_id == paper_run_id)
        if symbol is not None:
            query = query.filter(models.DecisionSnapshot.symbol == symbol)
        if pipeline_status is not None:
            query = query.filter(models.DecisionSnapshot.pipeline_status == pipeline_status)
        if since is not None:
            query = query.filter(models.DecisionSnapshot.cycle_time >= since)
        query = query.order_by(models.DecisionSnapshot.cycle_time.desc())
        if limit is not None:
            query = query.limit(limit)
        rows = query.all()
        return [_decision_snapshot_from_orm(row) for row in rows]

    def create_snapshot(self, snapshot: DecisionSnapshot) -> DecisionSnapshot:
        row = models.DecisionSnapshot(
            decision_snapshot_id=snapshot.decision_snapshot_id or str(uuid.uuid4()),
            paper_run_id=snapshot.paper_run_id,
            symbol=snapshot.symbol,
            action=snapshot.action,
            pipeline_status=snapshot.pipeline_status,
            reason=snapshot.reason,
            decision_trace=_jsonable(snapshot.decision_trace),
            cycle_time=snapshot.cycle_time,
        )
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return _decision_snapshot_from_orm(row)


class ReviewRepository:
    def __init__(self, session: Session):
        self.session = session
        self.strategy_repo = StrategyRepository(session)

    def list_reports(self) -> list[ReviewReport]:
        rows = self.session.query(models.ReviewReport).order_by(models.ReviewReport.created_at).all()
        return [_review_report_from_orm(row) for row in rows]

    def get_report(self, review_report_id: str) -> ReviewReport | None:
        row = self.session.get(models.ReviewReport, review_report_id)
        return _review_report_from_orm(row) if row else None

    def create_report(self, report: ReviewReport) -> ReviewReport:
        row = models.ReviewReport(
            review_report_id=report.review_report_id or str(uuid.uuid4()),
            report_date=report.report_date,
            scope_type=report.scope_type,
            strategy_refs=report.strategy_refs,
            worst_performer_refs=report.worst_performer_refs,
            failure_patterns=report.failure_patterns,
            deviation_analysis=report.deviation_analysis,
            recommendations=report.recommendations,
            report_status=report.report_status,
        )
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return _review_report_from_orm(row)

    def list_failures(
        self,
        *,
        strategy_id: str | None = None,
        idea_id: str | None = None,
        failure_type: str | None = None,
        limit: int = 50,
    ) -> list[FailureRecord]:
        query = self.session.query(models.FailureRecord)
        if strategy_id is not None:
            query = query.filter(models.FailureRecord.strategy_id == strategy_id)
        if idea_id is not None:
            query = query.filter(models.FailureRecord.idea_id == idea_id)
        if failure_type is not None:
            query = query.filter(models.FailureRecord.failure_type == failure_type)
        rows = query.order_by(models.FailureRecord.created_at.desc()).limit(limit).all()
        return [_failure_record_from_orm(row) for row in rows]

    def create_failure(self, record: FailureRecord) -> FailureRecord:
        row = models.FailureRecord(
            failure_record_id=record.failure_record_id or str(uuid.uuid4()),
            strategy_id=record.strategy_id,
            idea_id=record.idea_id,
            version_id=record.version_id,
            origin_run_type=record.origin_run_type,
            origin_run_id=record.origin_run_id,
            failure_type=record.failure_type,
            failure_summary=record.failure_summary,
            evidence_refs=record.evidence_refs,
            recommended_change=record.recommended_change,
        )
        self.session.add(row)
        if record.strategy_id is not None:
            self.strategy_repo.append_failure_record(
                strategy_id=record.strategy_id,
                failure_summary=record.failure_summary,
                recommended_change=record.recommended_change,
            )
        self.session.commit()
        self.session.refresh(row)
        return _failure_record_from_orm(row)


class AgentTaskRepository:
    def __init__(self, session: Session):
        self.session = session

    def list_tasks(self, *, limit: int = 50) -> list[AgentTask]:
        rows = self.session.query(models.AgentTask).order_by(models.AgentTask.created_at.desc()).limit(limit).all()
        return [_agent_task_from_orm(row) for row in rows]

    def list_tasks_by_agent_type(self, *, agent_type: str, limit: int = 50) -> list[AgentTask]:
        rows = (
            self.session.query(models.AgentTask)
            .filter(models.AgentTask.agent_type == agent_type)
            .order_by(models.AgentTask.created_at.desc())
            .limit(limit)
            .all()
        )
        return [_agent_task_from_orm(row) for row in rows]

    def has_verified_testnet_acceptance(self) -> bool:
        """Acceptance proof must not depend on the generic recent-task window.

        Trading status used to scan only the latest 50 AgentTasks. Once other
        tasks pushed the completed Top20 acceptance out of that window, automatic
        Binance simulation stayed blocked even though acceptance had passed.
        """
        rows = (
            self.session.query(models.AgentTask)
            .filter(
                models.AgentTask.task_type == "testnet_acceptance",
                models.AgentTask.task_status == "completed",
            )
            .order_by(models.AgentTask.created_at.desc())
            .limit(20)
            .all()
        )
        for row in rows:
            payload = row.output_payload if isinstance(row.output_payload, dict) else {}
            if (
                payload.get("final_open_position_count") == 0
                and payload.get("final_open_order_count") == 0
                and len(payload.get("completed_symbols") or []) == 20
            ):
                return True
        return False

    def get_task(self, agent_task_id: str) -> AgentTask | None:
        row = self.session.get(models.AgentTask, agent_task_id)
        return _agent_task_from_orm(row) if row else None

    def create_task(self, task: AgentTask) -> AgentTask:
        row = models.AgentTask(
            agent_task_id=task.agent_task_id or str(uuid.uuid4()),
            agent_type=task.agent_type,
            task_type=task.task_type,
            input_ref=task.input_ref,
            output_ref=task.output_ref,
            input_payload=_jsonable(task.input_payload),
            output_payload=_jsonable(task.output_payload),
            priority=task.priority,
            task_status=task.task_status,
            error_summary=task.error_summary,
            executor_name=task.executor_name,
            attempt_history=_jsonable(task.attempt_history),
            provider_trace=_jsonable(task.provider_trace),
            schema_validation_status=task.schema_validation_status,
            scheduled_at=task.scheduled_at,
        )
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return _agent_task_from_orm(row)

    def update_task(self, agent_task_id: str, **fields) -> AgentTask | None:
        row = self.session.get(models.AgentTask, agent_task_id)
        if row is None:
            return None
        for key, value in fields.items():
            setattr(row, key, _jsonable(value))
        self.session.commit()
        self.session.refresh(row)
        return _agent_task_from_orm(row)


class ExecutionRepository:
    def __init__(self, session: Session):
        self.session = session

    def list_live_runs(self) -> list[LiveRun]:
        rows = self.session.query(models.LiveRun).order_by(models.LiveRun.created_at).all()
        return [_live_run_from_orm(row) for row in rows]

    def create_live_run(self, run: LiveRun) -> LiveRun:
        row = models.LiveRun(
            live_run_id=run.live_run_id or str(uuid.uuid4()),
            strategy_id=run.strategy_id,
            version_id=run.version_id,
            exchange=str(run.exchange),
            capital_tier=run.capital_tier,
            live_status=run.live_status,
            validation_backtest_run_id=run.validation_backtest_run_id,
            risk_profile_ref=run.risk_profile_ref,
            live_metrics_summary=_jsonable(run.live_metrics_summary),
        )
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return _live_run_from_orm(row)

    def list_orders(self) -> list[OrderExecution]:
        rows = self.session.query(models.OrderExecution).order_by(models.OrderExecution.created_at).all()
        return [_order_execution_from_orm(row) for row in rows]

    def create_order(self, order: OrderExecution) -> OrderExecution:
        row = models.OrderExecution(
            order_execution_id=order.order_execution_id or str(uuid.uuid4()),
            strategy_id=order.strategy_id,
            version_id=order.version_id,
            symbol=order.symbol,
            direction=str(order.direction),
            execution_status=order.execution_status,
            stoploss_present=order.stoploss_present,
            close_only_mode=order.close_only_mode,
            rejection_reason=order.rejection_reason,
            rejection_codes=order.rejection_codes,
            entry_context=_jsonable(order.entry_context),
            stoploss_plan=_jsonable(order.stoploss_plan),
            takeprofit_plan=_jsonable(order.takeprofit_plan),
            risk_profile_ref=order.risk_profile_ref,
            validation_backtest_run_id=order.validation_backtest_run_id,
            paper_run_id=order.paper_run_id,
            live_run_id=order.live_run_id,
            signal_ensemble_id=order.signal_ensemble_id,
            meta_label_id=order.meta_label_id,
            veto_result=_jsonable(order.veto_result),
            evaluated_risk_state=(
                order.evaluated_risk_state.model_dump(mode="json") if order.evaluated_risk_state is not None else {}
            ),
            gateway_name=order.gateway_name,
            gateway_order_id=order.gateway_order_id,
            gateway_status=order.gateway_status,
            lifecycle_history=_jsonable(order.lifecycle_history),
            reconciliation_status=order.reconciliation_status,
            last_gateway_update_at=order.last_gateway_update_at,
        )
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return _order_execution_from_orm(row)

    def get_live_run(self, live_run_id: str) -> LiveRun | None:
        row = self.session.get(models.LiveRun, live_run_id)
        return _live_run_from_orm(row) if row else None

    def get_order(self, order_execution_id: str) -> OrderExecution | None:
        row = self.session.get(models.OrderExecution, order_execution_id)
        return _order_execution_from_orm(row) if row else None

    def find_order_by_gateway_order_id(self, gateway_order_id: str) -> OrderExecution | None:
        row = (
            self.session.query(models.OrderExecution)
            .filter(models.OrderExecution.gateway_order_id == gateway_order_id)
            .one_or_none()
        )
        return _order_execution_from_orm(row) if row else None

    def find_latest_filled_entry_order(self, *, run_type: str, run_id: str, symbol: str) -> OrderExecution | None:
        query = (
            self.session.query(models.OrderExecution)
            .filter(
                models.OrderExecution.symbol == symbol,
                models.OrderExecution.execution_status == "filled",
                models.OrderExecution.close_only_mode.is_(False),
            )
            .order_by(models.OrderExecution.created_at.desc())
        )
        if run_type == "paper":
            query = query.filter(models.OrderExecution.paper_run_id == run_id)
        elif run_type == "live":
            query = query.filter(models.OrderExecution.live_run_id == run_id)
        else:
            return None
        row = query.first()
        return _order_execution_from_orm(row) if row else None

    def update_order(self, order_execution_id: str, **fields) -> OrderExecution | None:
        row = self.session.get(models.OrderExecution, order_execution_id)
        if row is None:
            return None
        for key, value in fields.items():
            setattr(row, key, _jsonable(value) if not isinstance(value, datetime) else value)
        self.session.commit()
        self.session.refresh(row)
        return _order_execution_from_orm(row)

    def list_positions(self) -> list[PositionSnapshot]:
        rows = self.session.query(models.PositionSnapshot).order_by(models.PositionSnapshot.snapshot_time).all()
        return [_position_snapshot_from_orm(row) for row in rows]

    def list_positions_for_run(self, *, run_type: str, run_id: str) -> list[PositionSnapshot]:
        rows = (
            self.session.query(models.PositionSnapshot)
            .filter(models.PositionSnapshot.run_type == run_type, models.PositionSnapshot.run_id == run_id)
            .order_by(models.PositionSnapshot.snapshot_time)
            .all()
        )
        return [_position_snapshot_from_orm(row) for row in rows]

    def list_latest_positions_for_run(
        self,
        *,
        run_type: str,
        run_id: str,
        include_closed: bool = False,
    ) -> list[PositionSnapshot]:
        latest_by_symbol: dict[str, PositionSnapshot] = {}
        for snapshot in self.list_positions_for_run(run_type=run_type, run_id=run_id):
            current = latest_by_symbol.get(snapshot.symbol)
            snap_time = _ensure_utc(snapshot.snapshot_time) or _utcnow()
            current_time = _ensure_utc(current.snapshot_time) if current is not None else None
            if current is None or current_time is None or snap_time >= current_time:
                latest_by_symbol[snapshot.symbol] = snapshot
        positions = list(latest_by_symbol.values())
        if include_closed:
            return positions
        return [position for position in positions if abs(position.quantity) > 0]

    def create_position_snapshot(self, snapshot: PositionSnapshot) -> PositionSnapshot:
        row = models.PositionSnapshot(
            position_snapshot_id=snapshot.position_snapshot_id or str(uuid.uuid4()),
            run_type=snapshot.run_type,
            run_id=snapshot.run_id,
            symbol=snapshot.symbol,
            side=str(snapshot.side),
            quantity=snapshot.quantity,
            entry_price=snapshot.entry_price,
            mark_price=snapshot.mark_price,
            unrealized_pnl=snapshot.unrealized_pnl,
            snapshot_time=snapshot.snapshot_time,
        )
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return _position_snapshot_from_orm(row)

    def list_account_snapshots(self, *, live_run_id: str | None = None) -> list[ExchangeAccountSnapshot]:
        query = self.session.query(models.ExchangeAccountSnapshot)
        if live_run_id is not None:
            query = query.filter(models.ExchangeAccountSnapshot.live_run_id == live_run_id)
        rows = query.order_by(models.ExchangeAccountSnapshot.snapshot_time).all()
        return [_account_snapshot_from_orm(row) for row in rows]

    def create_account_snapshot(self, snapshot: ExchangeAccountSnapshot) -> ExchangeAccountSnapshot:
        row = models.ExchangeAccountSnapshot(
            snapshot_id=snapshot.snapshot_id or str(uuid.uuid4()),
            live_run_id=snapshot.live_run_id,
            exchange=snapshot.exchange,
            wallet_balance=snapshot.wallet_balance,
            available_balance=snapshot.available_balance,
            margin_balance=snapshot.margin_balance,
            unrealized_pnl=snapshot.unrealized_pnl,
            open_position_count=snapshot.open_position_count,
            source_ref=snapshot.source_ref,
            snapshot_time=snapshot.snapshot_time or _utcnow(),
        )
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return _account_snapshot_from_orm(row)

    def list_reconciliation_records(self, *, live_run_id: str | None = None) -> list[ReconciliationRecord]:
        query = self.session.query(models.ReconciliationRecord)
        if live_run_id is not None:
            query = query.filter(models.ReconciliationRecord.live_run_id == live_run_id)
        rows = query.order_by(models.ReconciliationRecord.created_at).all()
        return [_reconciliation_from_orm(row) for row in rows]

    def create_reconciliation_record(self, record: ReconciliationRecord) -> ReconciliationRecord:
        row = models.ReconciliationRecord(
            reconciliation_id=record.reconciliation_id or str(uuid.uuid4()),
            live_run_id=record.live_run_id,
            reconciliation_status=record.reconciliation_status,
            open_order_count=record.open_order_count,
            position_mismatches=_jsonable(record.position_mismatches),
            notes=_jsonable(record.notes),
            created_at=record.created_at or _utcnow(),
        )
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return _reconciliation_from_orm(row)

    def list_signal_ensembles(self) -> list[SignalEnsemble]:
        rows = self.session.query(models.SignalEnsemble).order_by(models.SignalEnsemble.created_at).all()
        return [_signal_ensemble_from_orm(row) for row in rows]

    def create_signal_ensemble(self, ensemble: SignalEnsemble) -> SignalEnsemble:
        row = models.SignalEnsemble(
            ensemble_id=ensemble.ensemble_id,
            strategy_refs=ensemble.strategy_refs,
            fusion_method=ensemble.fusion_method,
            correlation_matrix_ref=ensemble.correlation_matrix_ref,
            raw_votes=_jsonable(ensemble.raw_votes),
            fused_direction=str(ensemble.fused_direction) if ensemble.fused_direction else None,
            fused_confidence=ensemble.fused_confidence,
            ensemble_status=str(ensemble.ensemble_status),
            created_at=ensemble.created_at,
        )
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return _signal_ensemble_from_orm(row)

    def create_meta_label(self, meta_label: MetaLabel) -> MetaLabel:
        row = models.MetaLabel(
            meta_label_id=meta_label.meta_label_id,
            ensemble_id=meta_label.ensemble_id,
            triple_barrier_result=(str(meta_label.triple_barrier_result) if meta_label.triple_barrier_result else None),
            bet_decision=str(meta_label.bet_decision),
            position_size_fraction=meta_label.position_size_fraction,
            model_ref=meta_label.model_ref,
            training_window_ref=meta_label.training_window_ref,
        )
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return _meta_label_from_orm(row)

    def list_meta_labels(self) -> list[MetaLabel]:
        rows = self.session.query(models.MetaLabel).all()
        return [_meta_label_from_orm(row) for row in rows]

    def get_meta_label(self, meta_label_id: str) -> MetaLabel | None:
        row = self.session.get(models.MetaLabel, meta_label_id)
        return _meta_label_from_orm(row) if row else None

    def get_signal_ensemble(self, ensemble_id: str) -> SignalEnsemble | None:
        row = self.session.get(models.SignalEnsemble, ensemble_id)
        return _signal_ensemble_from_orm(row) if row else None
