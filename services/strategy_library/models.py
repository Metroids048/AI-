"""SQLAlchemy ORM for the first core vertical slice.

These models persist the strategy-intake chain plus the first validation/data
bookkeeping objects:

    StrategyIdea -> StrategyDraft -> Strategy -> StrategyVersion -> BacktestRun
                                                 -> IngestionJob

Cross-layer contracts still live in `shared.models`. The ORM here remains a
storage detail owned by Alembic for relational tables only.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for all relational (Alembic-owned) tables."""


def _uuid_str() -> str:
    return str(uuid.uuid4())


class _RulesColumns:
    """Shared structured rule blocks stored as JSON."""

    entry_rules: Mapped[dict] = mapped_column(JSON, default=dict)
    exit_rules: Mapped[dict] = mapped_column(JSON, default=dict)
    stoploss_rules: Mapped[dict] = mapped_column(JSON, default=dict)
    takeprofit_rules: Mapped[dict] = mapped_column(JSON, default=dict)
    position_rules: Mapped[dict] = mapped_column(JSON, default=dict)


class StrategyIdea(Base):
    __tablename__ = "strategy_ideas"

    idea_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    title: Mapped[str] = mapped_column(String(200))
    source: Mapped[str] = mapped_column(String(60))
    market: Mapped[str] = mapped_column(String(30), default="crypto_perp")
    symbol_scope: Mapped[list[str]] = mapped_column(JSON, default=list)
    hypothesis_summary: Mapped[str] = mapped_column(Text)
    source_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    intake_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    intake_bucket: Mapped[str] = mapped_column(String(40), default="rule_candidate")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Hypothesis(Base):
    __tablename__ = "hypotheses"

    hypothesis_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    strategy_id: Mapped[str | None] = mapped_column(ForeignKey("strategies.id"), nullable=True, index=True)
    idea_id: Mapped[str | None] = mapped_column(ForeignKey("strategy_ideas.idea_id"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(200))
    statement: Mapped[str] = mapped_column(Text)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    benchmark_plan: Mapped[dict] = mapped_column(JSON, default=dict)
    acceptance_criteria: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(30), default="draft")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class StrategyDraft(Base, _RulesColumns):
    __tablename__ = "strategy_drafts"

    draft_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    idea_id: Mapped[str | None] = mapped_column(ForeignKey("strategy_ideas.idea_id"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(200))
    source: Mapped[str] = mapped_column(String(60))
    core_thesis: Mapped[str] = mapped_column(Text)
    market: Mapped[str] = mapped_column(String(30), default="crypto_perp")
    symbol_scope: Mapped[list[str]] = mapped_column(JSON, default=list)
    timeframe: Mapped[str] = mapped_column(String(10), default="1h")
    market_regime: Mapped[str | None] = mapped_column(String(60), nullable=True)
    risk_level: Mapped[str] = mapped_column(String(20), default="medium")
    draft_status: Mapped[str] = mapped_column(String(30), default="drafting")
    review_notes: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class Strategy(Base):
    __tablename__ = "strategies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    strategy_key: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    source: Mapped[str] = mapped_column(String(60))

    core_thesis: Mapped[str] = mapped_column(Text, default="")
    market: Mapped[str] = mapped_column(String(30), default="crypto_perp")
    symbol_scope: Mapped[list[str]] = mapped_column(JSON, default=list)
    timeframe: Mapped[str] = mapped_column(String(10), default="1h")
    market_regime: Mapped[str | None] = mapped_column(String(60), nullable=True)
    risk_level: Mapped[str] = mapped_column(String(20), default="medium")

    entry_rules: Mapped[dict] = mapped_column(JSON, default=dict)
    exit_rules: Mapped[dict] = mapped_column(JSON, default=dict)
    stoploss_rules: Mapped[dict] = mapped_column(JSON, default=dict)
    takeprofit_rules: Mapped[dict] = mapped_column(JSON, default=dict)
    position_rules: Mapped[dict] = mapped_column(JSON, default=dict)

    strategy_status: Mapped[str] = mapped_column(String(30), default="drafting")
    backtest_status: Mapped[str] = mapped_column(String(20), default="not_started")
    paper_status: Mapped[str] = mapped_column(String(20), default="not_started")
    live_status: Mapped[str] = mapped_column(String(20), default="not_started")

    failure_reasons: Mapped[list] = mapped_column(JSON, default=list)
    iteration_history: Mapped[list] = mapped_column(JSON, default=list)

    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class StrategyVersion(Base):
    __tablename__ = "strategy_versions"

    version_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    strategy_id: Mapped[str] = mapped_column(ForeignKey("strategies.id"), index=True)
    version_label: Mapped[str] = mapped_column(String(40))
    change_summary: Mapped[str] = mapped_column(Text)
    code_artifact_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    backtest_run_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    strategy_id: Mapped[str] = mapped_column(ForeignKey("strategies.id"), index=True)
    version_id: Mapped[str | None] = mapped_column(
        ForeignKey("strategy_versions.version_id"), nullable=True, index=True
    )
    dataset_scope: Mapped[str | None] = mapped_column(String(255), nullable=True)
    execution_engine: Mapped[str] = mapped_column(String(30))
    parameter_set: Mapped[dict] = mapped_column(JSON, default=dict)
    market_regime_coverage: Mapped[list[str]] = mapped_column(JSON, default=list)
    sample_split_plan: Mapped[dict] = mapped_column(JSON, default=dict)
    cost_model_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    validation_methodology: Mapped[dict] = mapped_column(JSON, default=dict)
    stress_test_scenarios: Mapped[list[str]] = mapped_column(JSON, default=list)
    metrics_summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    run_status: Mapped[str] = mapped_column(String(30), default="queued")
    eligibility_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"

    ingestion_job_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    source_family: Mapped[str] = mapped_column(String(10))
    source_name: Mapped[str] = mapped_column(String(80))
    job_type: Mapped[str] = mapped_column(String(80))
    schedule_mode: Mapped[str] = mapped_column(String(40))
    job_status: Mapped[str] = mapped_column(String(30), default="pending")
    input_window: Mapped[dict] = mapped_column(JSON, default=dict)
    target_symbols: Mapped[list[str]] = mapped_column(JSON, default=list)
    output_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    execution_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class PaperRun(Base):
    __tablename__ = "paper_runs"

    paper_run_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    strategy_id: Mapped[str] = mapped_column(ForeignKey("strategies.id"), index=True)
    version_id: Mapped[str | None] = mapped_column(
        ForeignKey("strategy_versions.version_id"), nullable=True, index=True
    )
    exchange: Mapped[str] = mapped_column(String(20), default="binance")
    symbol_scope: Mapped[list[str]] = mapped_column(JSON, default=list)
    candidate_symbols: Mapped[list[str]] = mapped_column(JSON, default=list)
    selection_basis: Mapped[str] = mapped_column(String(80), default="binance_top20_quote_volume")
    run_window: Mapped[dict] = mapped_column(JSON, default=dict)
    execution_profile: Mapped[dict] = mapped_column(JSON, default=dict)
    gate_decision_ref: Mapped[str | None] = mapped_column(String(36), nullable=True)
    paper_metrics_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    paper_status: Mapped[str] = mapped_column(String(30), default="queued")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class OptimizationRun(Base):
    __tablename__ = "optimization_runs"

    optimization_run_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    strategy_id: Mapped[str] = mapped_column(ForeignKey("strategies.id"), index=True)
    version_id: Mapped[str | None] = mapped_column(
        ForeignKey("strategy_versions.version_id"), nullable=True, index=True
    )
    search_space_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    optimization_method: Mapped[str] = mapped_column(String(40), default="hyperopt")
    best_candidate_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    run_status: Mapped[str] = mapped_column(String(30), default="queued")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class RiskProfile(Base):
    __tablename__ = "risk_profiles"

    risk_profile_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    single_trade_risk_limit: Mapped[float] = mapped_column(Float, default=0.01)
    max_symbol_exposure: Mapped[float] = mapped_column(Float, default=0.10)
    max_total_exposure: Mapped[float] = mapped_column(Float, default=0.50)
    max_open_positions: Mapped[int] = mapped_column(default=3)
    max_leverage: Mapped[float] = mapped_column(Float, default=3.0)
    daily_loss_limit: Mapped[float] = mapped_column(Float, default=0.03)
    weekly_loss_limit: Mapped[float] = mapped_column(Float, default=0.08)
    drawdown_limit: Mapped[float] = mapped_column(Float, default=0.10)
    hard_stop_drawdown_limit: Mapped[float] = mapped_column(Float, default=0.20)
    consecutive_loss_limit: Mapped[int] = mapped_column(default=4)
    api_failure_limit: Mapped[int] = mapped_column(default=3)
    api_failure_window_minutes: Mapped[int] = mapped_column(default=10)
    market_scope: Mapped[str] = mapped_column(String(120), default="BTC/USDT perpetual")
    config_source: Mapped[str] = mapped_column(String(255), default="risk-control-and-safeguards-plan.md section 4")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class NotificationOutbox(Base):
    __tablename__ = "notification_outbox"

    notification_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(60), index=True)
    severity: Mapped[str] = mapped_column(String(20), index=True)
    channel_group: Mapped[str] = mapped_column(String(40), default="ops")
    delivery_channels: Mapped[list[str]] = mapped_column(JSON, default=list)
    subject: Mapped[str] = mapped_column(String(240))
    body: Mapped[str] = mapped_column(Text)
    source_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    delivery_status: Mapped[str] = mapped_column(String(40), default="pending_adapter", index=True)
    delivery_attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(nullable=True)
    attempt_history: Mapped[list] = mapped_column(JSON, default=list)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class ReviewReport(Base):
    __tablename__ = "review_reports"

    review_report_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    report_date: Mapped[str] = mapped_column(String(20))
    scope_type: Mapped[str] = mapped_column(String(40), default="daily")
    strategy_refs: Mapped[list[str]] = mapped_column(JSON, default=list)
    worst_performer_refs: Mapped[list[str]] = mapped_column(JSON, default=list)
    failure_patterns: Mapped[list[str]] = mapped_column(JSON, default=list)
    deviation_analysis: Mapped[list[str]] = mapped_column(JSON, default=list)
    recommendations: Mapped[list[str]] = mapped_column(JSON, default=list)
    report_status: Mapped[str] = mapped_column(String(30), default="draft")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class DecisionMemoryEntry(Base):
    __tablename__ = "decision_memory_entries"

    decision_memory_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    scope_type: Mapped[str] = mapped_column(String(40), index=True)
    scope_id: Mapped[str] = mapped_column(String(36), index=True)
    decision_type: Mapped[str] = mapped_column(String(60), index=True)
    verdict: Mapped[str] = mapped_column(String(30), index=True)
    summary: Mapped[str] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    evidence_refs: Mapped[list[str]] = mapped_column(JSON, default=list)
    context_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)


