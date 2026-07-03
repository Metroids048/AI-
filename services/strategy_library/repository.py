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
    EnsembleStatus,
    Exchange,
    FailureRecord,
    GateDecision,
    IngestionJob,
    LiveRun,
    Market,
    MetaLabel,
    OptimizationRun,
    OrderExecution,
    PaperRun,
    PositionSnapshot,
    ReviewReport,
    RiskLevel,
    RiskProfile,
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
        intake_bucket=row.intake_bucket,
        created_at=row.created_at,
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
        backtest_status=RunStatus(row.backtest_status),
        paper_status=RunStatus(row.paper_status),
        live_status=RunStatus(row.live_status),
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
        market_scope=row.market_scope,
        config_source=row.config_source,
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


def _failure_record_from_orm(row: models.FailureRecord) -> FailureRecord:
    return FailureRecord(
        failure_record_id=row.failure_record_id,
        strategy_id=row.strategy_id,
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
        snapshot_time=row.snapshot_time,
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

    def delete_strategy(self, strategy_id: str) -> bool:
        row = self.session.get(models.Strategy, strategy_id)
        if row is None:
            return False
        self.session.delete(row)
        self.session.commit()
        return True

    def list_versions(self) -> list[StrategyVersion]:
        rows = self.session.query(models.StrategyVersion).order_by(models.StrategyVersion.created_at).all()
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
            market_scope=profile.market_scope,
            config_source=profile.config_source,
        )
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return _risk_profile_from_orm(row)


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

    def list_failures(self) -> list[FailureRecord]:
        rows = self.session.query(models.FailureRecord).order_by(models.FailureRecord.created_at).all()
        return [_failure_record_from_orm(row) for row in rows]

    def create_failure(self, record: FailureRecord) -> FailureRecord:
        row = models.FailureRecord(
            failure_record_id=record.failure_record_id or str(uuid.uuid4()),
            strategy_id=record.strategy_id,
            version_id=record.version_id,
            origin_run_type=record.origin_run_type,
            origin_run_id=record.origin_run_id,
            failure_type=record.failure_type,
            failure_summary=record.failure_summary,
            evidence_refs=record.evidence_refs,
            recommended_change=record.recommended_change,
        )
        self.session.add(row)
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

    def list_tasks(self) -> list[AgentTask]:
        rows = self.session.query(models.AgentTask).order_by(models.AgentTask.created_at).all()
        return [_agent_task_from_orm(row) for row in rows]

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
        )
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return _order_execution_from_orm(row)

    def list_positions(self) -> list[PositionSnapshot]:
        rows = self.session.query(models.PositionSnapshot).order_by(models.PositionSnapshot.snapshot_time).all()
        return [_position_snapshot_from_orm(row) for row in rows]

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
