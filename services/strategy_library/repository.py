"""Repositories and mappers for the strategy-intake chain."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from shared.models import (
    BacktestRun,
    GateDecision,
    IngestionJob,
    PaperRun,
    StrategyContract,
    StrategyCreate,
    StrategyDraft,
    StrategyIdea,
    StrategyRules,
    StrategyUpdate,
    StrategyVersion,
)

from . import models


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _jsonable(value):
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
        market=row.market,
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
        market=row.market,
        symbol_scope=row.symbol_scope,
        timeframe=row.timeframe,
        market_regime=row.market_regime,
        risk_level=row.risk_level,
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
        market=row.market,
        symbol_scope=row.symbol_scope,
        timeframe=row.timeframe,
        market_regime=row.market_regime,
        risk_level=row.risk_level,
        rules=_strategy_rules_from_orm(row),
        strategy_status=row.strategy_status,
        backtest_status=row.backtest_status,
        paper_status=row.paper_status,
        live_status=row.live_status,
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


def _gate_from_payload(
    strategy_id: str, payload: dict | GateDecision | None
) -> GateDecision | None:
    if payload is None:
        return None
    if isinstance(payload, GateDecision):
        return payload
    normalized = dict(payload)
    normalized.setdefault("strategy_id", strategy_id)
    return GateDecision(**normalized)


def _backtest_from_orm(row: models.BacktestRun) -> BacktestRun:
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
        metrics_summary=row.metrics_summary,
        run_status=row.run_status,
        eligibility_result=_gate_from_payload(row.strategy_id, row.eligibility_result),
        created_at=row.created_at,
        updated_at=row.updated_at,
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
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _paper_run_from_orm(row: models.PaperRun) -> PaperRun:
    return PaperRun(
        paper_run_id=row.paper_run_id,
        strategy_id=row.strategy_id,
        version_id=row.version_id,
        exchange=row.exchange,
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


def _draft_to_strategy_key(title: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_]+", "_", title).strip("_")
    base = normalized or "strategy"
    return f"{base}_{uuid.uuid4().hex[:8]}"


class StrategyRepository:
    """Repository for the first strategy lifecycle slice."""

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
            metrics_summary=(
                run.metrics_summary.model_dump(mode="json")
                if run.metrics_summary is not None
                else None
            ),
            run_status=run.run_status,
            eligibility_result=(
                run.eligibility_result.model_dump(mode="json")
                if run.eligibility_result is not None
                else None
            ),
        )
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return _backtest_from_orm(row)


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
            input_window=job.input_window,
            target_symbols=job.target_symbols,
            output_ref=job.output_ref,
            error_summary=job.error_summary,
        )
        self.session.add(row)
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

    def create_paper_run(self, run: PaperRun) -> PaperRun:
        row = models.PaperRun(
            paper_run_id=run.paper_run_id or str(uuid.uuid4()),
            strategy_id=run.strategy_id,
            version_id=run.version_id,
            exchange=str(run.exchange),
            symbol_scope=run.symbol_scope,
            candidate_symbols=run.candidate_symbols,
            selection_basis=run.selection_basis,
            run_window=run.run_window,
            execution_profile=run.execution_profile,
            gate_decision_ref=run.gate_decision_ref,
            paper_metrics_summary=run.paper_metrics_summary,
            paper_status=run.paper_status,
        )
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return _paper_run_from_orm(row)