class FailureRecord(Base):
    __tablename__ = "failure_records"

    failure_record_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    strategy_id: Mapped[str | None] = mapped_column(ForeignKey("strategies.id"), nullable=True, index=True)
    idea_id: Mapped[str | None] = mapped_column(ForeignKey("strategy_ideas.idea_id"), nullable=True, index=True)
    version_id: Mapped[str | None] = mapped_column(
        ForeignKey("strategy_versions.version_id"), nullable=True, index=True
    )
    origin_run_type: Mapped[str] = mapped_column(String(40))
    origin_run_id: Mapped[str] = mapped_column(String(36))
    failure_type: Mapped[str] = mapped_column(String(60))
    failure_summary: Mapped[str] = mapped_column(Text)
    evidence_refs: Mapped[list[str]] = mapped_column(JSON, default=list)
    recommended_change: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class AgentTask(Base):
    __tablename__ = "agent_tasks"

    agent_task_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    agent_type: Mapped[str] = mapped_column(String(60), index=True)
    task_type: Mapped[str] = mapped_column(String(80))
    input_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    output_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    input_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    output_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    priority: Mapped[int] = mapped_column(default=5)
    task_status: Mapped[str] = mapped_column(String(30), default="queued")
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    executor_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    attempt_history: Mapped[list] = mapped_column(JSON, default=list)
    provider_trace: Mapped[dict] = mapped_column(JSON, default=dict)
    schema_validation_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class LiveRun(Base):
    __tablename__ = "live_runs"

    live_run_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    strategy_id: Mapped[str] = mapped_column(ForeignKey("strategies.id"), index=True)
    version_id: Mapped[str | None] = mapped_column(
        ForeignKey("strategy_versions.version_id"), nullable=True, index=True
    )
    exchange: Mapped[str] = mapped_column(String(20), default="binance")
    capital_tier: Mapped[str] = mapped_column(String(30), default="micro")
    live_status: Mapped[str] = mapped_column(String(30), default="queued")
    validation_backtest_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("backtest_runs.backtest_run_id"), nullable=True, index=True
    )
    risk_profile_ref: Mapped[str | None] = mapped_column(ForeignKey("risk_profiles.risk_profile_id"), nullable=True)
    live_metrics_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ExchangeAccountSnapshot(Base):
    __tablename__ = "exchange_account_snapshots"

    snapshot_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    live_run_id: Mapped[str] = mapped_column(ForeignKey("live_runs.live_run_id"), index=True)
    exchange: Mapped[str] = mapped_column(String(30))
    wallet_balance: Mapped[float] = mapped_column(Float)
    available_balance: Mapped[float] = mapped_column(Float)
    margin_balance: Mapped[float] = mapped_column(Float)
    unrealized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    open_position_count: Mapped[int] = mapped_column(Integer, default=0)
    source_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    snapshot_time: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)


class ReconciliationRecord(Base):
    __tablename__ = "reconciliation_records"

    reconciliation_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    live_run_id: Mapped[str] = mapped_column(ForeignKey("live_runs.live_run_id"), index=True)
    reconciliation_status: Mapped[str] = mapped_column(String(30), default="ok", index=True)
    open_order_count: Mapped[int] = mapped_column(Integer, default=0)
    position_mismatches: Mapped[list] = mapped_column(JSON, default=list)
    notes: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)


class SignalEnsemble(Base):
    __tablename__ = "signal_ensembles"

    ensemble_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    strategy_refs: Mapped[list[str]] = mapped_column(JSON, default=list)
    fusion_method: Mapped[str] = mapped_column(String(40), default="weighted_vote")
    correlation_matrix_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    raw_votes: Mapped[list] = mapped_column(JSON, default=list)
    fused_direction: Mapped[str | None] = mapped_column(String(20), nullable=True)
    fused_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    ensemble_status: Mapped[str] = mapped_column(String(40), default="formed")
    created_at: Mapped[datetime | None] = mapped_column(nullable=True)


class MetaLabel(Base):
    __tablename__ = "meta_labels"

    meta_label_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    ensemble_id: Mapped[str] = mapped_column(ForeignKey("signal_ensembles.ensemble_id"), index=True)
    triple_barrier_result: Mapped[str | None] = mapped_column(String(20), nullable=True)
    bet_decision: Mapped[str] = mapped_column(String(20), default="pending")
    position_size_fraction: Mapped[float | None] = mapped_column(Float, nullable=True)
    model_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    training_window_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)


class OrderExecution(Base):
    __tablename__ = "order_executions"

    order_execution_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    strategy_id: Mapped[str] = mapped_column(ForeignKey("strategies.id"), index=True)
    version_id: Mapped[str | None] = mapped_column(
        ForeignKey("strategy_versions.version_id"), nullable=True, index=True
    )
    symbol: Mapped[str] = mapped_column(String(30), index=True)
    direction: Mapped[str] = mapped_column(String(20))
    execution_status: Mapped[str] = mapped_column(String(30), default="queued")
    stoploss_present: Mapped[bool] = mapped_column(default=False)
    close_only_mode: Mapped[bool] = mapped_column(default=False)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    rejection_codes: Mapped[list] = mapped_column(JSON, default=list)
    entry_context: Mapped[dict] = mapped_column(JSON, default=dict)
    stoploss_plan: Mapped[dict] = mapped_column(JSON, default=dict)
    takeprofit_plan: Mapped[dict] = mapped_column(JSON, default=dict)
    risk_profile_ref: Mapped[str | None] = mapped_column(ForeignKey("risk_profiles.risk_profile_id"), nullable=True)
    validation_backtest_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("backtest_runs.backtest_run_id"), nullable=True
    )
    paper_run_id: Mapped[str | None] = mapped_column(ForeignKey("paper_runs.paper_run_id"), nullable=True)
    live_run_id: Mapped[str | None] = mapped_column(ForeignKey("live_runs.live_run_id"), nullable=True)
    signal_ensemble_id: Mapped[str | None] = mapped_column(ForeignKey("signal_ensembles.ensemble_id"), nullable=True)
    meta_label_id: Mapped[str | None] = mapped_column(ForeignKey("meta_labels.meta_label_id"), nullable=True)
    veto_result: Mapped[dict] = mapped_column(JSON, default=dict)
    evaluated_risk_state: Mapped[dict] = mapped_column(JSON, default=dict)
    gateway_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    gateway_order_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    gateway_status: Mapped[str | None] = mapped_column(String(30), nullable=True, index=True)
    lifecycle_history: Mapped[list] = mapped_column(JSON, default=list)
    reconciliation_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    last_gateway_update_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class PositionSnapshot(Base):
    __tablename__ = "position_snapshots"

    position_snapshot_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    run_type: Mapped[str] = mapped_column(String(20))
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    symbol: Mapped[str] = mapped_column(String(30), index=True)
    side: Mapped[str] = mapped_column(String(20))
    quantity: Mapped[float] = mapped_column(Float)
    entry_price: Mapped[float] = mapped_column(Float)
    mark_price: Mapped[float] = mapped_column(Float)
    unrealized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    snapshot_time: Mapped[datetime] = mapped_column(index=True)
    hedge_group_id: Mapped[str | None] = mapped_column(String(60), nullable=True, index=True)
    is_hedge_leg: Mapped[bool] = mapped_column(default=False)


class DecisionSnapshot(Base):
    __tablename__ = "decision_snapshots"

    decision_snapshot_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    paper_run_id: Mapped[str] = mapped_column(ForeignKey("paper_runs.paper_run_id"), index=True)
    symbol: Mapped[str] = mapped_column(String(30), index=True)
    action: Mapped[str] = mapped_column(String(60))
    pipeline_status: Mapped[str | None] = mapped_column(String(60), nullable=True, index=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    decision_trace: Mapped[dict] = mapped_column(JSON, default=dict)
    cycle_time: Mapped[datetime] = mapped_column(index=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class StrategyRoadmapState(Base):
    __tablename__ = "strategy_roadmap_states"

    item_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    audit_history: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
